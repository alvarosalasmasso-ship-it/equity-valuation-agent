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

import math
import statistics
import warnings
from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Sequence

import pandas as pd

from engine.valuation import DCFInputs, compute_stub_fraction, run_dcf, solve_for_target_price


def cagr(first_value: float, last_value: float, n_periods: int) -> float:
    """Tasa de crecimiento anual compuesto entre dos valores separados
    n_periods periodos. Requiere first_value > 0."""
    if n_periods <= 0:
        raise ValueError("n_periods debe ser positivo")
    if first_value <= 0:
        raise ValueError("first_value debe ser positivo para calcular un CAGR")
    return (last_value / first_value) ** (1 / n_periods) - 1


def _valid_ratio_series(numerator: Sequence[float], denominator: Sequence[float]) -> list[float]:
    """numerator[i]/denominator[i] en orden cronológico, ignorando pares
    con datos faltantes (NaN/None). Compartido por average_margin() y la
    detección de outliers del ancla del fade."""
    return [
        n / d for n, d in zip(numerator, denominator)
        if n is not None and d not in (None, 0) and not _is_nan(n) and not _is_nan(d)
    ]


def average_margin(numerator: Sequence[float], denominator: Sequence[float]) -> float:
    """Media de numerator[i]/denominator[i], ignorando pares con datos
    faltantes (NaN/None)."""
    ratios = _valid_ratio_series(numerator, denominator)
    if not ratios:
        raise ValueError("No hay pares válidos para calcular el margen medio")
    return statistics.mean(ratios)


def _is_nan(value) -> bool:
    return isinstance(value, float) and value != value


# Iglewicz, B. y Hoaglin, D. (1993), "How to Detect and Handle Outliers" --
# regla estándar del z-score modificado (mediana + MAD, robusto en muestras
# pequeñas a diferencia de media/desviación típica clásicas). |z| > 3.5 se
# considera outlier. Verificado contra el caso real documentado en
# docs/METHODOLOGY.md sección 9 (JNJ, margen EBIT 2025 = 35.6% frente a
# 18.6%/19.6% en 2023/2024, un ítem no recurrente por la escisión de
# Kenvue): da z=21.3, muy por encima del umbral.
OUTLIER_MODIFIED_Z_THRESHOLD = 3.5

# Sesión 17 (prueba de estrés, docs/AUDIT.md hallazgo abierto): NVDA
# mostró un valor terminal de $21.7 billones (trillion) manteniendo su
# CAGR real reciente (~100%/año) plano durante los 5 años del horizonte
# -- económicamente implausible, pese a que el crecimiento observado es
# real (demanda de chips de IA, no un error de datos). Regla de pulgar
# (no una ley exacta, igual que MIN_PRUDENT_WACC_GROWTH_SPREAD): no hay
# un umbral objetivo verificado con datos de que a partir de qué tasa
# el "flat CAGR" deja de ser razonable -- inventar uno con precisión
# falsa repetiría el error que se evitó con M2. 50%/año es un punto de
# corte defendible (pocas compañías reales sostienen eso varios años
# seguidos sin desacelerar) pero deliberadamente conservador, solo para
# avisar, nunca para bloquear ni ajustar el número.
EXTREME_FLAT_GROWTH_WARNING_THRESHOLD = 0.50

