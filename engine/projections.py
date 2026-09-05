"""Motor de proyección: convierte históricos (engine.data_provider) en
las series futuras que pide engine.valuation.DCFInputs.

El Excel de referencia resuelve esto con el "Operating Model": supuestos
de % crecimiento / % sobre ventas fijados a mano por el analista, por
segmento de negocio. Para un ticker arbitrario no tenemos un analista
fijando esos supuestos a mano, así que este módulo los deriva del propio
histórico de forma sistemática y documentada, con un único mecanismo
aplicado a TODOS los drivers (crecimiento, márgenes, CapEx, ΔNWC):

    fade lineal desde un valor "año 1" (estado actual) hasta un valor
    "año N" (estado estable de largo plazo), a lo largo del horizonte
    de proyección.

Esto es "reversión a la media" estándar en DCF (Damodaran: toda empresa
converge a supuestos de industria/largo plazo con el tiempo, no
mantiene sus métricas actuales a perpetuidad). Por defecto:

- Año 1 = valor real del último ejercicio fiscal reportado (el mejor
  estimador disponible del "estado actual" de la compañía).
- Año N = media de los últimos `lookback_years` años (estimador del
  "estado normalizado" de largo plazo) para márgenes/CapEx/D&A/ΔNWC.

**Crecimiento de ingresos: plano durante todo el horizonte explícito,
NO fade hacia la tasa terminal.** Verificado contra el propio Excel de
referencia (docs/METHODOLOGY.md sección 14): el analista profesional
mantiene el crecimiento de Amazon prácticamente constante (~10-11%)
durante los 6 años de previsión explícita, y solo lo hace converger a la
tasa de crecimiento de largo plazo (g) de golpe, dentro de la fórmula de
Gordon Growth del valor terminal — nunca dentro de los años explícitos.
Por eso `default_assumptions_from_history()` fija
`revenue_growth = FadeAssumption(cagr_reciente, cagr_reciente)` (plano,
al CAGR de los últimos `lookback_years` años) y `terminal_growth_rate`
se usa EXCLUSIVAMENTE en `gordon_growth_terminal_value()` — nunca para
construir la serie de ingresos proyectados. Antes de este cambio, el
motor diluía el crecimiento linealmente hasta la tasa terminal dentro de
la propia ventana explícita, una forma de curva distinta a la que usa el
modelo de referencia (no solo más conservadora en el nivel, sino con una
forma de decaimiento distinta).

Sigue siendo un punto de partida transparente y sobreescribible, no una
predicción "óptima" — cualquier campo de ProjectionAssumptions se puede
fijar a mano en vez de aceptar el valor derivado del histórico (por
ejemplo, para modelar una desaceleración explícita del crecimiento
dentro del propio horizonte, algo que el analista del Excel no necesitó
para Amazon pero que puede ser razonable para otra compañía).
"""

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

import pandas as pd

from engine.valuation import compute_stub_fraction


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
class FadeAssumption:
    """Un driver que evoluciona linealmente desde `start` (año 1) hasta
    `end` (año N) a lo largo del horizonte de proyección. `start == end`
    equivale a mantener el driver constante (caso particular del fade)."""
    start: float
    end: float

    def path(self, n_years: int) -> list[float]:
        return linear_fade(self.start, self.end, n_years)


@dataclass
class ProjectionAssumptions:
    n_years: int
    revenue_growth: FadeAssumption
    ebit_margin: FadeAssumption
    da_pct_revenue: FadeAssumption
    capex_pct_revenue: FadeAssumption
    nwc_change_pct_revenue: FadeAssumption
    tax_rate: float  # se mantiene plano: fade de tipo impositivo no es práctica estándar


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
    """Proyecta revenue aplicando el fade de crecimiento, y cada línea
    (EBIT, D&A, CapEx, ΔNWC) como su propio % de revenue en fade."""
    n = assumptions.n_years
    growth_rates = assumptions.revenue_growth.path(n)
    revenue = []
    prev = last_actual_revenue
    for g in growth_rates:
        prev = prev * (1 + g)
        revenue.append(prev)

    ebit_margins = assumptions.ebit_margin.path(n)
    da_pcts = assumptions.da_pct_revenue.path(n)
    capex_pcts = assumptions.capex_pct_revenue.path(n)
    nwc_pcts = assumptions.nwc_change_pct_revenue.path(n)

    return ProjectionResult(
        revenue=revenue,
        ebit=[r * m for r, m in zip(revenue, ebit_margins)],
        tax_rate=[assumptions.tax_rate] * n,
        d_and_a=[r * p for r, p in zip(revenue, da_pcts)],
        capex=[r * p for r, p in zip(revenue, capex_pcts)],
        change_in_nwc=[r * p for r, p in zip(revenue, nwc_pcts)],
    )


