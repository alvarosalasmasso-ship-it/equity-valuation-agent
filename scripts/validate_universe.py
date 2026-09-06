"""Script de validación reproducible (Fase 7, sesión 16).

Corre el pipeline completo (`engine.validation.value_ticker`) sobre los
universos piloto -- Big Tech/Cloud (Alpha Vantage) y Consumo defensivo
(yfinance) -- con el risk-free rate en vivo del día, y guarda un
snapshot fechado en `data/validation_history/`.

Motivo (docs/PROGRESS_REVIEW.md sección 5, ítem 4): la cifra de
"desviación media vs. mercado" se ha tenido que reconstruir a mano con
Python interactivo cada vez que hacía falta -- sin esto, no hay forma
de defender esas cifras del CV/entrevista de forma reproducible, ni de
ver cómo evolucionan en el tiempo. Este script reemplaza esa
reconstrucción manual, no añade ningún cálculo nuevo: usa exactamente
`value_ticker()`, ya testeado.

Uso:
    ./.venv/Scripts/python.exe scripts/validate_universe.py
    ./.venv/Scripts/python.exe scripts/validate_universe.py --date 2026-09-06

Requiere `ALPHA_VANTAGE_API_KEY` en `.env` para el grupo "Big Tech /
Cloud" (usa el caché en disco de 24h si existe, igual que la app --
no gasta cuota extra en ejecuciones repetidas el mismo día). El grupo
"Consumo defensivo" usa yfinance, sin API key.
"""

import argparse
import json
import sys
import warnings
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from engine.data_provider import AlphaVantageClient
from engine.data_provider import historical_financials as av_historical_financials
from engine.data_provider import market_snapshot as av_market_snapshot
from engine.validation import bootstrap_deviation_ci, summarize_deviation, value_ticker
from engine.yfinance_provider import get_ticker as yf_get_ticker
from engine.yfinance_provider import historical_financials as yf_historical_financials
from engine.yfinance_provider import live_price as yf_live_price
from engine.yfinance_provider import market_snapshot as yf_market_snapshot
from engine.yfinance_provider import treasury_yield_10y

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "validation_history"

# Mismos parámetros por defecto que app/streamlit_app.py -- ver ese
# archivo y docs/AUDIT.md para la justificación de cada uno (I1 para el
# risk-free rate en vivo; M2, aún abierto, para gordon_weight=0.8).
MARKET_RISK_PREMIUM = 0.0406
N_YEARS = 5
TERMINAL_GROWTH_RATE = 0.025
LOOKBACK_YEARS = 3
GORDON_WEIGHT = 0.8

UNIVERSES = {
    "Big Tech / Cloud": {"tickers": ["AMZN", "MSFT", "GOOGL", "META", "AAPL"], "provider": "alpha_vantage"},
    "Consumo defensivo": {"tickers": ["KO", "PG", "JNJ"], "provider": "yfinance"},
}


def _load_universe(provider: str, tickers: list[str]) -> tuple[dict, dict]:
    if provider == "alpha_vantage":
        client = AlphaVantageClient()
        hist = {t: av_historical_financials(client, t, use_cache=True) for t in tickers}
        snap = {t: av_market_snapshot(client, t, use_cache=True) for t in tickers}
        # Auditoría sesión 17: el precio derivado de Alpha Vantage
        # (MarketCapitalization/SharesOutstanding) resultó mal para 2 de 5
        # tickers reales de Big Tech (GOOGL 2.08x, META 1.155x -- este
        # segundo no detectable solo con campos de Alpha Vantage, ver
        # engine.data_provider._validate_derived_price). yfinance se usa
        # como fuente PREFERIDA de cotización, con el precio derivado de AV
        # solo como último recurso -- mismo criterio que app/streamlit_app.py.
        for t in tickers:
            fallback = yf_live_price(yf_get_ticker(t))
            if fallback is not None:
                snap[t]["price"] = fallback
        return hist, snap
    hist, snap = {}, {}
    for t in tickers:
        ticker_obj = yf_get_ticker(t)
        hist[t] = yf_historical_financials(ticker_obj)
        snap[t] = yf_market_snapshot(ticker_obj)
    return hist, snap


