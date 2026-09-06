"""Interfaz Streamlit del agente de valoración DCF (Fase 6 del blueprint).

Esta capa NO calcula nada: orquesta llamadas a engine/ y ai/ y presenta
los resultados. Toda la lógica financiera vive en engine/, ya validada
y testeada por separado (ver docs/METHODOLOGY.md).

Ejecutar con:  ./.venv/Scripts/streamlit.exe run app/streamlit_app.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ai.memo_generator import build_memo_input, build_prompt, run_scenarios_capturing_warnings
from engine.data_provider import AlphaVantageClient, AlphaVantageError
from engine.data_provider import historical_financials as av_historical_financials
from engine.data_provider import market_snapshot as av_market_snapshot
from engine.validation import build_peer_set
from engine.valuation import DCFInputs, sensitivity_matrix
from engine.wacc_builder import build_wacc
from engine.yfinance_provider import get_ticker as yf_get_ticker
from engine.yfinance_provider import historical_financials as yf_historical_financials
from engine.yfinance_provider import market_snapshot as yf_market_snapshot
from engine.yfinance_provider import treasury_yield_10y as yf_treasury_yield_10y

# ---------------------------------------------------------------------------
# Diseño: tokens de color/tipografía (sesión 16, pase de UX/UI)
# ---------------------------------------------------------------------------
#
# Fuente única para el color en toda la app -- CSS inyectado y gráficos
# Plotly leen de aquí, para no tener la paleta duplicada en dos sitios
# que puedan desincronizarse. Paleta "quant/banca de inversión": un
# único azul marino como acento, grises fríos neutros, semántica
# separada del acento (verde=crea valor, ámbar=aviso, rojo=negativo) --
# igual que ya se hace en docs/AUDIT.md y docs/PROGRESS_REVIEW.md.
COLORS = {
    "ink": "#10151C", "ink_soft": "#4B5768", "ink_faint": "#8993A4",
    "border": "#E1E4EA", "surface": "#FFFFFF", "bg": "#F6F7F9",
    "accent": "#1B3A5C", "accent_soft": "#E8EEF4",
    "series": "#2F6690",  # magnitud ordenada (escenarios, mapa de calor) -- no es una categórica de identidad
    "good": "#1F7A5C", "warn": "#A66A1B", "bad": "#B23B36",
    "market_ref": "#6B7280", "consensus_ref": "#A66A1B",
}
FONT_SANS = "IBM Plex Sans, -apple-system, Segoe UI, sans-serif"
FONT_MONO = "IBM Plex Mono, SFMono-Regular, Consolas, monospace"


def _inject_custom_css() -> None:
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

    html, body, [class*="css"] {{ font-family: {FONT_SANS} !important; }}
    h1, h2, h3, [data-testid="stMarkdownContainer"] h1,
    [data-testid="stMarkdownContainer"] h2, [data-testid="stMarkdownContainer"] h3 {{
        font-family: {FONT_SANS} !important; font-weight: 600 !important;
        letter-spacing: -0.01em; color: {COLORS['ink']};
    }}
    [data-testid="stMetricValue"] {{
        font-family: {FONT_MONO} !important; font-weight: 600; color: {COLORS['ink']};
        font-variant-numeric: tabular-nums; font-size: 1.65rem !important;
        white-space: normal !important; overflow: visible !important;
        line-height: 1.3 !important; word-break: break-word;
    }}
    [data-testid="stMetric"] {{ overflow: visible !important; }}
    [data-testid="stMetricLabel"] {{
        font-family: {FONT_SANS} !important; font-size: 0.78rem; color: {COLORS['ink_soft']};
        text-transform: uppercase; letter-spacing: 0.04em; font-weight: 500;
    }}
    [data-testid="stMetricDelta"] {{ font-family: {FONT_SANS} !important; font-weight: 500; }}
    code, [data-testid="stCodeBlock"], .stDataFrame {{ font-family: {FONT_MONO} !important; }}
    [data-testid="stSidebar"] {{ background-color: {COLORS['bg']}; border-right: 1px solid {COLORS['border']}; }}
    [data-testid="stMetric"] {{
        background: {COLORS['surface']}; border: 1px solid {COLORS['border']};
        border-radius: 10px; padding: 14px 16px 10px;
    }}
    .stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
    .stTabs [data-baseweb="tab"] {{
        font-family: {FONT_SANS}; font-weight: 500; font-size: 0.92rem;
    }}
    .app-eyebrow {{
        font-family: {FONT_MONO}; font-size: 0.75rem; letter-spacing: 0.08em;
        text-transform: uppercase; color: {COLORS['accent']}; margin-bottom: 2px;
    }}
    .app-subtitle {{ color: {COLORS['ink_soft']}; font-size: 0.95rem; margin-top: -6px; }}
    </style>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Universos con comparables ya validados en sesiones anteriores (ver estado.md)
# ---------------------------------------------------------------------------

CACHED_GROUPS = {
    "Big Tech / Cloud (Alpha Vantage)": ["AMZN", "MSFT", "GOOGL", "META", "AAPL"],
    "Consumo defensivo (yfinance)": ["KO", "PG", "JNJ"],
}

# Auditoría sesión 15, hallazgo I1: estos dos valores eran constantes
# congeladas de cuando se construyó el Excel de referencia (~nov-2024) y
# se usaban en todas las valoraciones sin importar cuándo se ejecutaran.
# El risk-free rate SÍ tiene una fuente en vivo estándar (Treasury 10Y) y
# ahora se consulta siempre por esa vía -- FALLBACK_RISK_FREE_RATE solo se
# usa si la consulta en vivo falla (sin red, símbolo no disponible), y la
# interfaz avisa explícitamente cuando eso ocurre. La prima de riesgo de
# mercado (ERP) no tiene un equivalente: no existe una API gratuita fiable
# que la sirva en vivo (el estándar del sector, Damodaran, se publica a
# mano de forma periódica) -- por eso se deja como un slider ajustable en
# vez de fingir que también es un dato en vivo.
FALLBACK_RISK_FREE_RATE = 0.03909
DEFAULT_MARKET_RISK_PREMIUM = 0.0406


@st.cache_data(ttl=3600, show_spinner="Consultando risk-free rate en vivo (Treasury 10Y)...")
def get_live_risk_free_rate() -> tuple[float, str]:
    """Devuelve (tasa, descripción de la fuente). Siempre vía yfinance
    (^TNX), incluso en modo "universo cacheado con Alpha Vantage": es un
    dato de mercado ambiental, igual para cualquier compañía, y así no
    consume la cuota de 25 peticiones/día de Alpha Vantage por algo que
    no depende del ticker. engine.data_provider.AlphaVantageClient.
    treasury_yield() existe como alternativa y está testeado, pero no se
    usa aquí por ese motivo de cuota."""
    try:
        rate = yf_treasury_yield_10y(yf_get_ticker("^TNX"))
        return rate, "Treasury 10Y (^TNX, Yahoo Finance, en vivo)"
    except Exception as e:
        return (
            FALLBACK_RISK_FREE_RATE,
            f"⚠️ Fallback congelado (~nov-2024) -- la consulta en vivo falló: {e}",
        )


# Sesión 16 (continuación), "rigor técnico restante": sin ttl, el caché de
# Streamlit (distinto del caché en disco de 24h de AlphaVantageClient, una
# capa por debajo) vivía tanto como el propio proceso -- en Streamlit Cloud
# eso son potencialmente días sin reiniciar. Una herramienta que presume de
# "risk-free rate en vivo" (get_live_risk_free_rate, arriba, sí con ttl=3600)
# no debería tener el precio de mercado y el consenso de analistas congelados
# por accidente durante ese tiempo. 3600s (1h) por consistencia con esa misma
# función -- no agota la cuota de Alpha Vantage porque su propio caché en
# disco (24h) sigue absorbiendo la mayoría de las re-peticiones.
UNIVERSE_CACHE_TTL_SECONDS = 3600


@st.cache_data(ttl=UNIVERSE_CACHE_TTL_SECONDS, show_spinner="Cargando datos cacheados de Alpha Vantage...")
def load_av_universe(tickers: tuple) -> tuple[dict, dict]:
    client = AlphaVantageClient()
    hist = {t: av_historical_financials(client, t, use_cache=True) for t in tickers}
    snap = {t: av_market_snapshot(client, t, use_cache=True) for t in tickers}
    return hist, snap


@st.cache_data(ttl=UNIVERSE_CACHE_TTL_SECONDS, show_spinner="Descargando datos de yfinance...")
def load_yf_universe(tickers: tuple) -> tuple[dict, dict]:
    hist, snap = {}, {}
    for t in tickers:
        ticker_obj = yf_get_ticker(t)
        hist[t] = yf_historical_financials(ticker_obj)
        snap[t] = yf_market_snapshot(ticker_obj)
    return hist, snap


def build_peer_wacc(target: str, hist_data: dict, snap_data: dict,
                     risk_free_rate: float, market_risk_premium: float):
    """Reutiliza build_peer_set (ya testeado en tests/test_validation.py)
    en vez de reconstruir la lista de comparables aquí."""
    hist, snap = hist_data[target], snap_data[target]
    peers = build_peer_set(target, hist_data, snap_data)
    return build_wacc(
        peers=peers, target_tax_rate=float(hist["tax_rate"].dropna().iloc[-1]),
        target_net_debt=(snap.get("total_debt") or 0) - (snap.get("cash") or 0),
        target_market_cap=snap["market_cap"], risk_free_rate=risk_free_rate,
        market_risk_premium=market_risk_premium,
        target_interest_expense=float(hist["interest_expense"].dropna().iloc[-1]),
        target_total_debt=snap["total_debt"],
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Agente de Valoración DCF", page_icon="📊", layout="wide")
_inject_custom_css()

st.markdown('<div class="app-eyebrow">Motor determinista · Reverse DCF · IA desacoplada del cálculo</div>',
            unsafe_allow_html=True)
st.title("Agente de Valoración DCF")
st.markdown(
    '<p class="app-subtitle">Herramienta educativa y de portfolio — no es asesoramiento de inversión regulado.</p>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Configuración")

    st.subheader("Parámetros de mercado (CAPM)")
    risk_free_rate, rf_source = get_live_risk_free_rate()
    st.metric("Risk-free rate (Treasury 10Y)", f"{risk_free_rate*100:.3f}%")
    st.caption(f"Fuente: {rf_source}")
    market_risk_premium = st.slider(
        "Prima de riesgo de mercado (ERP)", 0.02, 0.08, DEFAULT_MARKET_RISK_PREMIUM, 0.001,
        format="%.3f",
        help="Sin fuente gratuita en vivo fiable (auditoría sesión 15, hallazgo I1) — "
             "el valor por defecto es el heredado del Excel de referencia (~nov-2024). "
             "Ajusta a mano si tienes una estimación más reciente (p. ej. Damodaran).",
    )
    st.divider()

    mode = st.radio(
        "Fuente de datos",
        ["Universo cacheado (WACC riguroso vía comparables)", "Cualquier ticker (yfinance, WACC simplificado)"],
    )

    if mode.startswith("Universo"):
        group_name = st.selectbox("Grupo de comparables", list(CACHED_GROUPS.keys()))
        tickers = CACHED_GROUPS[group_name]
        target = st.selectbox("Ticker", tickers)
        loader = load_av_universe if "Alpha Vantage" in group_name else load_yf_universe
        # A diferencia del modo "cualquier ticker" (I3, auditoría sesión 15),
        # este camino -- el que usa cualquier visitante por defecto -- no tenía
        # manejo de errores: un fallo de Alpha Vantage (cuota de 25 peticiones/día
        # agotada, COMPARTIDA entre todos los visitantes de esta app ya pública) o
        # de red se propagaba como un traceback crudo de Streamlit en vez de un
        # mensaje accionable. Mismo principio que I3: límite del sistema (API
        # externa que no controlamos), no un error interno -- excepción amplia
        # deliberada, con un mensaje específico para el caso de cuota agotada.
        try:
            hist_data, snap_data = loader(tuple(tickers))
            wacc_result = build_peer_wacc(target, hist_data, snap_data, risk_free_rate, market_risk_premium)
        except AlphaVantageError as e:
            st.error(
                f"Alpha Vantage no pudo responder para el grupo '{group_name}': {e}\n\n"
                "El free tier limita a 25 peticiones/día, compartidas entre todos los "
                "visitantes de esta app — puede que la cuota esté agotada por hoy. Prueba "
                "con 'Consumo defensivo (yfinance)' o con 'Cualquier ticker', que no "
                "dependen de Alpha Vantage."
            )
            st.stop()
        except Exception as e:
            st.error(
                f"No se pudo cargar el grupo '{group_name}': {e}\n\n"
                "Prueba con otro grupo de comparables o con 'Cualquier ticker'."
            )
            st.stop()
        wacc_value = wacc_result.wacc
        wacc_detail = wacc_result
    else:
        target = st.text_input("Símbolo (ej. NVDA, DIS, WMT)", value="NVDA").strip().upper()
        wacc_detail = None
        if target:
            # Auditoría sesión 15, hallazgo I3: este bloque procesa un ticker
            # arbitrario tecleado por el usuario contra una API externa (yfinance)
            # que no controlamos -- es un límite del sistema (entrada de usuario +
            # datos de terceros), no una llamada interna con datos ya validados.
            # yfinance puede devolver estados financieros vacíos, sin beta, sin
            # gasto financiero o sin tipo impositivo (frecuente en small caps e
            # IPOs recientes, que solo traen ~4 años de historia) -- cualquiera
            # de esos huecos hacía crashear la app (IndexError/TypeError/KeyError
            # sin capturar) antes de este fix. Excepción amplia deliberada: no se
            # puede enumerar de antemano cada fallo posible de una API externa
            # ante una entrada arbitraria.
            try:
                ticker_obj = yf_get_ticker(target)
                hist = yf_historical_financials(ticker_obj)
                snap = yf_market_snapshot(ticker_obj)
                if hist.empty:
                    raise ValueError(
                        f"yfinance no devolvió estados financieros para '{target}' "
                        "— comprueba que el símbolo es correcto."
                    )

                beta = snap.get("beta")
                if beta is None:
                    raise ValueError(
                        f"yfinance no reporta beta para '{target}' (frecuente en small "
                        "caps o IPOs recientes) — no se puede construir el WACC simplificado."
                    )

                # Auditoría sesión 15/16, hallazgo M5: comprobar la divisa ANTES
                # de calcular el WACC, no después -- si se calculara primero, la
                # UI mostraría brevemente un WACC "válido" (y su caption de
                # advertencia habitual) para acto seguido bloquear, una secuencia
                # confusa que sugiere que el número llegó a ser utilizable.
                reported_currency = snap.get("currency")
                if reported_currency and reported_currency != "USD":
                    raise ValueError(
                        f"'{target}' reporta sus estados financieros en {reported_currency}, no en "
                        "USD -- el risk-free rate y la prima de riesgo están calibrados en USD, "
                        "mezclarlos sería un error de escala completo. Sin soporte de conversión "
                        "de divisa todavía."
                    )

                interest_expense_series = hist["interest_expense"].dropna()
                if interest_expense_series.empty:
                    raise ValueError(f"Sin dato de gasto financiero disponible para '{target}'.")

                tax_rate_series = hist["tax_rate"].dropna()
                if tax_rate_series.empty:
                    raise ValueError(f"Sin tipo impositivo disponible para '{target}'.")

                hist_data, snap_data = {target: hist}, {target: snap}
                from engine.valuation import cost_of_debt, cost_of_equity, wacc as wacc_fn
                re = cost_of_equity(risk_free_rate, beta, market_risk_premium)
                rd = cost_of_debt(interest_expense_series.iloc[-1] or 0, snap.get("total_debt") or 1)
                wacc_value = wacc_fn(snap.get("market_cap") or 0, snap.get("total_debt") or 0, re, rd,
                                      float(tax_rate_series.iloc[-1]))
                st.caption("⚠️ WACC simplificado: beta propio, sin releverage por comparables.")
            except Exception as e:
                st.error(
                    f"No se pudo construir la valoración de '{target}': {e}\n\n"
                    "Prueba con otro ticker (los del universo cacheado siempre funcionan) "
                    "o revisa que el símbolo existe en yfinance."
                )
                st.stop()

    st.divider()
    n_years = st.slider("Años de proyección", 3, 10, 5)
    terminal_growth_rate = st.slider("Tasa de crecimiento terminal (g)", 0.0, 0.05, 0.025, 0.0025, format="%.4f")
    lookback_years = st.slider("Años de histórico (lookback)", 2, 10, 3)
    gordon_weight = st.slider("Peso Gordon Growth en el valor terminal", 0.0, 1.0, 0.8, 0.1)
    valuation_date = st.date_input(
        "Fecha de valoración",
        value=date.today(),
        help="Determina el stub period del primer año proyectado (auditoría sesión 15, "
             "hallazgo C1) — antes se asumía siempre el 1 de enero, infravalorando el "
             "resultado cuanto más avanzado estuviera el año real.",
    )

if not target:
    st.stop()

hist = hist_data[target]
snap = snap_data[target]

# --- Verificación de divisa de reporte (auditoría sesión 15/16, hallazgo M5) --
#
# risk_free_rate y market_risk_premium están calibrados en USD (Treasury
# americano). Si los estados financieros del ticker vienen en otra divisa,
# mezclarlos con esos inputs sin convertir sería un error de escala completo
# (no un sesgo pequeño) -- el WACC y el precio implícito saldrían mal por un
# factor arbitrario, sin ningún aviso visible. Se bloquea explícitamente en
# vez de dejar pasar un número silenciosamente incorrecto; `currency=None`
# (dato no reportado por el proveedor) NO bloquea, porque no hay evidencia de
# que sea un problema -- solo un valor explícito distinto de USD lo hace.
reported_currency = snap.get("currency")
if reported_currency and reported_currency != "USD":
    st.error(
        f"'{target}' reporta sus estados financieros en **{reported_currency}**, no en USD. "
        "El risk-free rate y la prima de riesgo de mercado de esta herramienta están calibrados "
        "en USD (Treasury americano) — valorarlo tal cual mezclaría cifras de dos divisas "
        "distintas, un error de escala completo, no un matiz. Esta herramienta no soporta "
        "todavía conversión de divisa; prueba con un ticker que reporte en USD."
    )
    st.stop()

# --- Múltiplo de salida: mediana de comparables, no el propio de la empresa ---

from engine.comps import build_comps_table, peer_average_multiple

comps_table = None
if len(snap_data) > 1:
    comps_table = build_comps_table(list(snap_data.values()))
    terminal_multiple = peer_average_multiple(comps_table, "ev_to_ebitda", exclude_symbol=target, method="median")
else:
    terminal_multiple = snap.get("ev_to_ebitda")

# --- Stub period real (auditoría sesión 15, hallazgo C1) ----------------------

from engine.projections import stub_fraction_from_history

stub_fraction = stub_fraction_from_history(hist, valuation_date=valuation_date)

# --- Métricas clave ---------------------------------------------------------

st.markdown(f"### {target}")
with st.container(border=True):
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Precio de mercado", f"${snap['price']:.2f}" if snap.get("price") else "n/d")
    col2.metric("Consenso analistas", f"${snap['analyst_target_price']:.2f}" if snap.get("analyst_target_price") else "n/d")
    col3.metric("WACC", f"{wacc_value*100:.2f}%",
                help="Coste medio ponderado de capital, vía CAPM con beta de comparables (universo cacheado) o beta propia (cualquier ticker).")
    col4.metric("Sector", snap.get("sector") or "n/d")
    st.caption(
        f"Stub period del primer año proyectado: **{stub_fraction:.3f}** "
        f"(fracción del ejercicio fiscal que queda desde {valuation_date.isoformat()} hasta su cierre). "
        "Acciones diluidas: recuento básico reportado por el proveedor de datos, no vía Treasury "
        "Stock Method (construido y validado en `engine/valuation.py`, pero sin fuente gratuita de "
        "tramos de opciones outstanding para conectarlo — ver hallazgo I2, `docs/AUDIT.md`)."
    )

# --- Cómputo (sin renderizar todavía) ------------------------------------------
#
# Todo el cálculo se hace aquí, de un tirón; el renderizado (más abajo, dentro
# de las pestañas) solo lee estos resultados ya calculados -- separar cómputo
# de presentación es lo que permite reorganizar la UI en pestañas sin tocar
# ninguna lógica financiera.

scenario_results, warnings_text = run_scenarios_capturing_warnings(
    hist, wacc=wacc_value, cash=snap.get("cash") or 0, total_debt=snap.get("total_debt") or 0,
    diluted_shares=snap["shares_outstanding"], n_years=n_years, terminal_growth_rate=terminal_growth_rate,
    lookback_years=lookback_years, terminal_ev_ebitda_multiple=terminal_multiple,
    gordon_weight=gordon_weight, valuation_date=valuation_date,
)
scenario_names = list(scenario_results.keys())
scenario_prices = [r.implied_share_price for r in scenario_results.values()]

from engine.projections import default_assumptions_from_history, project_financials
from engine.reverse_dcf import compute_implied_expectations

assumptions = default_assumptions_from_history(hist, n_years=n_years, lookback_years=lookback_years)
projection = project_financials(hist["revenue"].iloc[-1], assumptions)
base_inputs = DCFInputs(
    ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
    capex=projection.capex, change_in_nwc=projection.change_in_nwc, wacc=wacc_value,
    terminal_growth_rate=terminal_growth_rate, stub_fraction=stub_fraction,
    cash=snap.get("cash") or 0, total_debt=snap.get("total_debt") or 0,
    diluted_shares=snap["shares_outstanding"], terminal_ev_ebitda_multiple=terminal_multiple,
    gordon_weight=gordon_weight,
)
reverse_dcf_kwargs = dict(
    wacc=wacc_value, terminal_growth_rate=terminal_growth_rate, stub_fraction=stub_fraction,
    cash=snap.get("cash") or 0, total_debt=snap.get("total_debt") or 0,
    diluted_shares=snap["shares_outstanding"], terminal_ev_ebitda_multiple=terminal_multiple,
    gordon_weight=gordon_weight,
)
implied_expectations = compute_implied_expectations(
    hist["revenue"].iloc[-1], assumptions, base_inputs, reverse_dcf_kwargs,
    targets=[("Mercado", snap.get("price")), ("Consenso analistas", snap.get("analyst_target_price"))],
)

from engine.ratios import latest_ratio_snapshot

try:
    ratio_snapshot = latest_ratio_snapshot(hist, wacc_value)
    ratio_error = None
except ValueError as e:
    ratio_snapshot = None
    ratio_error = str(e)

wacc_range = [wacc_value + delta for delta in (-0.01, -0.005, 0.0, 0.005, 0.01)]
growth_range = [max(terminal_growth_rate + delta, 0.0) for delta in (-0.01, -0.005, 0.0, 0.005, 0.01)]
wacc_range = [w for w in wacc_range if w > max(growth_range)]
matrix = sensitivity_matrix(base_inputs, wacc_values=wacc_range, growth_values=growth_range) if len(wacc_range) >= 2 else None

key_assumptions = {
    "ebit_margin_start": assumptions.ebit_margin.start, "ebit_margin_end": assumptions.ebit_margin.end,
    "capex_pct_start": assumptions.capex_pct_revenue.start, "capex_pct_end": assumptions.capex_pct_revenue.end,
    "revenue_growth_start": assumptions.revenue_growth.start, "revenue_growth_end": assumptions.revenue_growth.end,
}
memo_input = build_memo_input(
    ticker=target, wacc=wacc_value, terminal_growth_rate=terminal_growth_rate,
    scenario_results=scenario_results, key_assumptions=key_assumptions,
    market_price=snap.get("price"), analyst_target_price=snap.get("analyst_target_price"),
    warnings_raised=warnings_text, ratios=ratio_snapshot,
    implied_expectations=implied_expectations,
)

# --- Presentación, organizada en pestañas ---------------------------------------

tab_valoracion, tab_supuestos, tab_fundamentales, tab_memo = st.tabs(
    ["Valoración", "Supuestos y expectativas", "Fundamentales", "Memo"]
)

with tab_valoracion:
    st.subheader("Rango de escenarios")

    fig = go.Figure()
    fig.add_bar(
        y=scenario_names, x=scenario_prices, orientation="h", width=0.5,
        marker_color=COLORS["series"], marker_line_width=0,
        text=[f"${p:,.2f}" for p in scenario_prices], textposition="outside",
        textfont=dict(family=FONT_MONO, size=13, color=COLORS["ink"]),
        hovertemplate="%{y}<br>$%{x:,.2f}<extra></extra>",
    )
    reference_prices = [v for v in (snap.get("price"), snap.get("analyst_target_price")) if v]
    max_x = max(scenario_prices + reference_prices) * 1.18
    if snap.get("price"):
        fig.add_vline(x=snap["price"], line_dash="dash", line_width=1.5, line_color=COLORS["market_ref"],
                      annotation_text=f"Mercado ${snap['price']:.2f}", annotation_position="top",
                      annotation_font=dict(family=FONT_SANS, size=11, color=COLORS["market_ref"]))
    if snap.get("analyst_target_price"):
        fig.add_vline(x=snap["analyst_target_price"], line_dash="dot", line_width=1.5, line_color=COLORS["consensus_ref"],
                      annotation_text=f"Consenso ${snap['analyst_target_price']:.2f}", annotation_position="bottom",
                      annotation_font=dict(family=FONT_SANS, size=11, color=COLORS["consensus_ref"]))
    fig.update_layout(
        height=280, margin=dict(l=10, r=10, t=50, b=40), showlegend=False,
        plot_bgcolor=COLORS["surface"], paper_bgcolor=COLORS["surface"],
        font=dict(family=FONT_SANS, color=COLORS["ink_soft"], size=13),
        xaxis=dict(title="Precio implícito ($)", range=[0, max_x], gridcolor=COLORS["border"], zeroline=False),
        yaxis=dict(gridcolor=COLORS["border"], automargin=True),
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    if warnings_text:
        for w in warnings_text:
            st.warning(f"Aviso técnico del modelo: {w}")
    else:
        st.info("Sin avisos técnicos: el margen WACC-g es saludable en este cálculo.")

    st.subheader("Sensibilidad: WACC × tasa de crecimiento terminal")
    if matrix is not None:
        z = matrix.implied_share_price
        x_labels = [f"{g*100:.2f}%" for g in matrix.growth_values]
        y_labels = [f"{w*100:.2f}%" for w in matrix.wacc_values]
        fig_matrix = go.Figure(data=go.Heatmap(
            z=z, x=x_labels, y=y_labels,
            colorscale=[[0, COLORS["accent_soft"]], [1, COLORS["accent"]]],
            text=[[f"${v:,.0f}" for v in row] for row in z],
            texttemplate="%{text}", textfont=dict(family=FONT_MONO, size=12, color=COLORS["ink"]),
            hovertemplate="WACC %{y} · g %{x}<br>$%{z:,.2f}<extra></extra>",
            colorbar=dict(title=dict(text="$", font=dict(family=FONT_SANS)), tickfont=dict(family=FONT_MONO)),
            xgap=2, ygap=2,
        ))
        fig_matrix.update_layout(
            height=280, margin=dict(l=10, r=10, t=10, b=10),
            plot_bgcolor=COLORS["surface"], paper_bgcolor=COLORS["surface"],
            font=dict(family=FONT_SANS, color=COLORS["ink_soft"], size=13),
            xaxis=dict(title="g terminal", side="bottom"),
            yaxis=dict(title="WACC", autorange="reversed"),
        )
        st.plotly_chart(fig_matrix, width="stretch", config={"displayModeBar": False})
    else:
        st.caption("Rango de WACC insuficiente para la matriz con la g elegida (sube el WACC o baja g).")

with tab_supuestos:
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
    st.dataframe(assumptions_df, hide_index=True, width="stretch")

    st.subheader("Expectativas implícitas del mercado (reverse DCF)")
    st.caption(
        "¿Qué tendría que ser cierto para justificar el precio que ya cotiza el mercado o el "
        "consenso? No es un error del modelo — es la prima de crecimiento que se está pagando hoy, "
        "cuantificada en vez de solo mostrada como un porcentaje de desviación."
    )
    if implied_expectations:
        implied_rows = []
        for exp in implied_expectations:
            growth_cell = (f"{exp.implied_revenue_growth*100:.1f}%" if exp.implied_revenue_growth is not None
                           else "fuera de rango (-30%/+60%)")
            gap_cell = f"{exp.revenue_growth_gap*100:+.1f} pp" if exp.revenue_growth_gap is not None else "n/d"
            if exp.implied_terminal_growth is not None:
                flag = " ⚠️" if exp.terminal_growth_fragile else ""
                g_cell = f"{exp.implied_terminal_growth*100:.2f}%{flag}"
            else:
                g_cell = "fuera de rango"
            implied_rows.append({
                "Precio objetivo": f"{exp.target_label} (${exp.target_price:,.2f})",
                "Crecimiento de ingresos implícito": growth_cell,
                "Gap vs. asumido": gap_cell,
                "g terminal implícita": g_cell,
            })
        st.dataframe(pd.DataFrame(implied_rows), hide_index=True, width="stretch")
        st.caption(
            f"Crecimiento de ingresos asumido (escenario conservador, CAGR reciente): "
            f"**{assumptions.revenue_growth.start*100:.1f}%**. Tasa de crecimiento terminal asumida: "
            f"**{terminal_growth_rate*100:.2f}%**. ⚠️ junto a la g terminal implícita indica que ese "
            "valor cae en la zona de spread WACC-g estrecho (inestable, ver aviso técnico del modelo)."
        )
    else:
        st.caption("Sin precio de mercado ni consenso disponible para calcular expectativas implícitas.")

with tab_fundamentales:
    st.subheader("Ratios financieros (último ejercicio disponible)")
    if ratio_snapshot is not None:
        ratio_cols = st.columns(5)
        ratio_cols[0].metric("ROE", f"{ratio_snapshot.roe*100:.1f}%")
        ratio_cols[1].metric(
            "ROIC vs. WACC", f"{ratio_snapshot.roic*100:.1f}%",
            "Crea valor" if ratio_snapshot.creates_value else "No crea valor",
            # st.metric no puede inferir el signo de un delta de texto libre --
            # sin esto, "No crea valor" se pintaría en verde igual que "Crea
            # valor" (Streamlit lo trata como positivo por defecto al no
            # llevar un "-" delante), lo cual sería activamente engañoso.
            delta_color="normal" if ratio_snapshot.creates_value else "inverse",
        )
        ratio_cols[2].metric("Debt/EBITDA", f"{ratio_snapshot.debt_to_ebitda:.2f}x")
        ratio_cols[3].metric("Cobertura de intereses",
                              f"{ratio_snapshot.interest_coverage:.1f}x" if ratio_snapshot.interest_coverage != float("inf") else "∞")
        ratio_cols[4].metric("Current ratio", f"{ratio_snapshot.current_ratio:.2f}")
        st.caption(f"Ejercicio fiscal {ratio_snapshot.fiscal_year}")
    else:
        st.caption(f"No se pudieron calcular los ratios: {ratio_error}")

    st.subheader("Comparables")
    if comps_table is not None:
        st.dataframe(comps_table, width="stretch")
        st.caption(
            f"Múltiplo EV/EBITDA de salida usado en el valor terminal: **{terminal_multiple:.2f}x** "
            f"(mediana de {len(comps_table) - 1} comparables, excluyendo {target} — no el múltiplo de "
            "la propia empresa). Grupo curado a mano, no un universo exhaustivo por subsector — "
            "puede incluir compañías con perfiles de negocio distintos (ver hallazgo I4, `docs/AUDIT.md`)."
        )
    elif terminal_multiple:
        st.caption(
            f"Sin grupo de comparables en modo ticker libre — el múltiplo de salida usado "
            f"({terminal_multiple:.2f}x) es el de la propia empresa, no el de comparables."
        )
    else:
        st.caption("No hay múltiplo EV/EBITDA disponible — el valor terminal usa Gordon Growth puro.")

with tab_memo:
    st.subheader("Investment Memo")
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