# Sesión 17 (hallazgo I15, evaluación del grupo biotech/farma): a
# diferencia de margen EBIT/D&A/CapEx/ΔNWC (que dividen entre ingresos,
# un denominador que nunca se acerca a cero para una empresa operativa),
# `tax_rate` divide entre `pretax_income`, que SÍ puede acercarse a cero
# en un año de reestructuración/pérdidas/cargo no recurrente -- un solo
# año así puede disparar un ratio de cientos por ciento (VRTX 2024:
# 315.5%, tras un cargo real de I+D en proceso de una adquisición). Se
# evaluó excluir el año outlier de la media (mismo mecanismo Iglewicz &
# Hoaglin que ya usa `_detect_anchor_outlier`), pero probado contra los
# 24 tickers reales ya usados en la auditoría de esta sesión, disparaba
# en 9 de 24 -- muchos casos de variación normal amplificada por el
# tamaño de muestra pequeño (p.ej. QCOM: excluir su único año alto
# (56.2%) deja una media de 1.8%, igual de irreal que el original) --
# el mismo error, ya investigado y rechazado, que motivó que
# `_margin_fade_from_recent_to_average` NUNCA sustituya automáticamente
# (ver su docstring). En vez de repetir ese error, se avisa sobre el
# RESULTADO final si cae fuera de una banda plausible, sin tocar el
# número -- verificado contra los mismos 24 tickers: solo 3 (AMD, INTC,
# VRTX) caen fuera de esta banda, cero falsos positivos. Regla de
# pulgar (no una ley exacta, mismo espíritu que
# EXTREME_FLAT_GROWTH_WARNING_THRESHOLD): la banda no es un tipo
# estatutario (M6 ya investigó y rechazó anclar a eso), es un rango
# amplio informado por tipos efectivos reales observados esta sesión.
TAX_RATE_PLAUSIBLE_RANGE = (-0.10, 0.60)


def _detect_anchor_outlier(chronological_ratios: Sequence[float]) -> tuple[bool, float]:
    """Compara el último valor de la serie (candidato a ancla `start` del
    fade) contra la mediana/MAD de los años PREVIOS -- nunca contra una
    distribución que lo incluya a él mismo. Devuelve (es_outlier, z
    modificado). Con menos de 2 años de referencia no hay base estadística
    para juzgar, así que nunca se marca outlier."""
    if len(chronological_ratios) < 3:
        return False, 0.0
    candidate = chronological_ratios[-1]
    reference = chronological_ratios[:-1]
    median_ref = statistics.median(reference)
    mad = statistics.median([abs(v - median_ref) for v in reference])
    if mad == 0:
        # Referencia sin variación (histórico plano): solo es outlier si la
        # diferencia es real, no ruido de coma flotante de un mismo margen
        # calculado a partir de revenues distintos (p.ej. r*0.05 / r).
        differs = not math.isclose(candidate, median_ref, rel_tol=1e-9, abs_tol=1e-12)
        return differs, float("inf") if differs else 0.0
    modified_z = 0.6745 * (candidate - median_ref) / mad
    return abs(modified_z) > OUTLIER_MODIFIED_Z_THRESHOLD, modified_z


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


