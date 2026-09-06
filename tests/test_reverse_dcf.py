"""Tests de engine/reverse_dcf.py: orquesta implied_revenue_growth()
(engine.projections) e implied_terminal_growth_rate() (engine.valuation)
en un único bundle -- reutiliza el histórico sintético de
tests/test_projections.py para no duplicar fixtures financieras."""

import pandas as pd
import pytest

from engine.projections import default_assumptions_from_history, project_financials
from engine.reverse_dcf import ImpliedExpectations, compute_implied_expectations
from engine.valuation import DCFInputs, run_dcf


def _synthetic_flat_history() -> pd.DataFrame:
    revenue = [1000.0, 1100.0, 1210.0, 1331.0]
    return pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": revenue,
        "ebit": [r * 0.20 for r in revenue],
        "d_and_a": [r * 0.05 for r in revenue],
        "capex": [r * 0.08 for r in revenue],
        "change_in_nwc": [None] + [r * 0.02 for r in revenue[1:]],
        "tax_rate": [0.25, 0.25, 0.25, 0.25],
    })


def _setup():
    history = _synthetic_flat_history()
    assumptions = default_assumptions_from_history(history, n_years=5, lookback_years=3)
    last_revenue = history["revenue"].iloc[-1]
    kwargs = dict(
        wacc=0.09, terminal_growth_rate=0.025, cash=100, total_debt=50,
        diluted_shares=100, terminal_ev_ebitda_multiple=None, gordon_weight=1.0,
    )
    projection = project_financials(last_revenue, assumptions)
    base_inputs = DCFInputs(
        ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
        capex=projection.capex, change_in_nwc=projection.change_in_nwc, **kwargs,
    )
    return last_revenue, assumptions, base_inputs, kwargs


def test_compute_implied_expectations_skips_missing_prices():
    last_revenue, assumptions, base_inputs, kwargs = _setup()
    results = compute_implied_expectations(
        last_revenue, assumptions, base_inputs, kwargs,
        targets=[("Mercado", None), ("Consenso analistas", 0)],
    )
    assert results == []


def test_compute_implied_expectations_round_trip_on_base_price():
    """Si el precio objetivo es el que produce el propio escenario base,
    tanto el crecimiento implícito como la g terminal implícita deben
    recuperar los valores asumidos -- ida y vuelta."""
    last_revenue, assumptions, base_inputs, kwargs = _setup()
    base_price = run_dcf(base_inputs).implied_share_price

    results = compute_implied_expectations(
        last_revenue, assumptions, base_inputs, kwargs,
        targets=[("Mercado", base_price)],
    )
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, ImpliedExpectations)
    assert result.target_label == "Mercado"
    assert result.target_price == pytest.approx(base_price)
    assert result.implied_revenue_growth == pytest.approx(0.10, abs=1e-4)
    assert result.implied_terminal_growth == pytest.approx(0.025, abs=1e-4)
    assert result.revenue_growth_gap == pytest.approx(0.0, abs=1e-4)
    assert result.terminal_growth_fragile is False


def test_compute_implied_expectations_marks_unsolvable_target_as_none_not_error():
    """Un precio inalcanzable en el rango de implied_revenue_growth no debe
    tirar toda la función abajo -- el campo queda en None, informativo."""
    last_revenue, assumptions, base_inputs, kwargs = _setup()
    results = compute_implied_expectations(
        last_revenue, assumptions, base_inputs, kwargs,
        targets=[("Precio absurdo", 1_000_000.0)],
    )
    assert len(results) == 1
    assert results[0].implied_revenue_growth is None
    assert results[0].revenue_growth_gap is None


def test_compute_implied_expectations_flags_fragile_terminal_growth():
    """Un precio objetivo que solo se explica con un g muy cercano al WACC
    debe marcar terminal_growth_fragile=True (mismo aviso de spread
    estrecho que el resto del motor)."""
    last_revenue, assumptions, base_inputs, kwargs = _setup()
    from dataclasses import replace
    high_g_inputs = replace(base_inputs, terminal_growth_rate=0.07)
    very_high_price = run_dcf(high_g_inputs).implied_share_price

    results = compute_implied_expectations(
        last_revenue, assumptions, base_inputs, kwargs,
        targets=[("Precio muy alto", very_high_price)],
    )
    assert results[0].terminal_growth_fragile is True
