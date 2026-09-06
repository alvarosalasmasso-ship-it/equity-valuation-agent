"""Tests de engine/monte_carlo.py con un histórico sintético (con algo
de variación real en margen/CapEx/crecimiento, para que la dispersión
muestreada no sea cero) y un generador de numpy con semilla fija, para
que los tests sean deterministas."""

import numpy as np
import pandas as pd
import pytest

from engine.monte_carlo import DEFAULT_TERMINAL_GROWTH_STD, MonteCarloResult, run_monte_carlo

REVENUE = [1000.0, 1100.0, 1210.0, 1331.0]
HISTORY = pd.DataFrame({
    "fiscal_year": [2020, 2021, 2022, 2023],
    "revenue": REVENUE,
    "ebit": [190.0, 210.0, 200.0, 220.0],
    "d_and_a": [50.0, 55.0, 60.0, 66.0],
    "capex": [80.0, 88.0, 96.0, 106.0],
    "change_in_nwc": [None, 20.0, 22.0, 24.0],
    "tax_rate": [0.25] * 4,
})


def _run(**overrides):
    kwargs = dict(
        history=HISTORY, wacc=0.09, cash=100, total_debt=200, diluted_shares=100,
        n_years=5, terminal_growth_rate=0.025, lookback_years=3, gordon_weight=1.0,
        n_simulations=1000, rng=np.random.default_rng(42),
    )
    kwargs.update(overrides)
    return run_monte_carlo(**kwargs)


def test_returns_monte_carlo_result_with_ordered_percentiles():
    result = _run()
    assert isinstance(result, MonteCarloResult)
    assert result.p10 < result.p50 < result.p90
    assert result.successful_simulations + result.failed_simulations == result.n_simulations


def test_all_simulations_succeed_on_a_healthy_wacc_g_spread():
    result = _run()
    assert result.failed_simulations == 0
    assert result.successful_simulations == 1000
    assert len(result.prices) == 1000


def test_mean_and_std_computed_over_successful_prices_only():
    result = _run()
    assert result.mean == pytest.approx(sum(result.prices) / len(result.prices))
    assert result.std > 0.0  # hay dispersión real, no todos los draws dan el mismo precio


def test_is_reproducible_with_the_same_seed():
    """Mismo rng con la misma semilla -> mismos resultados exactos --
    importante para que un test de regresión sea determinista."""
    r1 = _run(rng=np.random.default_rng(7))
    r2 = _run(rng=np.random.default_rng(7))
    assert r1.p50 == pytest.approx(r2.p50, rel=1e-9)
    assert r1.prices == pytest.approx(r2.prices, rel=1e-9)


def test_different_seeds_give_different_but_similar_distributions():
    r1 = _run(rng=np.random.default_rng(1))
    r2 = _run(rng=np.random.default_rng(2))
    assert r1.p50 != pytest.approx(r2.p50, rel=1e-9)  # distintas simulaciones concretas
    assert r1.p50 == pytest.approx(r2.p50, rel=0.15)  # pero la misma distribución subyacente


def test_capex_draws_are_clipped_at_zero():
    """CapEx % ventas no puede salir negativo de un draw -- a diferencia
    de margen/crecimiento, que sí pueden. Fuerza una desviación típica
    de CapEx artificialmente grande para que algunos draws intenten
    cruzar cero, y confirma que ninguna simulación fallida se debe a
    CapEx negativo en unlevered_fcf (el motor no lo rechazaría, pero un
    CapEx negativo real sería un dato sin sentido económico)."""
    history = pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": REVENUE,
        "ebit": [190.0, 210.0, 200.0, 220.0],
        "d_and_a": [50.0, 55.0, 60.0, 66.0],
        "capex": [1.0, 50.0, 5.0, 90.0],  # dispersión grande a propósito
        "change_in_nwc": [None, 20.0, 22.0, 24.0],
        "tax_rate": [0.25] * 4,
    })
    result = run_monte_carlo(history, wacc=0.09, cash=100, total_debt=200, diluted_shares=100,
                              n_years=5, terminal_growth_rate=0.025, lookback_years=3,
                              gordon_weight=1.0, n_simulations=500, rng=np.random.default_rng(3))
    assert result.successful_simulations == 500  # ninguna simulación crashea por CapEx negativo


def test_negative_implied_prices_are_floored_at_zero_not_discarded():
    """Auditoría sesión 17: verificado con AMZN real que, sin este
    suelo, algunos draws (margen bajo + CapEx alto a la vez) dan un
    precio implícito negativo -- matemáticamente consistente con la
    fórmula (mismo mecanismo que I10, docs/AUDIT.md), pero sin sentido
    económico para un accionista de responsabilidad limitada (nunca
    pierde MÁS que su inversión). Se flotan a $0, no se descartan --
    a diferencia de un draw que falla (wacc<=g), este SÍ cuenta como
    simulación exitosa, solo con precio $0."""
    # Dispersión de margen/CapEx muy grande a propósito, para forzar
    # algún draw con margen muy bajo + CapEx muy alto a la vez.
    history = pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": REVENUE,
        "ebit": [10.0, 400.0, 20.0, 380.0],  # margen: 1%, 36%, 1.7%, 28.5% -- dispersión enorme
        "d_and_a": [50.0, 55.0, 60.0, 66.0],
        "capex": [10.0, 300.0, 15.0, 280.0],  # CapEx: 1%, 27%, 1.2%, 21% -- dispersión enorme
        "change_in_nwc": [None, 20.0, 22.0, 24.0],
        "tax_rate": [0.25] * 4,
    })
    result = run_monte_carlo(history, wacc=0.09, cash=50, total_debt=800, diluted_shares=100,
                              n_years=5, terminal_growth_rate=0.025, lookback_years=3,
                              gordon_weight=1.0, n_simulations=2000, rng=np.random.default_rng(11))
    assert min(result.prices) == pytest.approx(0.0)
    assert all(p >= 0.0 for p in result.prices)
    # las simulaciones con precio 0 cuentan como éxito, no como fallo
    assert result.successful_simulations == 2000
    assert result.failed_simulations == 0


def test_default_terminal_growth_std_is_half_a_point():
    assert DEFAULT_TERMINAL_GROWTH_STD == pytest.approx(0.005)


def test_raises_when_all_simulations_fail():
    """WACC por debajo de la media de g terminal, con una desviación
    típica pequeña -> prácticamente todos los draws de g terminal caen
    por encima de wacc, forzando el fallo en todas las simulaciones --
    debe fallar explícito, no devolver una distribución vacía o
    inventada."""
    with pytest.raises(ValueError, match="fallaron todas"):
        run_monte_carlo(HISTORY, wacc=0.01, cash=100, total_debt=200, diluted_shares=100,
                         n_years=5, terminal_growth_rate=0.025, lookback_years=3,
                         gordon_weight=1.0, n_simulations=200, terminal_growth_std=0.001,
                         rng=np.random.default_rng(9))
