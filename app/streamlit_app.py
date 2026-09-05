"""Interfaz Streamlit del agente de valoración DCF (Fase 6 del blueprint).

Esta capa NO calcula nada: orquesta llamadas a engine/ y ai/ y presenta
los resultados. Toda la lógica financiera vive en engine/, ya validada
y testeada por separado (ver docs/METHODOLOGY.md).

Ejecutar con:  ./.venv/Scripts/streamlit.exe run app/streamlit_app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from ai.memo_generator import build_memo_input, build_prompt, run_scenarios_capturing_warnings
from engine.data_provider import AlphaVantageClient
from engine.data_provider import historical_financials as av_historical_financials
from engine.data_provider import market_snapshot as av_market_snapshot
from engine.validation import build_peer_set
from engine.valuation import DCFInputs, sensitivity_matrix
from engine.wacc_builder import build_wacc
from engine.yfinance_provider import get_ticker as yf_get_ticker
from engine.yfinance_provider import historical_financials as yf_historical_financials
from engine.yfinance_provider import market_snapshot as yf_market_snapshot

# ---------------------------------------------------------------------------
# Universos con comparables ya validados en sesiones anteriores (ver estado.md)
# ---------------------------------------------------------------------------

CACHED_GROUPS = {
    "Big Tech / Cloud (Alpha Vantage)": ["AMZN", "MSFT", "GOOGL", "META", "AAPL"],
    "Consumo defensivo (yfinance)": ["KO", "PG", "JNJ"],
}
RISK_FREE_RATE = 0.03909
MARKET_RISK_PREMIUM = 0.0406


@st.cache_data(show_spinner="Cargando datos cacheados de Alpha Vantage...")
def load_av_universe(tickers: tuple) -> tuple[dict, dict]:
    client = AlphaVantageClient()
    hist = {t: av_historical_financials(client, t, use_cache=True) for t in tickers}
    snap = {t: av_market_snapshot(client, t, use_cache=True) for t in tickers}
    return hist, snap


@st.cache_data(show_spinner="Descargando datos de yfinance...")
def load_yf_universe(tickers: tuple) -> tuple[dict, dict]:
    hist, snap = {}, {}
    for t in tickers:
        ticker_obj = yf_get_ticker(t)
        hist[t] = yf_historical_financials(ticker_obj)
        snap[t] = yf_market_snapshot(ticker_obj)
    return hist, snap


def build_peer_wacc(target: str, hist_data: dict, snap_data: dict):
    """Reutiliza build_peer_set (ya testeado en tests/test_validation.py)
    en vez de reconstruir la lista de comparables aquí."""
    hist, snap = hist_data[target], snap_data[target]
    peers = build_peer_set(target, hist_data, snap_data)
    return build_wacc(
        peers=peers, target_tax_rate=float(hist["tax_rate"].dropna().iloc[-1]),
        target_net_debt=(snap.get("total_debt") or 0) - (snap.get("cash") or 0),
        target_market_cap=snap["market_cap"], risk_free_rate=RISK_FREE_RATE,
        market_risk_premium=MARKET_RISK_PREMIUM,
        target_interest_expense=float(hist["interest_expense"].dropna().iloc[-1]),
        target_total_debt=snap["total_debt"],
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Agente de Valoración DCF", layout="wide")
st.title("Agente de Valoración DCF")
st.caption(
    "Motor determinista (engine/) + capa generativa desacoplada (ai/). "
    "Herramienta educativa y de portfolio — no es asesoramiento de inversión regulado."
)

with st.sidebar:
    st.header("Configuración")
    mode = st.radio(
        "Fuente de datos",
        ["Universo cacheado (WACC riguroso vía comparables)", "Cualquier ticker (yfinance, WACC simplificado)"],
    )

    if mode.startswith("Universo"):
        group_name = st.selectbox("Grupo de comparables", list(CACHED_GROUPS.keys()))
        tickers = CACHED_GROUPS[group_name]
        target = st.selectbox("Ticker", tickers)
        loader = load_av_universe if "Alpha Vantage" in group_name else load_yf_universe
        hist_data, snap_data = loader(tuple(tickers))
        wacc_result = build_peer_wacc(target, hist_data, snap_data)
        wacc_value = wacc_result.wacc
        wacc_detail = wacc_result
    else:
        target = st.text_input("Símbolo (ej. NVDA, DIS, WMT)", value="NVDA").strip().upper()
        wacc_detail = None
        if target:
            ticker_obj = yf_get_ticker(target)
            hist = yf_historical_financials(ticker_obj)
            snap = yf_market_snapshot(ticker_obj)
            hist_data, snap_data = {target: hist}, {target: snap}
            from engine.valuation import cost_of_debt, cost_of_equity, wacc as wacc_fn
            re = cost_of_equity(RISK_FREE_RATE, snap["beta"], MARKET_RISK_PREMIUM)
            rd = cost_of_debt(hist["interest_expense"].dropna().iloc[-1] or 0, snap["total_debt"] or 1)
            wacc_value = wacc_fn(snap["market_cap"], snap["total_debt"] or 0, re, rd,
                                  float(hist["tax_rate"].dropna().iloc[-1]))
            st.caption("⚠️ WACC simplificado: beta propio, sin releverage por comparables.")

    st.divider()
    n_years = st.slider("Años de proyección", 3, 10, 5)
    terminal_growth_rate = st.slider("Tasa de crecimiento terminal (g)", 0.0, 0.05, 0.025, 0.0025, format="%.4f")
    lookback_years = st.slider("Años de histórico (lookback)", 2, 10, 3)
    gordon_weight = st.slider("Peso Gordon Growth en el valor terminal", 0.0, 1.0, 0.8, 0.1)

if not target:
    st.stop()

hist = hist_data[target]
snap = snap_data[target]

# --- Métricas clave ---------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)
col1.metric("Precio de mercado", f"${snap['price']:.2f}" if snap.get("price") else "n/d")
col2.metric("Consenso analistas", f"${snap['analyst_target_price']:.2f}" if snap.get("analyst_target_price") else "n/d")
col3.metric("WACC", f"{wacc_value*100:.2f}%")
col4.metric("Sector", snap.get("sector") or "n/d")

# --- Escenarios --------------------------------------------------------------

st.subheader("Rango de escenarios")
scenario_results, warnings_text = run_scenarios_capturing_warnings(
    hist, wacc=wacc_value, cash=snap.get("cash") or 0, total_debt=snap.get("total_debt") or 0,
    diluted_shares=snap["shares_outstanding"], n_years=n_years, terminal_growth_rate=terminal_growth_rate,
    lookback_years=lookback_years, terminal_ev_ebitda_multiple=snap.get("ev_to_ebitda"),
    gordon_weight=gordon_weight,
)

scenario_names = list(scenario_results.keys())
scenario_prices = [r.implied_share_price for r in scenario_results.values()]

fig, ax = plt.subplots(figsize=(8, 3.2))
bar_color = "#4C72B0"  # hue único, neutro — no es una categórica de identidad, es una magnitud ordenada
bars = ax.barh(scenario_names, scenario_prices, color=bar_color, height=0.5)
for bar, price in zip(bars, scenario_prices):
    ax.text(bar.get_width() + max(scenario_prices) * 0.01, bar.get_y() + bar.get_height() / 2,
            f"${price:,.2f}", va="center", fontsize=9)

if snap.get("price"):
    ax.axvline(snap["price"], color="#555555", linestyle="--", linewidth=1.5)
    ax.text(snap["price"], -0.7, f"Mercado ${snap['price']:.2f}", color="#555555",
            fontsize=8, ha="center", va="top")
if snap.get("analyst_target_price"):
    ax.axvline(snap["analyst_target_price"], color="#C44E52", linestyle=":", linewidth=1.5)
    ax.text(snap["analyst_target_price"], -0.7, f"Consenso ${snap['analyst_target_price']:.2f}",
            color="#C44E52", fontsize=8, ha="center", va="top")

ax.set_xlabel("Precio implícito ($)")
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout()
st.pyplot(fig)

if warnings_text:
    for w in warnings_text:
        st.warning(f"⚠️ Aviso técnico del modelo: {w}")
else:
    st.info("Sin avisos técnicos: el margen WACC-g es saludable en este cálculo.")

# --- Supuestos clave ----------------------------------------------------------

from engine.projections import default_assumptions_from_history

assumptions = default_assumptions_from_history(
    hist, n_years=n_years, lookback_years=lookback_years,
)
st.subheader("Supuestos de proyección (escenario conservador)")
assumptions_df = pd.DataFrame([
    {"Driver": "Crecimiento de ingresos", "Año 1": f"{assumptions.revenue_growth.start*100:.2f}%",
     "Año N": f"{assumptions.revenue_growth.end*100:.2f}%"},
    {"Driver": "Margen EBIT", "Año 1": f"{assumptions.ebit_margin.start*100:.2f}%",
     "Año N": f"{assumptions.ebit_margin.end*100:.2f}%"},
    {"Driver": "D&A (% ventas)", "Año 1": f"{assumptions.da_pct_revenue.start*100:.2f}%",
     "Año N": f"{assumptions.da_pct_revenue.end*100:.2f}%"},
    {"Driver": "CapEx (% ventas)", "Año 1": f"{assumptions.capex_pct_revenue.start*100:.2f}%",
     "Año N": f"{assumptions.capex_pct_revenue.end*100:.2f}%"},
    {"Driver": "Δ NWC (% ventas)", "Año 1": f"{assumptions.nwc_change_pct_revenue.start*100:.2f}%",
     "Año N": f"{assumptions.nwc_change_pct_revenue.end*100:.2f}%"},
])
st.dataframe(assumptions_df, hide_index=True, use_container_width=True)

# --- Matriz de sensibilidad ---------------------------------------------------

st.subheader("Sensibilidad: WACC × tasa de crecimiento terminal")
conservative_name = "Conservador (reversión a la media)"
base_result = scenario_results[conservative_name]

from engine.projections import project_financials

projection = project_financials(hist["revenue"].iloc[-1], assumptions)
base_inputs = DCFInputs(
    ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
    capex=projection.capex, change_in_nwc=projection.change_in_nwc, wacc=wacc_value,
    terminal_growth_rate=terminal_growth_rate, cash=snap.get("cash") or 0, total_debt=snap.get("total_debt") or 0,
    diluted_shares=snap["shares_outstanding"], terminal_ev_ebitda_multiple=snap.get("ev_to_ebitda"),
    gordon_weight=gordon_weight,
)

wacc_range = [wacc_value + delta for delta in (-0.01, -0.005, 0.0, 0.005, 0.01)]
growth_range = [max(terminal_growth_rate + delta, 0.0) for delta in (-0.01, -0.005, 0.0, 0.005, 0.01)]
wacc_range = [w for w in wacc_range if w > max(growth_range)]

if len(wacc_range) >= 2:
    matrix = sensitivity_matrix(base_inputs, wacc_values=wacc_range, growth_values=growth_range)
    matrix_df = pd.DataFrame(
        matrix.implied_share_price,
        index=[f"{w*100:.2f}%" for w in matrix.wacc_values],
        columns=[f"{g*100:.2f}%" for g in matrix.growth_values],
    )
    matrix_df.index.name = "WACC"
    matrix_df.columns.name = "g terminal"
    st.dataframe(
        matrix_df.style.format("${:,.2f}").background_gradient(cmap="Blues", axis=None),
        use_container_width=True,
    )
else:
    st.caption("Rango de WACC insuficiente para la matriz con la g elegida (sube el WACC o baja g).")

# --- Memo (sin API todavía) ---------------------------------------------------

st.subheader("Investment Memo")
key_assumptions = {
    "ebit_margin_start": assumptions.ebit_margin.start, "ebit_margin_end": assumptions.ebit_margin.end,
    "capex_pct_start": assumptions.capex_pct_revenue.start, "capex_pct_end": assumptions.capex_pct_revenue.end,
    "revenue_growth_start": assumptions.revenue_growth.start, "revenue_growth_end": assumptions.revenue_growth.end,
}
memo_input = build_memo_input(
    ticker=target, wacc=wacc_value, terminal_growth_rate=terminal_growth_rate,
    scenario_results=scenario_results, key_assumptions=key_assumptions,
    market_price=snap.get("price"), analyst_target_price=snap.get("analyst_target_price"),
    warnings_raised=warnings_text,
)

import os

if os.environ.get("ANTHROPIC_API_KEY"):
    if st.button("Generar memo con la API de Anthropic"):
        from ai.memo_generator import generate_memo
        with st.spinner("Redactando memo..."):
            memo_text = generate_memo(memo_input)
        st.markdown(memo_text)
else:
    st.info(
        "No hay ANTHROPIC_API_KEY configurada. Copia este prompt y pégalo en Claude.ai "
        "para generar el memo manualmente sin coste (ver estado.md)."
    )
    _, user_prompt = build_prompt(memo_input)
    st.code(user_prompt, language="json")
