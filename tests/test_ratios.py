import pandas as pd
import pytest

from engine.ratios import (
    compute_ratio_snapshot,
    creates_value,
    current_ratio,
    debt_to_ebitda,
    interest_coverage,
    invested_capital,
    latest_ratio_snapshot,
    roe_dupont,
    roic,
)


def test_roe_dupont_decomposition_multiplies_back_to_roe():
    result = roe_dupont(net_income=100, revenue=1000, total_assets=2000, total_equity=500)
    assert result.net_margin == pytest.approx(0.10)
    assert result.asset_turnover == pytest.approx(0.5)
    assert result.equity_multiplier == pytest.approx(4.0)
    assert result.roe == pytest.approx(0.10 * 0.5 * 4.0)
    assert result.roe == pytest.approx(0.20)


def test_invested_capital():
    assert invested_capital(total_debt=300, total_equity=700, cash=100) == pytest.approx(900)


def test_roic_and_creates_value():
    ic = invested_capital(total_debt=300, total_equity=700, cash=100)
    r = roic(ebit=150, tax_rate=0.20, invested_capital_=ic)
    # NOPAT = 150*0.8 = 120; ROIC = 120/900 = 0.1333...
    assert r == pytest.approx(120 / 900)
    assert creates_value(r, wacc=0.09) is True
    assert creates_value(r, wacc=0.20) is False


def test_debt_to_ebitda():
    assert debt_to_ebitda(total_debt=400, ebitda=200) == pytest.approx(2.0)


def test_interest_coverage():
    assert interest_coverage(ebit=100, interest_expense=20) == pytest.approx(5.0)


def test_interest_coverage_zero_interest_is_infinite():
    assert interest_coverage(ebit=100, interest_expense=0) == float("inf")


def test_current_ratio():
    assert current_ratio(current_assets=150, current_liabilities=100) == pytest.approx(1.5)


def test_roic_warns_on_non_positive_invested_capital():
    with pytest.warns(UserWarning, match="Capital invertido"):
        roic(ebit=100, tax_rate=0.2, invested_capital_=-50)


def test_roic_does_not_warn_on_positive_invested_capital():
    import warnings as warnings_module
    with warnings_module.catch_warnings():
        warnings_module.simplefilter("error")
        roic(ebit=100, tax_rate=0.2, invested_capital_=500)


ROW = {
    "fiscal_year": 2025, "net_income": 100, "revenue": 1000, "total_assets": 2000,
    "total_equity": 500, "total_debt": 300, "cash": 100, "ebit": 150, "tax_rate": 0.2,
    "ebitda": 200, "interest_expense": 20, "current_assets": 150, "current_liabilities": 100,
}


def test_compute_ratio_snapshot_matches_individual_functions():
    snapshot = compute_ratio_snapshot(ROW, wacc=0.09)
    assert snapshot.fiscal_year == 2025
    assert snapshot.roe == pytest.approx(0.20)
    assert snapshot.roic == pytest.approx(120 / 700)  # invested_capital = 300+500-100=700
    assert snapshot.creates_value is True
    assert snapshot.debt_to_ebitda == pytest.approx(1.5)
    assert snapshot.interest_coverage == pytest.approx(7.5)
    assert snapshot.current_ratio == pytest.approx(1.5)


def test_latest_ratio_snapshot_picks_last_year_with_complete_data():
    history = pd.DataFrame([
        {**ROW, "fiscal_year": 2024, "ebitda": None},  # año incompleto, debe saltarse
        ROW,
    ])
    snapshot = latest_ratio_snapshot(history, wacc=0.09)
    assert snapshot.fiscal_year == 2025


def test_latest_ratio_snapshot_raises_when_no_complete_year():
    history = pd.DataFrame([{**ROW, "ebitda": None}])
    with pytest.raises(ValueError):
        latest_ratio_snapshot(history, wacc=0.09)
