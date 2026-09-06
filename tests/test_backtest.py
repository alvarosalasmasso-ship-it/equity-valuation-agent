"""Tests de engine/backtest.py."""

from datetime import date

import pandas as pd
import pytest

from engine.backtest import DEFAULT_REPORTING_LAG_DAYS, build_backtest_result, known_history_as_of

HISTORY = pd.DataFrame({
    "fiscal_year": [2020, 2021, 2022, 2023],
    "fiscal_year_end_month": [12, 12, 12, 12],
    "fiscal_year_end_day": [31, 31, 31, 31],
    "revenue": [1000.0, 1100.0, 1210.0, 1331.0],
})


def test_known_history_as_of_excludes_years_not_yet_filed():
    """A fecha de backtest 2023-06-01, FY2022 (cierre 2022-12-31 +
    90 días = 2023-03-31) ya está publicado; FY2023 (cierre
    2023-12-31 + 90 días = 2024-03-31) todavía no."""
    result = known_history_as_of(HISTORY, as_of_date=date(2023, 6, 1))
    assert list(result["fiscal_year"]) == [2020, 2021, 2022]


def test_known_history_as_of_includes_year_exactly_at_the_reporting_lag_boundary():
    result = known_history_as_of(HISTORY, as_of_date=date(2023, 3, 31))
    assert 2022 in list(result["fiscal_year"])
    assert 2023 not in list(result["fiscal_year"])


def test_known_history_as_of_excludes_year_one_day_before_the_boundary():
    result = known_history_as_of(HISTORY, as_of_date=date(2023, 3, 30))
    assert 2022 not in list(result["fiscal_year"])


def test_known_history_as_of_default_reporting_lag_is_90_days():
    assert DEFAULT_REPORTING_LAG_DAYS == 90


def test_known_history_as_of_far_future_date_includes_everything():
    result = known_history_as_of(HISTORY, as_of_date=date(2030, 1, 1))
    assert list(result["fiscal_year"]) == [2020, 2021, 2022, 2023]


def test_known_history_as_of_far_past_date_includes_nothing():
    result = known_history_as_of(HISTORY, as_of_date=date(2015, 1, 1))
    assert result.empty


def test_known_history_as_of_drops_rows_without_fiscal_year_end():
    history = pd.DataFrame({
        "fiscal_year": [2022, 2023],
        "fiscal_year_end_month": [12, None],
        "fiscal_year_end_day": [31, None],
        "revenue": [1000.0, 1100.0],
    })
    result = known_history_as_of(history, as_of_date=date(2030, 1, 1))
    assert list(result["fiscal_year"]) == [2022]


# --- BacktestResult / build_backtest_result -----------------------------------

def test_build_backtest_result_computes_deviation_and_actual_return():
    result = build_backtest_result(
        ticker="TEST", backtest_date=date(2024, 9, 6), price_at_backtest=100.0,
        price_today=150.0, implied_price_at_backtest=80.0, wacc_at_backtest=0.09,
        years_of_history_used=3,
    )
    assert result.deviation_at_backtest == pytest.approx(80.0 / 100.0 - 1)  # -20%
    assert result.actual_return == pytest.approx(150.0 / 100.0 - 1)  # +50%


def test_build_backtest_result_rejects_non_positive_prices():
    with pytest.raises(ValueError):
        build_backtest_result(ticker="TEST", backtest_date=date(2024, 9, 6), price_at_backtest=0.0,
                               price_today=150.0, implied_price_at_backtest=80.0, wacc_at_backtest=0.09,
                               years_of_history_used=3)
    with pytest.raises(ValueError):
        build_backtest_result(ticker="TEST", backtest_date=date(2024, 9, 6), price_at_backtest=100.0,
                               price_today=-1.0, implied_price_at_backtest=80.0, wacc_at_backtest=0.09,
                               years_of_history_used=3)
