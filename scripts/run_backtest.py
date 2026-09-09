"""Script de backtesting walk-forward (Fase 7 extendida, sesión 17,
Lote C). Responde la pregunta que la validación transversal
(`scripts/validate_universe.py`) no puede: ¿la dirección de la
desviación del modelo en el PASADO predijo el retorno real posterior,
o el modelo solo se ajusta transversalmente al precio de hoy?

Congela cada ticker "como si" se valorara en `--date` (2 años atrás por
defecto): trunca su histórico a solo los años que ya existían entonces
(`engine.backtest.known_history_as_of`, con margen de retraso de
reporting), usa el precio de mercado y el risk-free rate REALES de esa
fecha (ambos vía yfinance, gratis), y compara la desviación resultante
contra el retorno real del precio hasta hoy.

Simplificación deliberada y documentada (ver docstring de
engine/backtest.py): beta, coste de deuda y acciones diluidas son los
de HOY, no los de la fecha de backtest -- no hay fuente gratuita de
beta histórico point-in-time. WACC "simplificado" (beta propia, no vía
comparables) para las 8 empresas del universo piloto, uniforme y
consistente entre todas.

Uso:
    ./.venv/Scripts/python.exe scripts/run_backtest.py
    ./.venv/Scripts/python.exe scripts/run_backtest.py --date 2024-09-06
    ./.venv/Scripts/python.exe scripts/run_backtest.py --years-back 3
"""

import argparse
import json
import statistics
import sys
import warnings
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # consola Windows (cp1252) no codifica "⚠️" por defecto

from engine.backtest import build_backtest_result, known_history_as_of
from engine.projections import default_assumptions_from_history, project_financials, stub_fraction_from_history
from engine.valuation import DCFInputs, cost_of_debt, cost_of_equity, run_dcf
from engine.valuation import wacc as wacc_fn
from engine.yfinance_provider import get_ticker as yf_get_ticker
from engine.yfinance_provider import historical_financials as yf_historical_financials
from engine.yfinance_provider import live_price as yf_live_price
from engine.yfinance_provider import market_snapshot as yf_market_snapshot

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "backtest_history"

MARKET_RISK_PREMIUM = 0.0406
N_YEARS = 5
TERMINAL_GROWTH_RATE = 0.025
LOOKBACK_YEARS = 3
GORDON_WEIGHT = 0.8
MIN_YEARS_OF_TRUNCATED_HISTORY = LOOKBACK_YEARS + 1  # el mínimo que exige default_assumptions_from_history

UNIVERSE = {
    "Big Tech / Cloud": {"tickers": ["AMZN", "MSFT", "GOOGL", "META", "AAPL"]},
    "Consumo defensivo": {"tickers": ["KO", "PG", "JNJ"]},
}


def _historical_price(ticker_obj, as_of_date: date) -> float:
    """Precio de cierre real más cercano a `as_of_date` (dentro de una
    ventana de 10 días para saltar fines de semana/festivos), vía
    yfinance -- funciona igual para cualquier ticker, sin importar de
    qué proveedor vienen sus estados financieros."""
    start = as_of_date - timedelta(days=10)
    end = as_of_date + timedelta(days=10)
    hist = ticker_obj.history(start=start.isoformat(), end=end.isoformat())
    if hist.empty:
        raise ValueError(f"yfinance no devolvió cotización histórica cerca de {as_of_date}.")
    hist = hist.copy()
    hist["_date"] = [ts.date() for ts in hist.index]
    hist["_delta"] = [abs((d - as_of_date).days) for d in hist["_date"]]
    closest = hist.sort_values("_delta").iloc[0]
    return float(closest["Close"])


def _risk_free_rate_at(as_of_date: date) -> float:
    tnx = yf_get_ticker("^TNX")
    return _historical_price(tnx, as_of_date) / 100.0


