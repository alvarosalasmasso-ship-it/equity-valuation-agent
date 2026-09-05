"""Tests de engine/scenarios.py con un histórico sintético donde el
margen EBIT tiene una tendencia clara (0.05, 0.05, 0.10, 0.20), para
poder afirmar a mano el resultado exacto de cada escenario."""

import pandas as pd
import pytest

from engine.projections import default_assumptions_from_history
from engine.scenarios import (
    bullish_scenario,
    conservative_scenario,
    hold_current_scenario,
    run_scenarios,
)

# Histórico con tendencia de margen: último año real = 0.20, media de los
# últimos 3 años (0.05, 0.10, 0.20) = 0.1166...
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


def _base_assumptions():
    return default_assumptions_from_history(HISTORY, n_years=5, lookback_years=3)


def test_conservative_scenario_is_the_unmodified_default():
    base = _base_assumptions()
    scenario = conservative_scenario(base)
    assert scenario.assumptions is base
    assert scenario.assumptions.ebit_margin.start == pytest.approx(0.20)
    assert scenario.assumptions.ebit_margin.end == pytest.approx((0.05 + 0.10 + 0.20) / 3)


def test_hold_current_scenario_freezes_every_driver_at_year_one_value():
    base = _base_assumptions()
    scenario = hold_current_scenario(base)
    assert scenario.assumptions.ebit_margin.start == pytest.approx(0.20)
    assert scenario.assumptions.ebit_margin.end == pytest.approx(0.20)
    assert scenario.assumptions.da_pct_revenue.end == scenario.assumptions.da_pct_revenue.start
    assert scenario.assumptions.capex_pct_revenue.end == scenario.assumptions.capex_pct_revenue.start
    assert scenario.assumptions.nwc_change_pct_revenue.end == scenario.assumptions.nwc_change_pct_revenue.start
    # el crecimiento de ingresos NO se toca en este escenario
    assert scenario.assumptions.revenue_growth == base.revenue_growth


def test_bullish_scenario_extrapolates_same_magnitude_of_margin_move():
    base = _base_assumptions()
    scenario = bullish_scenario(base)
    already_moved = base.ebit_margin.start - base.ebit_margin.end  # 0.20 - 0.1167 = 0.0833
    assert scenario.assumptions.ebit_margin.start == pytest.approx(0.20)
    assert scenario.assumptions.ebit_margin.end == pytest.approx(0.20 + already_moved)
    assert scenario.assumptions.ebit_margin.end > 0.20  # sigue mejorando, no revierte
    # CapEx/D&A/NWC se mantienen (hold), no se extrapolan también
    assert scenario.assumptions.capex_pct_revenue.end == scenario.assumptions.capex_pct_revenue.start


def test_bullish_price_above_hold_above_conservative_when_margin_rising():
    """Con una tendencia de margen alcista, el orden de precios implícitos
    debe ser: conservador < mantener actual < alcista (invariante, no un
    valor exacto -- lo exacto ya se cubre en los tests de arriba)."""
    results = run_scenarios(
        HISTORY, wacc=0.09, cash=100, total_debt=50, diluted_shares=100,
        n_years=5, terminal_growth_rate=0.025, lookback_years=3,
    )
    conservative = results["Conservador (reversión a la media)"].implied_share_price
    hold = results["Mantener nivel actual"].implied_share_price
    bullish = results["Alcista (continúa la tendencia reciente)"].implied_share_price
    assert conservative < hold < bullish


def test_run_scenarios_returns_all_three_named_scenarios():
    results = run_scenarios(
        HISTORY, wacc=0.09, cash=100, total_debt=50, diluted_shares=100,
    )
    assert set(results.keys()) == {
        "Conservador (reversión a la media)",
        "Mantener nivel actual",
        "Alcista (continúa la tendencia reciente)",
    }
    assert all(r.implied_share_price > 0 for r in results.values())


def test_hold_scenario_equals_conservative_when_history_is_flat():
    """Si el histórico no tiene tendencia (margen constante), año1 == mediaN,
    así que conservador y mantener-actual deben coincidir exactamente."""
    flat_history = pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": REVENUE,
        "ebit": [200.0, 200.0, 200.0, 200.0],
        "d_and_a": [50.0] * 4,
        "capex": [80.0] * 4,
        "change_in_nwc": [None, 20.0, 20.0, 20.0],
        "tax_rate": [0.25] * 4,
    })
    results = run_scenarios(flat_history, wacc=0.09, cash=100, total_debt=50, diluted_shares=100)
    conservative = results["Conservador (reversión a la media)"].implied_share_price
    hold = results["Mantener nivel actual"].implied_share_price
    bullish = results["Alcista (continúa la tendencia reciente)"].implied_share_price
    assert conservative == pytest.approx(hold)
    assert hold == pytest.approx(bullish)  # sin tendencia, "seguir mejorando" tampoco cambia nada
