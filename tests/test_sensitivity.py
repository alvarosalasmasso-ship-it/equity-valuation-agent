"""Tests de engine/sensitivity.py, sobre el mismo histórico sintético
que tests/test_scenarios.py (margen EBIT con tendencia clara), para
poder afirmar a mano el signo y la magnitud relativa de cada driver."""

import pandas as pd
import pytest

from engine.sensitivity import DEFAULT_BUMP, driver_sensitivities

REVENUE = [1000.0, 1000.0, 1000.0, 1000.0]
HISTORY = pd.DataFrame({
    "fiscal_year": [2020, 2021, 2022, 2023],
    "revenue": REVENUE,
    "ebit": [50.0, 50.0, 100.0, 200.0],
    "d_and_a": [50.0, 50.0, 50.0, 50.0],
    "capex": [80.0, 80.0, 80.0, 80.0],
    "change_in_nwc": [None, 20.0, 20.0, 20.0],
    "tax_rate": [0.25] * 4,
})


def _run(**overrides):
    kwargs = dict(wacc=0.09, cash=100, total_debt=50, diluted_shares=100,
                  n_years=5, terminal_growth_rate=0.025, lookback_years=3)
    kwargs.update(overrides)
    return driver_sensitivities(HISTORY, **kwargs)


def _by_driver(results):
    return {r.driver: r for r in results}


def test_returns_all_six_drivers():
    results = _run()
    assert {r.driver for r in results} == {
        "wacc", "terminal_growth_rate", "ebit_margin",
        "capex_pct_revenue", "da_pct_revenue", "revenue_growth",
    }


def test_all_share_the_same_base_price():
    results = _run()
    base_prices = {r.base_price for r in results}
    assert len(base_prices) == 1  # todos parten del mismo escenario conservador


def test_sorted_by_descending_absolute_impact():
    results = _run()
    magnitudes = [abs(r.price_change_pct) for r in results]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_signs_match_dcf_mechanics():
    """Verificado a mano contra el propio cálculo de UFCF = EBIT*(1-t) +
    D&A - CapEx - ΔNWC y el descuento estándar: subir WACC o CapEx baja
    el precio; subir margen, D&A, g terminal o crecimiento de ingresos
    lo sube."""
    by_driver = _by_driver(_run())
    assert by_driver["wacc"].price_change_pct < 0
    assert by_driver["capex_pct_revenue"].price_change_pct < 0
    assert by_driver["ebit_margin"].price_change_pct > 0
    assert by_driver["da_pct_revenue"].price_change_pct > 0
    assert by_driver["terminal_growth_rate"].price_change_pct > 0
    assert by_driver["revenue_growth"].price_change_pct > 0


def test_capex_and_da_are_symmetric_in_this_simplified_ufcf_model():
    """CapEx y D&A entran en UFCF con el mismo coeficiente (revenue ×
    bump) y signo opuesto, sin ningún término que los compense --
    desplazar cualquiera de los dos +1pp mueve el precio la misma
    magnitud en direcciones opuestas. Confirma que el bump-and-reprice
    se aplica de forma simétrica, no una coincidencia de los datos."""
    by_driver = _by_driver(_run())
    assert by_driver["capex_pct_revenue"].price_change_pct == pytest.approx(
        -by_driver["da_pct_revenue"].price_change_pct, rel=1e-9
    )


def test_ebit_margin_dominates_over_revenue_growth_on_flat_synthetic_history():
    """El histórico sintético tiene crecimiento de ingresos plano (0%) y
    una tendencia de margen marcada -- el margen debe pesar mucho más
    que el crecimiento de ingresos en el ranking."""
    by_driver = _by_driver(_run())
    assert abs(by_driver["ebit_margin"].price_change_pct) > abs(by_driver["revenue_growth"].price_change_pct)


def test_custom_bump_scales_the_effect_price_change_roughly_linearly():
    """Un bump 5x mayor debe producir un efecto porcentual del mismo
    orden de magnitud mayor (el DCF no es perfectamente lineal en cada
    supuesto, pero para un bump pequeño la diferencia debe ser menor,
    no invertirse de signo ni desviarse por un orden de magnitud)."""
    base = _by_driver(_run(bump=0.01))["ebit_margin"]
    bigger = _by_driver(_run(bump=0.05))["ebit_margin"]
    ratio = bigger.price_change_pct / base.price_change_pct
    assert 4.0 < ratio < 6.0  # ~5x, con margen por la no linealidad


def test_default_bump_is_one_percentage_point():
    assert DEFAULT_BUMP == pytest.approx(0.01)


def test_terminal_ev_ebitda_multiple_and_gordon_weight_pass_through():
    """Con gordon_weight=0 (100% múltiplo de salida), la tasa de
    crecimiento terminal no debe tener ningún efecto sobre el precio --
    confirma que driver_sensitivities respeta gordon_weight en vez de
    ignorarlo."""
    results = _run(terminal_ev_ebitda_multiple=10.0, gordon_weight=0.0)
    by_driver = _by_driver(results)
    assert by_driver["terminal_growth_rate"].price_change_pct == pytest.approx(0.0, abs=1e-9)