def _run_group(tickers: list[str], provider: str, risk_free_rate: float,
               valuation_date: date) -> tuple[list, dict]:
    """Valora cada ticker del grupo de forma aislada: si uno falla (dato
    faltante, fallo de red puntual), se omite con un aviso en stderr en
    vez de tirar abajo todo el grupo -- a diferencia de
    engine.validation.validate_universe(), que no aísla fallos por
    ticker."""
    hist_data, snap_data = _load_universe(provider, tickers)

    checks, warnings_by_ticker = [], {}
    for t in tickers:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                check = value_ticker(
                    t, hist_data, snap_data, risk_free_rate=risk_free_rate,
                    market_risk_premium=MARKET_RISK_PREMIUM, n_years=N_YEARS,
                    terminal_growth_rate=TERMINAL_GROWTH_RATE, lookback_years=LOOKBACK_YEARS,
                    gordon_weight=GORDON_WEIGHT, valuation_date=valuation_date,
                )
            except Exception as e:
                print(f"  ⚠️  {t}: fallo al valorar ({e}) -- omitido", file=sys.stderr)
                continue
        checks.append(check)
        if caught:
            warnings_by_ticker[t] = [str(w.message) for w in caught]
    return checks, warnings_by_ticker


def _checks_to_dataframe(checks: list) -> pd.DataFrame:
    return pd.DataFrame([asdict(c) for c in checks]).set_index("ticker")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=str, default=None,
                         help="Fecha de valoración YYYY-MM-DD (por defecto: hoy)")
    args = parser.parse_args()
    valuation_date = date.fromisoformat(args.date) if args.date else date.today()

    print(f"Consultando risk-free rate en vivo (Treasury 10Y, ^TNX)...")
    risk_free_rate = treasury_yield_10y(yf_get_ticker("^TNX"))
    print(f"  Risk-free rate: {risk_free_rate:.4%}\n")

    report = {
        "run_date": valuation_date.isoformat(),
        "risk_free_rate": risk_free_rate,
        "market_risk_premium": MARKET_RISK_PREMIUM,
        "parameters": {
            "n_years": N_YEARS, "terminal_growth_rate": TERMINAL_GROWTH_RATE,
            "lookback_years": LOOKBACK_YEARS, "gordon_weight": GORDON_WEIGHT,
        },
        "groups": {},
    }
    all_checks = []

    for group_name, cfg in UNIVERSES.items():
        print(f"=== {group_name} ({cfg['provider']}) ===")
        checks, warnings_by_ticker = _run_group(cfg["tickers"], cfg["provider"], risk_free_rate, valuation_date)
        if not checks:
            print("  Sin tickers valorados en este grupo.\n")
            continue

        df = _checks_to_dataframe(checks)
        summary = summarize_deviation(df)
        print(df[["wacc", "implied_price", "market_price", "analyst_target_price",
                   "deviation_vs_market", "deviation_vs_consensus"]].to_string())
        print(f"  Desv. media abs. vs. mercado: {summary['mean_abs_deviation_vs_market']:.2%}"
              f"  |  vs. consenso: {summary['mean_abs_deviation_vs_consensus']:.2%}\n")

        report["groups"][group_name] = {
            "tickers": [asdict(c) for c in checks],
            "warnings": warnings_by_ticker,
            "summary": summary,
        }
        all_checks.extend(checks)

    if all_checks:
        combined_df = _checks_to_dataframe(all_checks)
        combined_summary = summarize_deviation(combined_df)
        report["combined_summary"] = combined_summary
        print(f"=== Combinado ({combined_summary['n']} tickers) ===")
        print(f"  Desv. media abs. vs. mercado: {combined_summary['mean_abs_deviation_vs_market']:.2%}"
              f"  |  vs. consenso: {combined_summary['mean_abs_deviation_vs_consensus']:.2%}\n")

        # Sesión 17, item E del lote de rigor matemático (estado.md
        # sección 13): la cifra puntual de arriba no comunica cuánta
        # incertidumbre de muestreo hay detrás de un universo piloto de
        # solo n=8 tickers. Semilla fija para que el JSON guardado sea
        # reproducible entre ejecuciones del mismo día, no solo el
        # cómputo de por sí determinista salvo por esta semilla.
        if combined_summary["n"] >= 2:
            ci = bootstrap_deviation_ci(combined_df, seed=17)
            report["combined_summary_bootstrap_ci"] = ci
            print(f"  IC bootstrap (80%, n_bootstrap={ci['n_bootstrap']}): "
                  f"[{ci['ci_lower']:.2%}, {ci['ci_upper']:.2%}] "
                  f"alrededor de {ci['point_estimate']:.2%} -- muestra pequeña (n={ci['n']}), "
                  "intervalo ancho esperado.\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{valuation_date.isoformat()}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"Guardado: {output_path}")


if __name__ == "__main__":
    main()
