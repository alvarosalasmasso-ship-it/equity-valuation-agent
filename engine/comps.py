"""Tabla de comparables (Fase 3 del blueprint).

Construye una tabla de múltiplos de mercado a partir de varios
`engine.data_provider.market_snapshot()` (uno por ticker del universo
piloto) y calcula el múltiplo medio/mediano de los peers — útil como
input de `engine.valuation.exit_multiple_terminal_value` en vez de
usar el múltiplo de la propia empresa (que puede estar ya sobre/infra
valorada, sesgando el valor terminal).
"""

from typing import Optional, Sequence

import pandas as pd

COMPS_COLUMNS = [
    "symbol", "sector", "industry", "market_cap", "price",
    "ev_to_ebitda", "ev_to_revenue", "pe_ratio", "price_to_sales",
    "price_to_book", "beta",
]


def build_comps_table(snapshots: Sequence[dict]) -> pd.DataFrame:
    """snapshots: lista de dicts devueltos por market_snapshot()."""
    if not snapshots:
        raise ValueError("Se necesita al menos un snapshot para construir la tabla de comps")
    df = pd.DataFrame(snapshots)
    existing_cols = [c for c in COMPS_COLUMNS if c in df.columns]
    return df[existing_cols].set_index("symbol")


def peer_average_multiple(comps: pd.DataFrame, column: str,
                           exclude_symbol: Optional[str] = None,
                           method: str = "median") -> float:
    """Múltiplo medio/mediano de los peers, excluyendo opcionalmente el
    ticker objetivo (para no usar su propio múltiplo como referencia de
    "mercado").
    """
    if column not in comps.columns:
        raise ValueError(f"Columna '{column}' no está en la tabla de comps")
    series = comps[column]
    if exclude_symbol is not None and exclude_symbol.upper() in series.index:
        series = series.drop(index=exclude_symbol.upper())
    series = series.dropna()
    if series.empty:
        raise ValueError(f"No hay valores disponibles de '{column}' para calcular el múltiplo de peers")
    if method == "median":
        return float(series.median())
    if method == "mean":
        return float(series.mean())
    raise ValueError("method debe ser 'median' o 'mean'")
