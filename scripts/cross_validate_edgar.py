"""Validador cruzado contra SEC EDGAR (sesión 17).

Motivo: la mayoría de bugs de código reales encontrados evaluando la
herramienta de punta a punta esta sesión (M8, M9, I13, y el más grave,
C2 -- "EBIT" incluía partidas no operativas) eran huecos/inconsistencias
del proveedor de datos, no errores del motor de valoración -- se
descubrieron auditando empresa por empresa a mano. Este script corre
esa misma comparación de forma sistemática y repetible: para cada
ticker, contrasta el último año del histórico ya usado por el pipeline
(yfinance) contra SEC EDGAR (gratis, sin cuota diaria) y avisa de
discrepancias por encima de un umbral.

Uso:
    ./.venv/Scripts/python.exe scripts/cross_validate_edgar.py AMZN MSFT GOOGL META AAPL

Requiere `SEC_EDGAR_USER_AGENT` en `.env` (contacto real, exigido por
la política de uso de SEC EDGAR -- ver `engine/edgar_provider.py`).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.edgar_provider import EdgarClient, EdgarError, cross_validate_latest_year
from engine.yfinance_provider import get_ticker as yf_get_ticker
from engine.yfinance_provider import historical_financials as yf_historical_financials


def _load_history(ticker: str):
    return yf_historical_financials(yf_get_ticker(ticker))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tickers", nargs="+", help="Tickers a contrastar")
    args = parser.parse_args()

    edgar = EdgarClient()
    any_mismatch = False

    for ticker in args.tickers:
        print(f"=== {ticker} ===")
        try:
            history = _load_history(ticker)
        except Exception as e:
            print(f"  fallo al cargar histórico: {e}\n")
            continue
        if history.empty:
            print("  histórico vacío, se omite\n")
            continue

        try:
            facts = edgar.company_facts(ticker)
        except EdgarError as e:
            print(f"  SEC EDGAR: {e}\n")
            continue

        comparisons = cross_validate_latest_year(facts, history.iloc[-1])
        if not comparisons:
            print("  SEC EDGAR no cubre ningún concepto comparable para el último año, se omite\n")
            continue

        for c in sorted(comparisons, key=lambda c: -abs(c.relative_diff)):
            flag = "  <== MISMATCH" if c.is_mismatch else ""
            print(f"  {c.concept:<20} proveedor={c.provider_value:>18,.0f}  "
                  f"edgar={c.edgar_value:>18,.0f}  diff={c.relative_diff:+.2%}{flag}")
            any_mismatch = any_mismatch or c.is_mismatch
        print()

    if any_mismatch:
        print("Hay discrepancias por encima del umbral -- revisar antes de confiar en el dato del proveedor.")
        sys.exit(1)


if __name__ == "__main__":
    main()
