"""Simulación de Monte Carlo (sesión 17, Lote C): bandas de confianza
probabilísticas (P10/P50/P90) sobre el precio implícito, en vez de un
único número puntual + 3 escenarios con nombre.

Motivación: hasta ahora la herramienta comunica incertidumbre con
`engine.scenarios` (3 lecturas alternativas con nombre) y
`engine.sensitivity` (impacto de mover un supuesto a la vez). Ninguna
de las dos da una distribución real -- Monte Carlo sí: muestrea varios
supuestos A LA VEZ desde su propia dispersión histórica real (no un
rango inventado) y corre el DCF completo cientos de veces, dando una
distribución de la que se puede leer directamente "¿qué probabilidad
hay de que el precio caiga en tal rango?".

Alcance deliberado, documentado, no un descuido:
- Se muestrean 4 supuestos: margen EBIT (destino del fade), CapEx %
  ventas (destino del fade), crecimiento de ingresos (la tasa plana) y
  tasa de crecimiento terminal g. Cada uno desde Normal(media, sigma)
  con media/sigma medidos del propio histórico real de la empresa
  (`engine.projections.historical_ratio_stats`/
  `historical_revenue_growth_stats`), excepto g (macro, sin histórico
  propio de la empresa -- sigma fijo y pequeño, documentado como tal).
- WACC y D&A/ΔNWC se mantienen FIJOS en su valor puntual -- randomizar
  WACC exigiría una distribución de beta justificada (dispersión de
  peers, no siempre disponible en modo "cualquier ticker") y D&A/ΔNWC
  ya se ha visto que dominan menos la sensibilidad que margen/CapEx
  (`engine.sensitivity`, sección 22). Ampliarlo es una extensión
  futura, no un hueco escondido.
- Los 4 supuestos se tratan como INDEPENDIENTES entre sí (sin modelar
  correlación) -- simplificación estándar en Monte Carlo DCF de la
  práctica real, documentada como tal (en la realidad, margen y CapEx
  suelen moverse algo correlacionados con el ciclo macro).
- Draws que producen wacc<=g (tras el desplazamiento de g) o cualquier
  otro ValueError de run_dcf se descartan y se cuentan, nunca se dejan
  pasar como un precio inventado.
"""

import statistics
import warnings
from dataclasses import dataclass, replace
from typing import Optional

import pandas as pd

from engine.projections import (
    FadeAssumption,
    default_assumptions_from_history,
    historical_ratio_stats,
    historical_revenue_growth_stats,
    project_financials,
    stub_fraction_from_history,
)
from engine.valuation import DCFInputs, run_dcf

DEFAULT_N_SIMULATIONS = 2000
DEFAULT_TERMINAL_GROWTH_STD = 0.005  # ±0.5pp -- incertidumbre macro, no de la empresa


@dataclass
class MonteCarloResult:
    n_simulations: int
    successful_simulations: int
    failed_simulations: int  # draws descartados (wacc<=g u otro ValueError), nunca inventados
    prices: list[float]
    p10: float
    p50: float
    p90: float
    mean: float
    std: float