def _margin_fade_from_recent_to_average(window: pd.DataFrame, column: str,
                                         driver_label: Optional[str] = None) -> FadeAssumption:
    """Año 1 = margen real del último año de la ventana; año N = media
    de la ventana completa. Si ambos coinciden (histórico plano), el
    fade colapsa a un valor constante.

    Aviso de outlier (sesión 17, ver docs/METHODOLOGY.md sección 9 y el
    hallazgo de JNJ): si el último año es un outlier estadístico frente a
    los años previos (`_detect_anchor_outlier`), SE AVISA pero NO se
    sustituye el ancla -- se probó primero la sustitución automática
    (mediana de años previos) y, con datos reales del universo piloto
    completo, resultó ser la decisión equivocada: dispara en 7 de 8
    tickers, incluido el CapEx de MSFT/META (34.9%/34.7% de ventas), que
    es precisamente el supercycle de inversión en IA ya verificado como
    real en la sección 7 -- no ruido. Con solo 2-3 años de referencia, un
    z-score no puede distinguir "ítem no recurrente" (JNJ, Kenvue) de
    "inicio de una tendencia estructural real" (CapEx de IA): esa
    distinción requiere criterio cualitativo, no estadística de muestra
    pequeña. Por eso el motor mantiene siempre el valor real como año 1
    (fiel al principio "el último año real es el mejor estimador del
    estado actual") y solo señala la anomalía para que el usuario la
    revise -- mismo patrón que MIN_PRUDENT_WACC_GROWTH_SPREAD en
    engine.valuation: nunca un ajuste silencioso.

    Auditoría (sesión 17, prueba de estrés con tickers reales fuera del
    universo piloto): con XOM (sin D&A reportado por yfinance), PLD
    -REIT- (sin CapEx, se reporta de otra forma) o JPM -banco- (sin
    EBIT ni CapEx en el sentido tradicional), `window[column]` puede no
    tener NINGÚN valor válido en toda la ventana -- `ratios` sale vacío
    y `ratios[-1]` crasheaba con `IndexError`, sin capturar en ningún
    punto del pipeline real (ni en modo "cualquier ticker" ni en
    "universo cacheado", el `try/except` de ambos termina antes de este
    punto). Fallar aquí con un ValueError claro, identificando la
    partida concreta, es intencionadamente la misma señal que ya da
    esta función para outliers -- pero además interpretable como lo que
    realmente es: esta empresa/sector no encaja con el esquema de
    columnas que asume el motor (ver docs/AUDIT.md, nuevo hallazgo)."""
    ratios = _valid_ratio_series(window[column].tolist(), window["revenue"].tolist())
    if not ratios:
        raise ValueError(
            f"Sin ningún dato válido de '{driver_label or column}' en el histórico "
            f"disponible -- no se puede construir la proyección. Frecuente en sectores "
            "con estados financieros no estándar (bancos/financieras, REITs) o cuando "
            "el proveedor de datos no reporta esa partida para esta empresa."
        )
    recent_value = ratios[-1]
    is_outlier, modified_z = _detect_anchor_outlier(ratios)
    if is_outlier:
        reference_median = statistics.median(ratios[:-1])
        warnings.warn(
            f"{driver_label or column}: el último año ({recent_value:.1%}) es un outlier "
            f"estadístico frente a los {len(ratios) - 1} años previos (mediana {reference_median:.1%}, "
            f"z modificado={modified_z:.1f}, umbral {OUTLIER_MODIFIED_Z_THRESHOLD} -- Iglewicz & "
            "Hoaglin). Se mantiene como año 1 del fade (el motor no puede distinguir si es un ítem "
            "no recurrente o el inicio de una tendencia real) -- revisa manualmente si conviene "
            "ajustar el escenario.",
            stacklevel=3,
        )
    average_value = average_margin(window[column].tolist(), window["revenue"].tolist())
    return FadeAssumption(start=float(recent_value), end=float(average_value))


def historical_ratio_stats(history: pd.DataFrame, column: str, lookback_years: int) -> tuple[float, float]:
    """Media y desviación típica de `column`/`revenue` sobre exactamente
    los últimos `lookback_years` puntos -- misma ventana que usa
    `_margin_fade_from_recent_to_average()` para el fade, expuesta aquí
    para que `engine.monte_carlo` pueda muestrear cada supuesto desde su
    propia dispersión histórica real en vez de un rango inventado.
    Desviación típica muestral (ddof=1); con un único punto válido
    devuelve std=0.0 (no hay dispersión que medir, no es un error)."""
    window = history.tail(lookback_years)
    ratios = _valid_ratio_series(window[column].tolist(), window["revenue"].tolist())
    if not ratios:
        raise ValueError(f"Sin ningún dato válido de '{column}' en el histórico disponible.")
    mean = statistics.mean(ratios)
    std = statistics.stdev(ratios) if len(ratios) > 1 else 0.0
    return mean, std