def _margin_fade_from_recent_to_average(window: pd.DataFrame, column: str) -> FadeAssumption:
    """Año 1 = margen real del último año de la ventana; año N = media
    de la ventana completa. Si ambos coinciden (histórico plano), el
    fade colapsa a un valor constante."""
    most_recent = window.iloc[-1]
    recent_value = most_recent[column] / most_recent["revenue"]
    average_value = average_margin(window[column].tolist(), window["revenue"].tolist())
    return FadeAssumption(start=float(recent_value), end=float(average_value))


def default_assumptions_from_history(history: pd.DataFrame, n_years: int = 5,
                                      lookback_years: int = 5) -> ProjectionAssumptions:
    """Deriva supuestos de proyección a partir de los últimos
    `lookback_years` años de `engine.data_provider.historical_financials`.

    Dos ventanas distintas y deliberadamente NO intercambiables:
    - CAGR de ingresos: necesita `lookback_years + 1` puntos para medir
      `lookback_years` periodos de crecimiento (los extremos del CAGR).
    - Márgenes (EBIT, D&A, CapEx, ΔNWC) y tipo impositivo: usan
      exactamente los últimos `lookback_years` puntos — mezclar ambas
      ventanas colaría un año adicional, más antiguo, sesgando la media.

    Requiere al menos 2 años de revenue no nulo para el CAGR, y al menos
    1 año con datos para cada margen.

    No recibe `terminal_growth_rate`: el crecimiento de ingresos se
    proyecta PLANO al CAGR reciente durante todo el horizonte explícito
    (como hace el analista del Excel de referencia), y la tasa de
    crecimiento terminal se aplica únicamente dentro de
    `engine.valuation.gordon_growth_terminal_value()` al construir el
    valor terminal — nunca aquí. Pásala directamente a `DCFInputs`/
    `run_dcf` cuando calcules la valoración completa.
    """
    revenue_window = history.dropna(subset=["revenue"]).tail(lookback_years + 1)
    if len(revenue_window) < 2:
        raise ValueError("Se necesitan al menos 2 años de revenue histórico")

    revenue_series = revenue_window["revenue"].tolist()
    initial_growth = cagr(revenue_series[0], revenue_series[-1], len(revenue_series) - 1)

    margin_window = history.tail(lookback_years)
    if margin_window.empty or pd.isna(margin_window.iloc[-1].get("revenue")):
        raise ValueError("No hay datos suficientes en la ventana de márgenes")

    ebit_margin = _margin_fade_from_recent_to_average(margin_window, "ebit")
    da_pct = _margin_fade_from_recent_to_average(margin_window, "d_and_a")
    capex_pct = _margin_fade_from_recent_to_average(margin_window, "capex")

    nwc_window = margin_window.dropna(subset=["change_in_nwc", "revenue"])
    nwc_fade = (_margin_fade_from_recent_to_average(nwc_window, "change_in_nwc")
                if len(nwc_window) > 0 else FadeAssumption(0.0, 0.0))

    tax_rate = margin_window["tax_rate"].dropna().mean()
    if pd.isna(tax_rate):
        raise ValueError("No hay tax_rate histórico disponible")

    return ProjectionAssumptions(
        n_years=n_years,
        revenue_growth=FadeAssumption(start=initial_growth, end=initial_growth),  # plano, ver docstring
        ebit_margin=ebit_margin,
        da_pct_revenue=da_pct,
        capex_pct_revenue=capex_pct,
        nwc_change_pct_revenue=nwc_fade,
        tax_rate=float(tax_rate),
    )


def stub_fraction_from_history(history: pd.DataFrame, valuation_date: Optional[date] = None) -> float:
    """Calcula el stub period real (`engine.valuation.compute_stub_fraction`)
    a partir del cierre de ejercicio fiscal del último año disponible en
    `history` (columnas `fiscal_year_end_month`/`fiscal_year_end_day`,
    expuestas por `engine.data_provider`/`engine.yfinance_provider`).

    Auditoría (sesión 15, hallazgo C1): antes de esta función, todo el
    pipeline construía `DCFInputs` con el `stub_fraction` por defecto
    (1.0), asumiendo implícitamente que la valoración se hace siempre el
    1 de enero del primer año proyectado. Esta función cierra ese hueco
    a partir del histórico ya cargado, sin pedir un dato nuevo al
    usuario.

    Si `history` no trae esas columnas (p.ej. un histórico sintético en
    tests, o construido a mano) devuelve 1.0 — mismo comportamiento que
    antes de la auditoría, para no romper nada que no pase por
    `historical_financials()`.
    """
    if "fiscal_year_end_month" not in history.columns or "fiscal_year_end_day" not in history.columns:
        return 1.0
    clean = history.dropna(subset=["fiscal_year_end_month", "fiscal_year_end_day"])
    if clean.empty:
        return 1.0
    last_row = clean.iloc[-1]
    return compute_stub_fraction(
        fiscal_year_end_month=int(last_row["fiscal_year_end_month"]),
        fiscal_year_end_day=int(last_row["fiscal_year_end_day"]),
        valuation_date=valuation_date or date.today(),
    )
