"""Escenarios de proyección con nombre (bear / base / bull), construidos
sobre engine.projections.default_assumptions_from_history().

Motivación (ver docs/METHODOLOGY.md secciones 7-9): el motor por defecto
asume reversión a la media, una postura razonada pero no universal. Un
único número no comunica eso — y las sesiones de validación mostraron
que ni "conservador" ni "agresivo" son etiquetas fiables sin contexto
(el mismo motor sale infravalorado en Big Tech y sobrevalorado en
algunas maduras, por mecanismos distintos). Este módulo no resuelve esa
ambigüedad con una heurística nueva: la hace EXPLÍCITA, ofreciendo 2-3
lecturas alternativas de los mismos datos históricos para que el
usuario/analista compare, en vez de recibir un solo número sin rango.

Ninguna transformación aquí inventa una cifra desde cero — todas parten
de `FadeAssumption(start, end)` ya derivado del histórico real.
"""

from dataclasses import dataclass, replace
from typing import Optional

import pandas as pd

from engine.projections import (
    FadeAssumption,
    ProjectionAssumptions,
    default_assumptions_from_history,
    project_financials,
)
from engine.valuation import DCFInputs, DCFResult, run_dcf


@dataclass
class Scenario:
    name: str
    description: str
    assumptions: ProjectionAssumptions


def _hold_at_start(fade: FadeAssumption) -> FadeAssumption:
    """Congela el driver en su valor de año 1 (el dato real más
    reciente) durante todo el horizonte — ni reversión a la media ni
    mejora continuada."""
    return FadeAssumption(start=fade.start, end=fade.start)


def _extrapolate_same_move(fade: FadeAssumption) -> FadeAssumption:
    """Proyecta hacia delante la MISMA magnitud de cambio que ya se
    observó del promedio histórico al nivel actual (start - end del fade
    conservador), en vez de revertirla. Si el margen ya subió X puntos
    frente a su media, el escenario alcista asume que sube X puntos más
    — no una cifra arbitraria, la misma magnitud ya observada."""
    already_moved = fade.start - fade.end
    return FadeAssumption(start=fade.start, end=fade.start + already_moved)


def conservative_scenario(base: ProjectionAssumptions) -> Scenario:
    """El valor por defecto de default_assumptions_from_history(): cada
    driver revierte a su media histórica de varios años. Es la hipótesis
    de que el nivel actual es temporal/cíclico."""
    return Scenario(
        name="Conservador (reversión a la media)",
        description=(
            "Margen EBIT, D&A, CapEx y ΔNWC convergen linealmente hacia "
            "su promedio de los últimos años. Asume que el nivel actual "
            "no se sostiene."
        ),
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
    observó (start - end del escenario conservador), en vez de
    revertirla. Solo se aplica al margen EBIT (el driver donde una
    tendencia de mejora real es más plausible); D&A/CapEx/ΔNWC quedan en
    su nivel actual (hold), no se asume que también mejoren sin límite."""
    bullish_margin = _extrapolate_same_move(base.ebit_margin)
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
        conservative_scenario(base),
        hold_current_scenario(base),
        bullish_scenario(base),
    ]


def run_scenarios(history: pd.DataFrame, wacc: float, cash: float, total_debt: float,
                   diluted_shares: float, n_years: int = 5, terminal_growth_rate: float = 0.025,
                   lookback_years: int = 3, terminal_ev_ebitda_multiple: Optional[float] = None,
                   gordon_weight: float = 1.0) -> dict[str, DCFResult]:
    """Corre run_dcf bajo los 3 escenarios por defecto sobre el mismo
    histórico. Devuelve {nombre_escenario: DCFResult}."""
    base = default_assumptions_from_history(
        history, n_years=n_years, terminal_growth_rate=terminal_growth_rate,
        lookback_years=lookback_years,
    )
    last_revenue = history["revenue"].iloc[-1]

    results = {}
    for scenario in default_scenarios(base):
        projection = project_financials(last_revenue, scenario.assumptions)
        inputs = DCFInputs(
            ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
            capex=projection.capex, change_in_nwc=projection.change_in_nwc,
            wacc=wacc, terminal_growth_rate=terminal_growth_rate,
            cash=cash, total_debt=total_debt, diluted_shares=diluted_shares,
            terminal_ev_ebitda_multiple=terminal_ev_ebitda_multiple, gordon_weight=gordon_weight,
        )
        results[scenario.name] = run_dcf(inputs)
    return results
