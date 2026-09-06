"""Escenarios de proyección con nombre (bear / base / bull), construidos
sobre engine.projections.default_assumptions_from_history().

Motivación (ver docs/METHODOLOGY.md secciones 7-9 y 33): el motor por
defecto asume reversión a la media SALVO evidencia objetiva de una
tendencia estructural real (sesión 17, hallazgo I16) — de cualquier
forma, una postura razonada pero no universal. Un único número no
comunica eso — y las sesiones de validación mostraron que ni "base"
ni "agresivo" son etiquetas fiables sin contexto (el mismo motor sale
infravalorado en Big Tech y sobrevalorado en algunas maduras, por
mecanismos distintos). Este módulo no resuelve esa ambigüedad con una
heurística nueva: la hace EXPLÍCITA, ofreciendo 2-3 lecturas
alternativas de los mismos datos históricos para que el
usuario/analista compare, en vez de recibir un solo número sin rango.

Ninguna transformación aquí inventa una cifra desde cero — todas parten
de `FadeAssumption(start, end)` ya derivado del histórico real.
"""

from dataclasses import dataclass, replace
from datetime import date
from typing import Optional

import pandas as pd

from engine.projections import (
    FadeAssumption,
    ProjectionAssumptions,
    default_assumptions_from_history,
    project_financials,
    stub_fraction_from_history,
)
from engine.valuation import DCFInputs, DCFResult, run_dcf


@dataclass
class Scenario:
    name: str
    description: str
    assumptions: ProjectionAssumptions


BASE_SCENARIO_NAME = "Base (histórico)"

# Sesión 17 (hallazgo I16): antes se llamaba "Conservador (reversión a
# la media)" -- desde que default_assumptions_from_history() puede
# mantener un driver en su nivel actual en vez de revertir (cuando hay
# evidencia objetiva de tendencia estructural, ver engine.projections),
# ese nombre dejó de ser cierto para todos los drivers a la vez: no es
# uniformemente "conservador" (para AMZN, mantener el CapEx en su nivel
# actual es MÁS bajista para el precio que revertirlo a la baja, ver
# docstring de hold_current_scenario), y ya no siempre "revierte a la
# media". Nombre neutral, agnóstico del mecanismo -- la descripción
# real (qué driver revierte y cuál se mantiene, y por qué) se genera
# dinámicamente en vez de afirmarse una vez y poder quedar desactualizada.
_DRIVER_LABELS = {
    "ebit_margin": "Margen EBIT", "da_pct_revenue": "D&A % ventas",
    "capex_pct_revenue": "CapEx % ventas", "nwc_change_pct_revenue": "ΔNWC % ventas",
}


def _describe_base_scenario(base: ProjectionAssumptions) -> str:
    """Genera la descripción driver por driver desde driver_trend_info
    -- nunca hand-written, para que no pueda quedar desactualizada frente
    a lo que el escenario realmente hace (el problema que tenía el texto
    fijo anterior)."""
    clauses = []
    for field_name, label in _DRIVER_LABELS.items():
        trend = base.driver_trend_info.get(field_name)
        fade = getattr(base, field_name)
        if trend is not None and trend.is_override:
            clauses.append(
                f"{label}: se mantiene en el nivel actual ({fade.end:.1%}, tendencia "
                f"estructural detectada, R²={trend.r_squared:.2f})"
            )
        else:
            clauses.append(f"{label}: revierte hacia su media histórica ({fade.end:.1%})")
    return ". ".join(clauses) + "."


def _hold_at_start(fade: FadeAssumption) -> FadeAssumption:
    """Congela el driver en su valor de año 1 (el dato real más
    reciente) durante todo el horizonte — ni reversión a la media ni
    mejora continuada."""
    return FadeAssumption(start=fade.start, end=fade.start)


def _extrapolate_same_move(fade: FadeAssumption, historical_mean: float) -> FadeAssumption:
    """Proyecta hacia delante la MISMA magnitud de cambio que ya se
    observó del promedio histórico REAL (`historical_mean`) al nivel
    actual, en vez de revertirla. Si el margen ya subió X puntos frente
    a su media, el escenario alcista asume que sube X puntos más — no
    una cifra arbitraria, la misma magnitud ya observada.

    Recibe `historical_mean` explícito, no `fade.end` (sesión 17,
    hallazgo I16): desde que default_assumptions_from_history() puede
    sobrescribir `end` a `start` cuando hay una tendencia estructural
    detectada, usar `fade.end` aquí colapsaría "alcista" en un
    duplicado silencioso de `_hold_at_start()` -- exactamente para las
    empresas (AMZN, MSFT...) que motivaron este cambio."""
    already_moved = fade.start - historical_mean
    return FadeAssumption(start=fade.start, end=fade.start + already_moved)


def base_scenario(base: ProjectionAssumptions) -> Scenario:
    """El valor por defecto de default_assumptions_from_history(): cada
    driver revierte a su media histórica de varios años, SALVO que haya
    evidencia objetiva de una tendencia estructural real (R² de un
    ajuste lineal, ver engine.projections), en cuyo caso se mantiene en
    su nivel actual en vez de revertir contra la tendencia."""
    return Scenario(
        name=BASE_SCENARIO_NAME,
        description=_describe_base_scenario(base),
        assumptions=base,
    )


