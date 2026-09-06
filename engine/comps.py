"""Tabla de comparables (Fase 3 del blueprint).

Construye una tabla de múltiplos de mercado a partir de varios
`engine.data_provider.market_snapshot()` (uno por ticker del universo
piloto) y calcula el múltiplo medio/mediano de los peers — útil como
input de `engine.valuation.exit_multiple_terminal_value` en vez de
usar el múltiplo de la propia empresa (que puede estar ya sobre/infra
valorada, sesgando el valor terminal).

`comps_implied_share_price()` (sesión 17) añade el mismo múltiplo de
peers como MÉTODO DE VALORACIÓN INDEPENDIENTE — no como input del DCF,
sino aplicado directamente al EBITDA/ingresos actuales de la empresa
objetivo para obtener un precio implícito "por comparables", tal como
lo presenta cualquier informe de equity research bancario junto al DCF
(ver docs/METHODOLOGY.md sección 23, "football field")."""

from dataclasses import dataclass
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


@dataclass
class CompsValuation:
    """Precio implícito de aplicar el múltiplo de peers directamente al
    tamaño actual (último año real) de la empresa objetivo -- un método
    de valoración independiente del DCF, no un input suyo.

    `implied_share_price_from_ebitda`/`_from_revenue` son `None` cuando
    la métrica base no es significativa (EBITDA <= 0: el múltiplo
    EV/EBITDA no tiene interpretación económica sobre una base negativa
    o nula -- por eso EV/Revenue existe como alternativa, robusta
    incluso en compañías sin beneficios)."""
    ev_ebitda_multiple: float
    ev_revenue_multiple: float
    target_ebitda: float
    target_revenue: float
    net_debt: float
    implied_ev_from_ebitda: float
    implied_ev_from_revenue: float
    implied_share_price_from_ebitda: Optional[float]
    implied_share_price_from_revenue: Optional[float]


def comps_implied_share_price(comps: pd.DataFrame, target_symbol: str, target_ebitda: float,
                               target_revenue: float, cash: float, total_debt: float,
                               diluted_shares: float, method: str = "median") -> CompsValuation:
    """Aplica el múltiplo EV/EBITDA y EV/Revenue de los peers (excluyendo
    `target_symbol` de la propia tabla) al EBITDA/ingresos reales de la
    empresa objetivo, para obtener un precio implícito por acción --
    exactamente la segunda pata de un "football field" bancario, junto
    al rango del DCF (ver docs/METHODOLOGY.md sección 23).

    target_ebitda/target_revenue: del último año real (p.ej.
    history["ebit"].iloc[-1] + history["d_and_a"].iloc[-1] y
    history["revenue"].iloc[-1]) -- el mismo "estado actual" que ancla
    el resto del motor, no una proyección."""
    if diluted_shares <= 0:
        raise ValueError("diluted_shares debe ser positivo")

    ev_ebitda_mult = peer_average_multiple(comps, "ev_to_ebitda", exclude_symbol=target_symbol, method=method)
    ev_revenue_mult = peer_average_multiple(comps, "ev_to_revenue", exclude_symbol=target_symbol, method=method)

    net_debt = total_debt - cash
    implied_ev_from_ebitda = target_ebitda * ev_ebitda_mult
    implied_ev_from_revenue = target_revenue * ev_revenue_mult

    price_from_ebitda = ((implied_ev_from_ebitda - net_debt) / diluted_shares
                          if target_ebitda > 0 else None)
    price_from_revenue = ((implied_ev_from_revenue - net_debt) / diluted_shares
                           if target_revenue > 0 else None)

    return CompsValuation(
        ev_ebitda_multiple=ev_ebitda_mult, ev_revenue_multiple=ev_revenue_mult,
        target_ebitda=target_ebitda, target_revenue=target_revenue, net_debt=net_debt,
        implied_ev_from_ebitda=implied_ev_from_ebitda, implied_ev_from_revenue=implied_ev_from_revenue,
        implied_share_price_from_ebitda=price_from_ebitda,
        implied_share_price_from_revenue=price_from_revenue,
    )
