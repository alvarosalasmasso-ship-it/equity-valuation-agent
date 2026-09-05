"""Motor de proyección: convierte históricos (engine.data_provider) en
las series futuras que pide engine.valuation.DCFInputs.

El Excel de referencia resuelve esto con el "Operating Model": supuestos
de % crecimiento / % sobre ventas fijados a mano por el analista, por
segmento de negocio. Para un ticker arbitrario no tenemos un analista
fijando esos supuestos a mano, así que este módulo los deriva del propio
histórico de forma sistemática y documentada:

- Crecimiento de ingresos: CAGR de los últimos N años, con "fade" lineal
  hacia la tasa de crecimiento terminal a lo largo del horizonte de
  proyección (evita extrapolar un crecimiento alto de forma irreal a
  perpetuidad — práctica estándar en DCF, ver Damodaran).
- Márgenes (EBIT, D&A, CapEx, ΔNWC como % de ventas) y tipo impositivo:
  media de los últimos N años, mantenidos constantes.

Estos son supuestos por defecto razonables y transparentes, no una
predicción "óptima" — el objetivo es tener un punto de partida auditable
que el usuario pueda sobreescribir explícitamente (pasando su propio
ProjectionAssumptions) en vez de aceptar una caja negra.
"""

import statistics
from dataclasses import dataclass
from typing import Sequence

import pandas as pd


def cagr(first_value: float, last_value: float, n_periods: int) -> float:
    """Tasa de crecimiento anual compuesto entre dos valores separados
    n_periods periodos. Requiere first_value > 0."""
    if n_periods <= 0:
        raise ValueError("n_periods debe ser positivo")
    if first_value <= 0:
        raise ValueError("first_value debe ser positivo para calcular un CAGR")
    return (last_value / first_value) ** (1 / n_periods) - 1


def average_margin(numerator: Sequence[float], denominator: Sequence[float]) -> float:
    """Media de numerator[i]/denominator[i], ignorando pares con datos
    faltantes (NaN/None)."""
    ratios = [
        n / d for n, d in zip(numerator, denominator)
        if n is not None and d not in (None, 0) and not _is_nan(n) and not _is_nan(d)
    ]
    if not ratios:
        raise ValueError("No hay pares válidos para calcular el margen medio")
    return statistics.mean(ratios)


def _is_nan(value) -> bool:
    return isinstance(value, float) and value != value


def linear_fade(start: float, end: float, n_years: int) -> list[float]:
    """n_years valores interpolados linealmente desde `start` (año 1)
    hasta `end` (año n_years), ambos incluidos."""
    if n_years < 1:
        raise ValueError("n_years debe ser >= 1")
    if n_years == 1:
        return [end]
    step = (end - start) / (n_years - 1)
    return [start + step * i for i in range(n_years)]


@dataclass
class ProjectionAssumptions:
    n_years: int
    initial_revenue_growth: float
    terminal_revenue_growth: float
    ebit_margin: float
    da_pct_revenue: float
    capex_pct_revenue: float
    nwc_change_pct_revenue: float
    tax_rate: float


@dataclass
class ProjectionResult:
    revenue: list[float]
    ebit: list[float]
    tax_rate: list[float]
    d_and_a: list[float]
    capex: list[float]
    change_in_nwc: list[float]


def project_financials(last_actual_revenue: float,
                        assumptions: ProjectionAssumptions) -> ProjectionResult:
    """Proyecta revenue con la tasa de crecimiento en fade lineal, y el
    resto de líneas como % constante de ese revenue proyectado."""
    growth_rates = linear_fade(
        assumptions.initial_revenue_growth,
        assumptions.terminal_revenue_growth,
        assumptions.n_years,
    )
    revenue = []
    prev = last_actual_revenue
    for g in growth_rates:
        prev = prev * (1 + g)
        revenue.append(prev)

    return ProjectionResult(
        revenue=revenue,
        ebit=[r * assumptions.ebit_margin for r in revenue],
        tax_rate=[assumptions.tax_rate] * assumptions.n_years,
        d_and_a=[r * assumptions.da_pct_revenue for r in revenue],
        capex=[r * assumptions.capex_pct_revenue for r in revenue],
        change_in_nwc=[r * assumptions.nwc_change_pct_revenue for r in revenue],
    )


def default_assumptions_from_history(history: pd.DataFrame, n_years: int = 5,
                                      terminal_growth_rate: float = 0.025,
                                      lookback_years: int = 5) -> ProjectionAssumptions:
    """Deriva supuestos de proyección a partir de los últimos
    `lookback_years` años de `engine.data_provider.historical_financials`.

    Dos ventanas distintas y deliberadamente NO intercambiables:
    - CAGR de ingresos: necesita `lookback_years + 1` puntos para medir
      `lookback_years` periodos de crecimiento (los extremos del CAGR).
    - Márgenes (EBIT, D&A, CapEx, ΔNWC) y tipo impositivo: media de
      exactamente los últimos `lookback_years` años (no N+1) — mezclar
      ambas ventanas colaría un año adicional, más antiguo, en la media
      de márgenes, sesgándola en empresas con tendencia de margen fuerte.

    Requiere al menos 2 años de revenue no nulo para el CAGR, y al menos
    1 año con datos para cada margen.
    """
    revenue_window = history.dropna(subset=["revenue"]).tail(lookback_years + 1)
    if len(revenue_window) < 2:
        raise ValueError("Se necesitan al menos 2 años de revenue histórico")

    revenue_series = revenue_window["revenue"].tolist()
    initial_growth = cagr(revenue_series[0], revenue_series[-1], len(revenue_series) - 1)

    margin_window = history.tail(lookback_years)
    ebit_margin = average_margin(margin_window["ebit"].tolist(), margin_window["revenue"].tolist())
    da_pct = average_margin(margin_window["d_and_a"].tolist(), margin_window["revenue"].tolist())
    capex_pct = average_margin(margin_window["capex"].tolist(), margin_window["revenue"].tolist())

    nwc_window = margin_window.dropna(subset=["change_in_nwc", "revenue"])
    nwc_pct = (average_margin(nwc_window["change_in_nwc"].tolist(), nwc_window["revenue"].tolist())
               if len(nwc_window) > 0 else 0.0)

    tax_rate = margin_window["tax_rate"].dropna().mean()
    if pd.isna(tax_rate):
        raise ValueError("No hay tax_rate histórico disponible")

    return ProjectionAssumptions(
        n_years=n_years,
        initial_revenue_growth=initial_growth,
        terminal_revenue_growth=terminal_growth_rate,
        ebit_margin=ebit_margin,
        da_pct_revenue=da_pct,
        capex_pct_revenue=capex_pct,
        nwc_change_pct_revenue=nwc_pct,
        tax_rate=float(tax_rate),
    )