def hold_current_scenario(base: ProjectionAssumptions) -> Scenario:
    """Margen, D&A, CapEx y ΔNWC se mantienen en el nivel real del
    último ejercicio fiscal durante todo el horizonte — la hipótesis de
    que el estado actual YA es el nuevo normal, sin apostar a que además
    siga mejorando.

    Aviso (no intuitivo, verificado con AMZN): este escenario puede dar
    un precio MENOR que el conservador si la compañía tiene un CapEx
    actual excepcionalmente alto (p.ej. un supercycle de inversión) — al
    congelar el CapEx en su nivel actual en vez de dejarlo revertir a la
    baja hacia el promedio, el lastre de CapEx puede superar la mejora
    de margen. No es un error: es información real sobre qué supuesto
    domina la sensibilidad del valor en esa compañía concreta. Ver
    docs/METHODOLOGY.md sección 10."""
    held = replace(
        base,
        ebit_margin=_hold_at_start(base.ebit_margin),
        da_pct_revenue=_hold_at_start(base.da_pct_revenue),
        capex_pct_revenue=_hold_at_start(base.capex_pct_revenue),
        nwc_change_pct_revenue=_hold_at_start(base.nwc_change_pct_revenue),
    )
    return Scenario(
        name="Mantener nivel actual",
        description=(
            "Margen EBIT, D&A, CapEx y ΔNWC se mantienen en su valor del "
            "último ejercicio real. Asume que el nivel actual es el "
            "nuevo estado estable, sin revertir ni seguir mejorando."
        ),
        assumptions=held,
    )


def bullish_scenario(base: ProjectionAssumptions) -> Scenario:
    """Extrapola hacia delante la misma mejora de margen que ya se
    observó (start - media histórica real), en vez de revertirla. Solo
    se aplica al margen EBIT (el driver donde una tendencia de mejora
    real es más plausible); D&A/CapEx/ΔNWC quedan en su nivel actual
    (hold), no se asume que también mejoren sin límite."""
    margin_trend = base.driver_trend_info.get("ebit_margin")
    # Fallback a base.ebit_margin.end para ProjectionAssumptions
    # construidos a mano sin driver_trend_info (p.ej. en tests) --
    # ahí end sigue siendo literalmente la media histórica, como antes.
    historical_mean = margin_trend.historical_mean if margin_trend is not None else base.ebit_margin.end
    bullish_margin = _extrapolate_same_move(base.ebit_margin, historical_mean)
    return Scenario(
        name="Alcista (continúa la tendencia reciente)",
        description=(
            "El margen EBIT sigue mejorando la misma magnitud que ya "
            "mejoró frente a su promedio histórico. D&A/CapEx/ΔNWC se "
            "mantienen en su nivel actual."
        ),
        assumptions=replace(
            base,
            ebit_margin=bullish_margin,
            da_pct_revenue=_hold_at_start(base.da_pct_revenue),
            capex_pct_revenue=_hold_at_start(base.capex_pct_revenue),
            nwc_change_pct_revenue=_hold_at_start(base.nwc_change_pct_revenue),
        ),
    )


def default_scenarios(base: ProjectionAssumptions) -> list[Scenario]:
    return [
        base_scenario(base),
        hold_current_scenario(base),
        bullish_scenario(base),
    ]


def run_scenarios(history: pd.DataFrame, wacc: float, cash: float, total_debt: float,
                   diluted_shares: float, n_years: int = 5, terminal_growth_rate: float = 0.025,
                   lookback_years: int = 3, terminal_ev_ebitda_multiple: Optional[float] = None,
                   gordon_weight: float = 1.0, valuation_date: Optional[date] = None) -> dict[str, DCFResult]:
    """Corre run_dcf bajo los 3 escenarios por defecto sobre el mismo
    histórico. Devuelve {nombre_escenario: DCFResult}.

    terminal_growth_rate se pasa solo a DCFInputs (valor terminal) — el
    crecimiento de ingresos del horizonte explícito ya no depende de él,
    ver default_assumptions_from_history().

    stub_fraction (auditoría sesión 15, hallazgo C1) se calcula a partir
    del cierre de ejercicio fiscal real del último año de `history` y de
    `valuation_date` (por defecto, hoy) — no se asume ya "1 de enero del
    primer año proyectado" de forma implícita."""
    base = default_assumptions_from_history(
        history, n_years=n_years, lookback_years=lookback_years,
    )
    last_revenue = history["revenue"].iloc[-1]
    stub_fraction = stub_fraction_from_history(history, valuation_date=valuation_date)

    results = {}
    for scenario in default_scenarios(base):
        projection = project_financials(last_revenue, scenario.assumptions)
        inputs = DCFInputs(
            ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
            capex=projection.capex, change_in_nwc=projection.change_in_nwc,
            wacc=wacc, terminal_growth_rate=terminal_growth_rate, stub_fraction=stub_fraction,
            cash=cash, total_debt=total_debt, diluted_shares=diluted_shares,
            terminal_ev_ebitda_multiple=terminal_ev_ebitda_multiple, gordon_weight=gordon_weight,
        )
        results[scenario.name] = run_dcf(inputs)
    return results
