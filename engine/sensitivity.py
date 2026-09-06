"""Elasticidades del modelo (sesión 17): cuánto se mueve el precio
implícito por cada supuesto, uno a la vez.

Motivación (docs/METHODOLOGY.md secciones 7, 9, 10 y 21): hasta ahora,
"en MSFT domina el CapEx" o "en PG domina el spread WACC-g" eran
conclusiones alcanzadas a mano, desglosando el DCF caso por caso. Este
módulo sistematiza exactamente ese ejercicio: reutiliza el mismo pipeline
de `engine.scenarios.run_scenarios` (histórico -> supuestos -> proyección
-> DCF), pero en vez de comparar 3 escenarios con nombre, aplica un
desplazamiento (+1pp por defecto) a un único supuesto cada vez, manteniendo
el resto fijo en el escenario conservador, y mide el cambio resultante en
el precio implícito. Es el equivalente de una tabla de sensibilidades
("Greeks") de un DCF profesional.

Método: desplazamiento paralelo de TODO el tramo del fade (año 1 y año N
por igual), no solo del ancla -- responde a "¿y si este supuesto fuera
sistemáticamente 1pp más alto durante todo el horizonte?", no a "¿y si
solo el año 1 cambiara?". Para WACC y la tasa de crecimiento terminal, el
desplazamiento se aplica directamente sobre el escalar de DCFInputs.

No es un análisis de segundo orden (no captura interacciones entre
supuestos) ni una derivada analítica -- es bump-and-reprice numérico,
deliberadamente simple y verificable a mano, en línea con el resto del
proyecto ("Python puro", sin dependencias nuevas)."""

import warnings
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
from engine.valuation import DCFInputs, run_dcf

DEFAULT_BUMP = 0.01  # +1 punto porcentual, la convención estándar de una tabla de sensibilidades


@dataclass
class DriverSensitivity:
    """Efecto sobre el precio implícito de desplazar `driver` en `bump`
    (por defecto +1pp), manteniendo el resto de supuestos en el
    escenario conservador. `price_change_pct` es (precio con bump -
    precio base) / precio base -- su signo indica la dirección
    (positivo: el precio sube cuando el supuesto sube) y su magnitud,
    junto a la de los demás drivers, indica cuál domina la valoración de
    esta empresa en concreto."""
    driver: str
    label: str
    base_value: float
    bump: float
    base_price: float
    price_at_bump: float

    @property
    def price_change_pct(self) -> float:
        return (self.price_at_bump - self.base_price) / self.base_price


def driver_sensitivities(history: pd.DataFrame, wacc: float, cash: float, total_debt: float,
                          diluted_shares: float, n_years: int = 5, terminal_growth_rate: float = 0.025,
                          lookback_years: int = 3, terminal_ev_ebitda_multiple: Optional[float] = None,
                          gordon_weight: float = 1.0, valuation_date: Optional[date] = None,
                          bump: float = DEFAULT_BUMP) -> list[DriverSensitivity]:
    """Devuelve la sensibilidad del precio implícito a cada supuesto
    clave (WACC, tasa de crecimiento terminal, margen EBIT, D&A % ventas,
    CapEx % ventas, crecimiento de ingresos), ordenada de mayor a menor
    impacto absoluto. Mismos parámetros que engine.scenarios.run_scenarios
    -- reutiliza el mismo histórico y el mismo escenario conservador como
    caso base, así que el precio base coincide exactamente con el del
    escenario "Conservador" de run_scenarios (mismos inputs)."""
    base_assumptions = default_assumptions_from_history(history, n_years=n_years, lookback_years=lookback_years)
    last_revenue = history["revenue"].iloc[-1]
    stub_fraction = stub_fraction_from_history(history, valuation_date=valuation_date)

    def price_for(assumptions: ProjectionAssumptions, wacc_: float, terminal_growth_rate_: float) -> float:
        projection = project_financials(last_revenue, assumptions)
        inputs = DCFInputs(
            ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
            capex=projection.capex, change_in_nwc=projection.change_in_nwc,
            wacc=wacc_, terminal_growth_rate=terminal_growth_rate_, stub_fraction=stub_fraction,
            cash=cash, total_debt=total_debt, diluted_shares=diluted_shares,
            terminal_ev_ebitda_multiple=terminal_ev_ebitda_multiple, gordon_weight=gordon_weight,
        )
        return run_dcf(inputs).implied_share_price

    def shifted(fade: FadeAssumption, delta: float) -> FadeAssumption:
        return FadeAssumption(start=fade.start + delta, end=fade.end + delta)

    # Los avisos del modelo (outlier del ancla, spread WACC-g estrecho) ya
    # se capturan una vez sobre el escenario base en otro punto del
    # pipeline (engine.ai.memo_generator.run_scenarios_capturing_warnings);
    # repetirlos aquí por cada uno de los 6 bumps sería puro ruido
    # duplicado, no información nueva.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base_price = price_for(base_assumptions, wacc, terminal_growth_rate)

        driver_specs: list[tuple[str, str, float, float]] = [
            ("wacc", "WACC", wacc,
             price_for(base_assumptions, wacc + bump, terminal_growth_rate)),
            ("terminal_growth_rate", "Tasa de crecimiento terminal (g)", terminal_growth_rate,
             price_for(base_assumptions, wacc, terminal_growth_rate + bump)),
            ("ebit_margin", "Margen EBIT", base_assumptions.ebit_margin.start,
             price_for(replace(base_assumptions, ebit_margin=shifted(base_assumptions.ebit_margin, bump)),
                       wacc, terminal_growth_rate)),
            ("capex_pct_revenue", "CapEx % ventas", base_assumptions.capex_pct_revenue.start,
             price_for(replace(base_assumptions, capex_pct_revenue=shifted(base_assumptions.capex_pct_revenue, bump)),
                       wacc, terminal_growth_rate)),
            ("da_pct_revenue", "D&A % ventas", base_assumptions.da_pct_revenue.start,
             price_for(replace(base_assumptions, da_pct_revenue=shifted(base_assumptions.da_pct_revenue, bump)),
                       wacc, terminal_growth_rate)),
            ("revenue_growth", "Crecimiento de ingresos", base_assumptions.revenue_growth.start,
             price_for(replace(base_assumptions, revenue_growth=shifted(base_assumptions.revenue_growth, bump)),
                       wacc, terminal_growth_rate)),
        ]

    results = [
        DriverSensitivity(driver=key, label=label, base_value=base_value, bump=bump,
                           base_price=base_price, price_at_bump=bumped_price)
        for key, label, base_value, bumped_price in driver_specs
    ]
    results.sort(key=lambda r: abs(r.price_change_pct), reverse=True)
    return results