def run_monte_carlo(history: pd.DataFrame, wacc: float, cash: float, total_debt: float,
                     diluted_shares: float, n_years: int = 5, terminal_growth_rate: float = 0.025,
                     lookback_years: int = 3, terminal_ev_ebitda_multiple: Optional[float] = None,
                     gordon_weight: float = 1.0, stub_fraction: float = 1.0,
                     n_simulations: int = DEFAULT_N_SIMULATIONS,
                     terminal_growth_std: float = DEFAULT_TERMINAL_GROWTH_STD,
                     rng: Optional["np.random.Generator"] = None) -> MonteCarloResult:  # noqa: F821
    """Corre `n_simulations` DCF completos, muestreando margen EBIT,
    CapEx % ventas, crecimiento de ingresos y g terminal desde su propia
    dispersión histórica real (WACC y D&A/ΔNWC fijos -- ver docstring
    del módulo). Devuelve la distribución resultante de precios
    implícitos, con P10/P50/P90 y el recuento de draws descartados.

    `rng`: generador de numpy inyectable (`numpy.random.default_rng(seed)`)
    para tests deterministas; por defecto uno sin semilla fija."""
    import numpy as np

    if rng is None:
        rng = np.random.default_rng()

    base_assumptions = default_assumptions_from_history(history, n_years=n_years, lookback_years=lookback_years)
    last_revenue = history["revenue"].iloc[-1]

    margin_mean, margin_std = historical_ratio_stats(history, "ebit", lookback_years)
    capex_mean, capex_std = historical_ratio_stats(history, "capex", lookback_years)
    # Misma ventana que margen/CapEx (lookback_years) -- no toda la
    # historia disponible, que mezclaría regímenes de crecimiento muy
    # distintos en una empresa con mucho histórico (ver docstring de
    # historical_revenue_growth_stats).
    growth_mean, growth_std = historical_revenue_growth_stats(history, lookback_years=lookback_years)

    margin_draws = rng.normal(margin_mean, margin_std, n_simulations) if margin_std > 0 else np.full(n_simulations, margin_mean)
    capex_draws = rng.normal(capex_mean, capex_std, n_simulations) if capex_std > 0 else np.full(n_simulations, capex_mean)
    growth_draws = rng.normal(growth_mean, growth_std, n_simulations) if growth_std > 0 else np.full(n_simulations, growth_mean)
    terminal_growth_draws = rng.normal(terminal_growth_rate, terminal_growth_std, n_simulations)
    # CapEx % ventas no puede ser negativo (no hay "CapEx negativo" con sentido económico);
    # margen y crecimiento SÍ pueden salir negativos en un draw (una pérdida real es posible).
    capex_draws = np.clip(capex_draws, 0.0, None)

    prices: list[float] = []
    failed = 0
    with warnings.catch_warnings():
        # Los avisos de calidad (outlier, spread WACC-g estrecho) ya se
        # muestran una vez sobre el escenario base en otro punto del
        # pipeline -- repetirlos por cada uno de los n_simulations draws
        # sería puro ruido, no información nueva.
        warnings.simplefilter("ignore")
        for i in range(n_simulations):
            assumptions_i = replace(
                base_assumptions,
                ebit_margin=FadeAssumption(start=base_assumptions.ebit_margin.start, end=float(margin_draws[i])),
                capex_pct_revenue=FadeAssumption(start=base_assumptions.capex_pct_revenue.start, end=float(capex_draws[i])),
                revenue_growth=FadeAssumption(start=float(growth_draws[i]), end=float(growth_draws[i])),
            )
            projection = project_financials(last_revenue, assumptions_i)
            inputs = DCFInputs(
                ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
                capex=projection.capex, change_in_nwc=projection.change_in_nwc, wacc=wacc,
                terminal_growth_rate=float(terminal_growth_draws[i]), stub_fraction=stub_fraction,
                cash=cash, total_debt=total_debt, diluted_shares=diluted_shares,
                terminal_ev_ebitda_multiple=terminal_ev_ebitda_multiple, gordon_weight=gordon_weight,
            )
            try:
                result = run_dcf(inputs)
            except ValueError:
                failed += 1
                continue
            # Suelo en $0, no descarte: la responsabilidad limitada del
            # equity significa que un accionista nunca pierde MÁS que su
            # inversión -- un valor de equity negativo en un draw no es
            # "el precio real", es la forma en que la fórmula de Gordon
            # Growth expresa un escenario de destrucción total de valor
            # (mismo mecanismo que I10, docs/AUDIT.md, con TSLA/BA reales).
            # Verificado con datos reales: sin este suelo, AMZN daba un
            # P10 de -$34 -- un "precio" negativo que no corresponde a
            # nada real y solo confundiría la distribución, no la
            # explicaría. Deliberadamente NO se aplica en run_dcf() ni en
            # los escenarios con nombre (ahí un TV negativo real, ver
            # I10, es información diagnóstica útil de un caso concreto;
            # aquí se agregan miles de draws en una distribución, donde
            # "precio negativo" solo rompe la lectura, no aporta nada que
            # el propio P10 no cuente ya con su magnitud real.
            prices.append(max(0.0, result.implied_share_price))

    if not prices:
        raise ValueError(
            f"Las {n_simulations} simulaciones fallaron todas (wacc<=g en cada draw de g terminal) "
            "-- reduce terminal_growth_std o revisa el margen WACC-g del caso base."
        )

    sorted_prices = sorted(prices)
    return MonteCarloResult(
        n_simulations=n_simulations,
        successful_simulations=len(prices),
        failed_simulations=failed,
        prices=prices,
        p10=float(np.percentile(sorted_prices, 10)),
        p50=float(np.percentile(sorted_prices, 50)),
        p90=float(np.percentile(sorted_prices, 90)),
        mean=statistics.mean(prices),
        std=statistics.stdev(prices) if len(prices) > 1 else 0.0,
    )