def historical_revenue_growth_stats(history: pd.DataFrame,
                                     lookback_years: Optional[int] = None) -> tuple[float, float]:
    """Media y desviación típica del crecimiento interanual de ingresos
    -- a diferencia del CAGR plano de `default_assumptions_from_history()`
    (un único punto, extremo a extremo), esto usa cada tasa año a año
    individual para medir cuánto varía de verdad el crecimiento de esta
    empresa, insumo para `engine.monte_carlo`.

    `lookback_years`: si se pasa, usa solo los últimos `lookback_years+1`
    puntos (mismo tamaño de ventana que `historical_ratio_stats()` para
    margen/CapEx) -- IMPORTANTE mantenerlo alineado en Monte Carlo:
    verificado con datos reales de AMZN (20+ años de histórico vía Alpha
    Vantage) que medir la dispersión sobre TODO el histórico mezcla la
    era de hiper-crecimiento inicial (2005-2010) con el régimen actual,
    infla la desviación típica muy por encima de la incertidumbre real
    del crecimiento reciente, y produce precios simulados negativos sin
    sentido económico. Si se omite (`None`), usa todo el histórico
    disponible -- comportamiento por defecto para cualquier otro
    consumidor de esta función que sí quiera la variabilidad completa.
    Requiere al menos 3 puntos de revenue no nulo en la ventana elegida
    (2 tasas de crecimiento) para que la desviación típica tenga
    sentido."""
    clean = history.dropna(subset=["revenue"])
    revenue = (clean.tail(lookback_years + 1) if lookback_years is not None else clean)["revenue"].tolist()
    if len(revenue) < 3:
        raise ValueError("Se necesitan al menos 3 años de revenue histórico para medir su dispersión.")
    growth_rates = [revenue[i] / revenue[i - 1] - 1 for i in range(1, len(revenue))]
    return statistics.mean(growth_rates), statistics.stdev(growth_rates)


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
    if initial_growth > EXTREME_FLAT_GROWTH_WARNING_THRESHOLD:
        terminal_year_multiple = (1 + initial_growth) ** n_years
        warnings.warn(
            f"Crecimiento de ingresos plano ({initial_growth:.0%}/año, el CAGR reciente real) "
            f"mantenido sin desacelerar durante los {n_years} años del horizonte explícito "
            f"(sección 14, verificado contra el Excel de referencia para crecimiento normal) "
            f"multiplica los ingresos por {terminal_year_multiple:.1f}x hacia el año {n_years} -- "
            "un ritmo sostenido excepcional incluso en hiper-crecimiento real (regla de pulgar, no "
            "una ley exacta: pocas compañías reales sostienen esto varios años seguidos sin "
            "desacelerar). El precio resultante puede salir económicamente implausible -- revisa "
            "si conviene modelar una desaceleración explícita en vez de aceptar el valor por defecto.",
            stacklevel=2,
        )

    margin_window = history.tail(lookback_years)
    if margin_window.empty or pd.isna(margin_window.iloc[-1].get("revenue")):
        raise ValueError("No hay datos suficientes en la ventana de márgenes")

    ebit_margin = _margin_fade_from_recent_to_average(margin_window, "ebit", "margen EBIT")
    da_pct = _margin_fade_from_recent_to_average(margin_window, "d_and_a", "D&A % ventas")
    capex_pct = _margin_fade_from_recent_to_average(margin_window, "capex", "CapEx % ventas")

    nwc_window = margin_window.dropna(subset=["change_in_nwc", "revenue"])
    nwc_fade = (_margin_fade_from_recent_to_average(nwc_window, "change_in_nwc", "ΔNWC % ventas")
                if len(nwc_window) > 0 else FadeAssumption(0.0, 0.0))

    tax_rate = margin_window["tax_rate"].dropna().mean()
    if pd.isna(tax_rate):
        raise ValueError("No hay tax_rate histórico disponible")
    if not (TAX_RATE_PLAUSIBLE_RANGE[0] <= tax_rate <= TAX_RATE_PLAUSIBLE_RANGE[1]):
        warnings.warn(
            f"tax_rate proyectado ({tax_rate:.1%}) está muy fuera de un rango plausible "
            f"({TAX_RATE_PLAUSIBLE_RANGE[0]:.0%} a {TAX_RATE_PLAUSIBLE_RANGE[1]:.0%}) -- se "
            "mantiene sin ajustar (hallazgo I15, docs/AUDIT.md), pero probablemente refleja un "
            "año con beneficio antes de impuestos cercano a cero dentro de la ventana histórica "
            "(reestructuración, cargo no recurrente, pérdidas), donde el ratio tax_provision/"
            "pretax_income se vuelve numéricamente inestable. Se proyecta PLANO (sin fade, "
            "decisión M6) durante todo el horizonte y el valor terminal -- revisa manualmente "
            "si conviene ajustar el escenario o ampliar `lookback_years`.",
            stacklevel=2,
        )

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