def _run_one(target: str, backtest_date: date, risk_free_rate_at_backtest: float):
    ticker_obj = yf_get_ticker(target)
    history = yf_historical_financials(ticker_obj)
    snap = yf_market_snapshot(ticker_obj)

    truncated = known_history_as_of(history, backtest_date)
    if len(truncated) < MIN_YEARS_OF_TRUNCATED_HISTORY:
        raise ValueError(
            f"Solo {len(truncated)} años de histórico habrían estado disponibles a fecha "
            f"{backtest_date} (se necesitan {MIN_YEARS_OF_TRUNCATED_HISTORY}) -- omitido."
        )

    # price_at_backtest y price_today deben venir de la MISMA fuente (yfinance),
    # o "retorno real" mezclaría dos convenios de precio distintos, no un
    # cambio de precio real.
    price_ticker = yf_get_ticker(target)
    price_at_backtest = _historical_price(price_ticker, backtest_date)
    price_today = yf_live_price(price_ticker)
    if not price_today:
        raise ValueError(f"Sin precio de mercado actual disponible para {target}.")

    beta = snap.get("beta")
    if beta is None:
        raise ValueError(f"Sin beta disponible para {target}.")
    interest_expense = truncated["interest_expense"].dropna()
    tax_rate_series = truncated["tax_rate"].dropna()
    if interest_expense.empty or tax_rate_series.empty:
        raise ValueError(f"Sin gasto financiero o tipo impositivo disponible para {target} en el histórico truncado.")

    market_cap_at_backtest = price_at_backtest * snap["shares_outstanding"]
    re = cost_of_equity(risk_free_rate_at_backtest, beta, MARKET_RISK_PREMIUM)
    rd = cost_of_debt(float(interest_expense.iloc[-1]), snap.get("total_debt") or 0)
    wacc_at_backtest = wacc_fn(market_cap_at_backtest, snap.get("total_debt") or 0, re, rd,
                                float(tax_rate_series.iloc[-1]))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assumptions = default_assumptions_from_history(truncated, n_years=N_YEARS, lookback_years=LOOKBACK_YEARS)
        stub_fraction = stub_fraction_from_history(truncated, valuation_date=backtest_date)
        projection = project_financials(truncated["revenue"].iloc[-1], assumptions)
        inputs = DCFInputs(
            ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
            capex=projection.capex, change_in_nwc=projection.change_in_nwc, wacc=wacc_at_backtest,
            terminal_growth_rate=TERMINAL_GROWTH_RATE, stub_fraction=stub_fraction,
            cash=snap.get("cash") or 0, total_debt=snap.get("total_debt") or 0,
            diluted_shares=snap["shares_outstanding"], gordon_weight=GORDON_WEIGHT,
        )
        implied_price_at_backtest = run_dcf(inputs).implied_share_price

    return build_backtest_result(
        ticker=target, backtest_date=backtest_date, price_at_backtest=price_at_backtest,
        price_today=price_today, implied_price_at_backtest=implied_price_at_backtest,
        wacc_at_backtest=wacc_at_backtest, years_of_history_used=len(truncated),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", type=str, default=None, help="Fecha de backtest YYYY-MM-DD (por defecto: hoy - 2 años)")
    parser.add_argument("--years-back", type=int, default=2, help="Años atrás desde hoy si no se pasa --date")
    args = parser.parse_args()
    backtest_date = date.fromisoformat(args.date) if args.date else date.today().replace(year=date.today().year - args.years_back)

    print(f"Fecha de backtest: {backtest_date}")
    risk_free_rate_at_backtest = _risk_free_rate_at(backtest_date)
    print(f"Risk-free rate real en esa fecha (^TNX): {risk_free_rate_at_backtest:.4%}\n")

    results = []
    for group_name, cfg in UNIVERSE.items():
        print(f"=== {group_name} ===")
        for target in cfg["tickers"]:
            try:
                result = _run_one(target, backtest_date, risk_free_rate_at_backtest)
            except Exception as e:
                print(f"  ⚠️  {target}: omitido ({e})")
                continue
            results.append(result)
            print(f"  {target}: precio_backtest=${result.price_at_backtest:.2f} "
                  f"implícito_backtest=${result.implied_price_at_backtest:.2f} "
                  f"desviación={result.deviation_at_backtest:+.1%} "
                  f"precio_hoy=${result.price_today:.2f} retorno_real={result.actual_return:+.1%}")
        print()

    if len(results) < 3:
        print("Menos de 3 resultados válidos -- no hay suficientes puntos para una correlación con sentido.")
        return

    deviations = [r.deviation_at_backtest for r in results]
    returns = [r.actual_return for r in results]
    correlation = statistics.correlation(deviations, returns)
    print(f"=== Correlación (n={len(results)}) ===")
    print(f"  desviación en la fecha de backtest vs. retorno real posterior: r = {correlation:+.3f}")
    print("  r > 0: una infravaloración marcada por el modelo tendió a preceder un retorno MAYOR")
    print("         (el mercado corrigió hacia el modelo -- señal predictiva en la dirección esperada).")
    print("  r < 0: una infravaloración marcada por el modelo tendió a preceder un retorno MENOR")
    print("         (el mercado ya tenía razón en pagar la prima -- sin señal predictiva, o inversa).")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{backtest_date.isoformat()}.json"
    payload = {
        "backtest_date": backtest_date.isoformat(),
        "risk_free_rate_at_backtest": risk_free_rate_at_backtest,
        "correlation_deviation_vs_actual_return": correlation,
        "results": [
            {**asdict(r), "deviation_at_backtest": r.deviation_at_backtest, "actual_return": r.actual_return}
            for r in results
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nGuardado: {output_path}")


if __name__ == "__main__":
    main()
