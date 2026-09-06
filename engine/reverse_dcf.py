"""Orquesta el reverse DCF (expectativas implícitas del mercado) a partir
de las piezas ya validadas de `engine.projections` y `engine.valuation` --
igual que `engine.wacc_builder` orquesta el WACC a partir de las piezas de
`engine.valuation`, sin duplicar ninguna fórmula ni introducir una segunda
fuente de verdad.

Un DCF hacia delante responde "¿qué precio justifican mis supuestos?".
Este módulo responde la pregunta complementaria, tan estándar en equity
research como la primera: "¿qué tendría que ser cierto para justificar el
precio que YA cotiza el mercado (o el consenso de analistas)?". La brecha
entre el precio del escenario conservador y el de mercado no es un error
del modelo a esconder -- es la prima de crecimiento que el mercado está
pagando hoy, cuantificada en vez de solo mostrada como un % de desviación
(ver docs/METHODOLOGY.md sección 20 y docs/PROGRESS_REVIEW.md).
"""

import warnings
from dataclasses import dataclass
from typing import Optional

from engine.projections import ProjectionAssumptions, implied_revenue_growth
from engine.valuation import DCFInputs, implied_terminal_growth_rate


@dataclass
class ImpliedExpectations:
    """Expectativas de mercado implícitas en un precio objetivo, con la
    comparación directa contra lo que asume el escenario conservador.

    Un campo `implied_*` en `None` significa que ese precio queda fuera
    del rango de búsqueda del solver correspondiente -- ver
    `engine.valuation.solve_for_target_price` -- no que el cálculo haya
    fallado en silencio. Es en sí mismo un resultado informativo (la
    brecha es tan grande que ni el extremo del rango la explica).
    """
    target_label: str
    target_price: float
    assumed_revenue_growth: float
    assumed_terminal_growth: float
    implied_revenue_growth: Optional[float] = None
    implied_terminal_growth: Optional[float] = None
    terminal_growth_fragile: bool = False

    @property
    def revenue_growth_gap(self) -> Optional[float]:
        """implied - assumed. Positivo: el mercado exige más crecimiento
        del que asume el motor. None si implied_revenue_growth no se pudo
        resolver."""
        if self.implied_revenue_growth is None:
            return None
        return self.implied_revenue_growth - self.assumed_revenue_growth


def compute_implied_expectations(
    last_actual_revenue: float,
    assumptions: ProjectionAssumptions,
    base_inputs: DCFInputs,
    reverse_dcf_kwargs: dict,
    targets: list[tuple[str, Optional[float]]],
) -> list[ImpliedExpectations]:
    """targets: lista de (etiqueta, precio) -- p.ej. [("Mercado", 258.51),
    ("Consenso analistas", 328.17)]. Los precios `None`/0 se ignoran (no
    todos los tickers tienen consenso de analistas disponible).

    reverse_dcf_kwargs: mismos argumentos que necesita `DCFInputs` aparte
    de las series de proyección (wacc, terminal_growth_rate, stub_fraction,
    cash, total_debt, diluted_shares, terminal_ev_ebitda_multiple,
    gordon_weight) -- se pasan tal cual a `implied_revenue_growth`.

    Cuando un precio queda fuera del rango de búsqueda de cualquiera de
    los dos solvers, ese campo concreto queda en `None` -- no se descarta
    el target entero por un único solver sin solución en rango.
    """
    results = []
    for label, price in targets:
        if not price:
            continue

        result = ImpliedExpectations(
            target_label=label, target_price=float(price),
            assumed_revenue_growth=assumptions.revenue_growth.start,
            assumed_terminal_growth=base_inputs.terminal_growth_rate,
        )

        try:
            growth_result = implied_revenue_growth(
                last_actual_revenue, assumptions, reverse_dcf_kwargs, price,
            )
            result.implied_revenue_growth = growth_result.implied_growth
        except (ValueError, RuntimeError):
            pass  # fuera de rango -- ver docstring de ImpliedExpectations

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result.implied_terminal_growth = implied_terminal_growth_rate(base_inputs, price)
            result.terminal_growth_fragile = bool(caught)
        except ValueError:
            pass  # gordon_weight<=0 sin efecto, o fuera de rango

        results.append(result)
    return results