# ---------------------------------------------------------------------------
# Reverse DCF: crecimiento de ingresos implícito en el precio de mercado
# ---------------------------------------------------------------------------

@dataclass
class ImpliedGrowthResult:
    """implied_growth: la tasa de crecimiento plana (misma forma que
    default_assumptions_from_history(), ver docstring del módulo y
    METHODOLOGY.md sección 14) que reproduce target_price.
    assumed_growth: la que de verdad usa el escenario base, para poder
    comparar las dos directamente -- esa comparación es el output que le
    importa a un analista, no el número aislado."""
    implied_growth: float
    assumed_growth: float

    @property
    def gap(self) -> float:
        """implied - assumed. Positivo: el mercado exige más crecimiento
        del que asume el motor. Negativo: exige menos (el motor es más
        optimista que el precio de mercado)."""
        return self.implied_growth - self.assumed_growth


def implied_revenue_growth(last_actual_revenue: float, base_assumptions: ProjectionAssumptions,
                            dcf_kwargs: dict, target_price: float,
                            lower_bound: float = -0.30, upper_bound: float = 0.60) -> ImpliedGrowthResult:
    """Reverse DCF: dado un precio objetivo (típicamente el de mercado o
    el consenso de analistas), resuelve qué tasa de crecimiento de
    ingresos PLANA durante todo el horizonte explícito -- la misma forma
    que ya usa `default_assumptions_from_history()` -- reproduce ese
    precio con `engine.valuation.run_dcf()`, manteniendo fijo todo lo
    demás: márgenes, CapEx/D&A/ΔNWC (% de ventas), tipo impositivo, WACC,
    tasa de crecimiento terminal y múltiplo de salida.

    Es deliberadamente el mismo tipo de supuesto (crecimiento plano) que
    el motor ya usa por defecto, no un artificio ad hoc para "resolver
    hacia atrás" -- así el resultado se puede comparar directamente contra
    `base_assumptions.revenue_growth.start` en las mismas unidades.

    dcf_kwargs: el resto de argumentos de `DCFInputs` aparte de las series
    de proyección (wacc, terminal_growth_rate, stub_fraction, cash,
    total_debt, diluted_shares, terminal_ev_ebitda_multiple, gordon_weight).

    lower_bound/upper_bound: rango de búsqueda de la tasa de crecimiento
    (-30% a +60% por defecto). Si `target_price` queda fuera de ese rango
    -- p.ej. ni con un 60% de crecimiento anual se alcanza el precio de
    mercado -- `solve_for_target_price()` falla explícitamente con el
    precio real alcanzable en cada extremo, que es en sí mismo un dato
    informativo (cuantifica cuán grande es la brecha), no un error a
    esconder.
    """
    def price_at_growth(g: float) -> float:
        trial_assumptions = replace(base_assumptions, revenue_growth=FadeAssumption(g, g))
        projection = project_financials(last_actual_revenue, trial_assumptions)
        inputs = DCFInputs(
            ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
            capex=projection.capex, change_in_nwc=projection.change_in_nwc, **dcf_kwargs,
        )
        return run_dcf(inputs).implied_share_price

    implied = solve_for_target_price(price_at_growth, target_price, lower_bound, upper_bound)
    return ImpliedGrowthResult(
        implied_growth=implied,
        assumed_growth=base_assumptions.revenue_growth.start,
    )
