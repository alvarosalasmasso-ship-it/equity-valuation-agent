"""Tests del motor de proyección (engine/projections.py).

Los casos de cagr/linear_fade/average_margin son verificables a mano
(matemática pura). default_assumptions_from_history se testea con un
histórico sintético de 4 años donde cada margen es constante a propósito,
para poder afirmar el resultado exacto.
"""

import pandas as pd
import pytest

from engine.projections import (
    ProjectionAssumptions,
    average_margin,
    cagr,
    default_assumptions_from_history,
    linear_fade,
    project_financials,
)


def test_cagr_known_case():
    # 100 -> 121 en 2 años = 10% anual compuesto
    assert cagr(100, 121, 2) == pytest.approx(0.10, rel=1e-9)


def test_cagr_rejects_non_positive_first_value():
    with pytest.raises(ValueError):
        cagr(0, 100, 2)
    with pytest.raises(ValueError):
        cagr(100, 100, 0)


def test_average_margin_ignores_missing_pairs():
    numerator = [10, None, 15, float("nan")]
    denominator = [100, 100, 150, 100]
    # pares válidos: 10/100=0.10, 15/150=0.10 -> media 0.10
    assert average_margin(numerator, denominator) == pytest.approx(0.10)


def test_average_margin_raises_when_no_valid_pairs():
    with pytest.raises(ValueError):
        average_margin([None, None], [1, 2])


def test_linear_fade_interpolates_from_start_to_end_inclusive():
    faded = linear_fade(0.10, 0.02, 5)
    assert faded[0] == pytest.approx(0.10)
    assert faded[-1] == pytest.approx(0.02)
    assert len(faded) == 5
    # paso constante
    steps = [faded[i + 1] - faded[i] for i in range(len(faded) - 1)]
    assert all(s == pytest.approx(steps[0]) for s in steps)


def test_linear_fade_single_year_returns_end_value():
    assert linear_fade(0.10, 0.02, 1) == [0.02]


def test_project_financials_applies_fade_and_constant_margins():
    assumptions = ProjectionAssumptions(
        n_years=3,
        initial_revenue_growth=0.10,
        terminal_revenue_growth=0.10,  # sin fade: crecimiento constante 10%
        ebit_margin=0.20,
        da_pct_revenue=0.05,
        capex_pct_revenue=0.08,
        nwc_change_pct_revenue=0.02,
        tax_rate=0.21,
    )
    result = project_financials(last_actual_revenue=1000, assumptions=assumptions)

    expected_revenue = [1100.0, 1210.0, 1331.0]
    for value, expected in zip(result.revenue, expected_revenue):
        assert value == pytest.approx(expected)

    assert result.ebit[0] == pytest.approx(1100.0 * 0.20)
    assert result.d_and_a[1] == pytest.approx(1210.0 * 0.05)
    assert result.capex[2] == pytest.approx(1331.0 * 0.08)
    assert result.change_in_nwc[0] == pytest.approx(1100.0 * 0.02)
    assert result.tax_rate == [0.21, 0.21, 0.21]


def _synthetic_history() -> pd.DataFrame:
    # 4 años, revenue creciendo 10%/año desde 1000, márgenes CONSTANTES
    # a propósito para poder afirmar el resultado exacto de las medias.
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


def test_default_assumptions_from_history_recovers_constant_margins():
    history = _synthetic_history()
    assumptions = default_assumptions_from_history(
        history, n_years=5, terminal_growth_rate=0.025, lookback_years=3
    )

    assert assumptions.initial_revenue_growth == pytest.approx(0.10, rel=1e-6)
    assert assumptions.ebit_margin == pytest.approx(0.20)
    assert assumptions.da_pct_revenue == pytest.approx(0.05)
    assert assumptions.capex_pct_revenue == pytest.approx(0.08)
    assert assumptions.nwc_change_pct_revenue == pytest.approx(0.02)
    assert assumptions.tax_rate == pytest.approx(0.25)
    assert assumptions.terminal_revenue_growth == pytest.approx(0.025)
    assert assumptions.n_years == 5


def test_default_assumptions_margin_window_excludes_extra_older_year():
    """Regresión: el CAGR de ingresos necesita lookback_years+1 puntos
    (los extremos), pero la media de márgenes debe usar EXACTAMENTE
    lookback_years puntos, no uno más. Aquí el año más antiguo (2020)
    tiene un margen muy distinto (0.50) al resto (0.20); con
    lookback_years=3 no debe entrar en la media de márgenes."""
    revenue = [1000.0, 1100.0, 1210.0, 1331.0]
    history = pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": revenue,
        "ebit": [500.0, 220.0, 242.0, 266.2],  # 2020 margen=0.50, resto=0.20
        "d_and_a": [r * 0.05 for r in revenue],
        "capex": [r * 0.08 for r in revenue],
        "change_in_nwc": [None] + [r * 0.02 for r in revenue[1:]],
        "tax_rate": [0.25, 0.25, 0.25, 0.25],
    })
    assumptions = default_assumptions_from_history(history, lookback_years=3)
    assert assumptions.ebit_margin == pytest.approx(0.20)


def test_default_assumptions_requires_at_least_two_revenue_points():
    history = pd.DataFrame({"revenue": [1000.0], "ebit": [200.0], "d_and_a": [50.0],
                             "capex": [80.0], "change_in_nwc": [None], "tax_rate": [0.25]})
    with pytest.raises(ValueError):
        default_assumptions_from_history(history)


def test_projection_pipeline_feeds_directly_into_dcf_inputs():
    """Integración: los outputs de project_financials deben ser
    directamente aceptados por engine.valuation.DCFInputs sin transformar."""
    from engine.valuation import DCFInputs, run_dcf

    history = _synthetic_history()
    assumptions = default_assumptions_from_history(history, n_years=5, terminal_growth_rate=0.025)
    projection = project_financials(history["revenue"].iloc[-1], assumptions)

    inputs = DCFInputs(
        ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
        capex=projection.capex, change_in_nwc=projection.change_in_nwc,
        wacc=0.09, terminal_growth_rate=0.025, cash=100, total_debt=50, diluted_shares=100,
    )
    result = run_dcf(inputs)
    assert result.implied_share_price > 0
