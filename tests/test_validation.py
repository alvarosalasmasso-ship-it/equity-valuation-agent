"""Tests de engine/validation.py con un universo sintético de 3 tickers
(no llama a la API real: build_peer_set/value_ticker/validate_universe
reciben los históricos y snapshots ya construidos)."""

from datetime import date

import pandas as pd
import pytest

from engine.reverse_dcf import ImpliedExpectations
from engine.validation import build_peer_set, summarize_deviation, validate_universe, value_ticker


def _flat_history(revenue_last: float, ebit_margin: float, tax_rate: float) -> pd.DataFrame:
    revenue = [revenue_last / 1.1**i for i in range(3, -1, -1)]  # 4 años, 10%/año
    return pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": revenue,
        "ebit": [r * ebit_margin for r in revenue],
        "d_and_a": [r * 0.05 for r in revenue],
        "capex": [r * 0.08 for r in revenue],
        "change_in_nwc": [None] + [r * 0.02 for r in revenue[1:]],
        "tax_rate": [tax_rate] * 4,
        "interest_expense": [r * 0.01 for r in revenue],
    })


UNIVERSE_HIST = {
    "AAA": _flat_history(1000.0, ebit_margin=0.20, tax_rate=0.25),
    "BBB": _flat_history(2000.0, ebit_margin=0.25, tax_rate=0.22),
    "CCC": _flat_history(1500.0, ebit_margin=0.18, tax_rate=0.24),
}

UNIVERSE_SNAP = {
    "AAA": {"symbol": "AAA", "beta": 1.0, "total_debt": 300.0, "cash": 100.0, "market_cap": 5000.0,
            "shares_outstanding": 100.0, "ev_to_ebitda": 15.0, "price": 55.0,
            "analyst_target_price": 60.0},
    "BBB": {"symbol": "BBB", "beta": 1.2, "total_debt": 500.0, "cash": 200.0, "market_cap": 8000.0,
            "shares_outstanding": 150.0, "ev_to_ebitda": 16.0, "price": 60.0,
            "analyst_target_price": 65.0},
    "CCC": {"symbol": "CCC", "beta": 0.9, "total_debt": 250.0, "cash": 150.0, "market_cap": 4000.0,
            "shares_outstanding": 80.0, "ev_to_ebitda": 14.0, "price": 55.0,
            "analyst_target_price": 50.0},
}


def test_build_peer_set_excludes_target():
    peers = build_peer_set("AAA", UNIVERSE_HIST, UNIVERSE_SNAP)
    symbols = {p.symbol for p in peers}
    assert symbols == {"BBB", "CCC"}


def test_value_ticker_computes_deviation_vs_market_and_consensus():
    check = value_ticker("AAA", UNIVERSE_HIST, UNIVERSE_SNAP,
                          risk_free_rate=0.04, market_risk_premium=0.05)
    assert check.ticker == "AAA"
    assert check.implied_price > 0
    assert check.deviation_vs_market == pytest.approx(check.implied_price / 55.0 - 1)
    assert check.deviation_vs_consensus == pytest.approx(check.implied_price / 60.0 - 1)


def test_value_ticker_applies_real_stub_when_history_has_fiscal_dates():
    """Regresión (auditoría sesión 15, hallazgo C1): con
    fiscal_year_end_month/day en el histórico, value_ticker debe usar un
    stub real (distinto del 1.0 por defecto)."""
    hist_with_dates = {
        t: h.assign(fiscal_year_end_month=12, fiscal_year_end_day=31)
        for t, h in UNIVERSE_HIST.items()
    }
    check_no_stub = value_ticker("AAA", UNIVERSE_HIST, UNIVERSE_SNAP, 0.04, 0.05)
    check_with_stub = value_ticker(
        "AAA", hist_with_dates, UNIVERSE_SNAP, 0.04, 0.05, valuation_date=date(2023, 7, 1),
    )
    assert check_with_stub.implied_price != pytest.approx(check_no_stub.implied_price)


def test_value_ticker_reports_peer_multiple_not_own_multiple():
    """Regresión: el múltiplo de salida debe ser la MEDIANA de los
    comparables (BBB=16, CCC=14 -> mediana 15), nunca el propio múltiplo
    de AAA. Se fuerza el propio de AAA a 999 (imposible que coincida por
    casualidad) para verificar sin ambigüedad que no se usa."""
    snap_with_distinct_own_multiple = {
        **UNIVERSE_SNAP,
        "AAA": {**UNIVERSE_SNAP["AAA"], "ev_to_ebitda": 999.0},
    }
    check = value_ticker("AAA", UNIVERSE_HIST, snap_with_distinct_own_multiple,
                          risk_free_rate=0.04, market_risk_premium=0.05)
    assert check.peer_ev_ebitda_multiple == pytest.approx(15.0)  # mediana de BBB(16) y CCC(14)
    assert check.peer_ev_ebitda_multiple != pytest.approx(999.0)


def test_value_ticker_requires_target_in_universe():
    with pytest.raises(ValueError):
        value_ticker("ZZZ", UNIVERSE_HIST, UNIVERSE_SNAP, 0.04, 0.05)


def test_value_ticker_requires_at_least_two_tickers():
    with pytest.raises(ValueError):
        value_ticker("AAA", {"AAA": UNIVERSE_HIST["AAA"]}, {"AAA": UNIVERSE_SNAP["AAA"]}, 0.04, 0.05)


def test_value_ticker_handles_missing_market_price_or_consensus():
    snap = dict(UNIVERSE_SNAP)
    snap = {**snap, "AAA": {**snap["AAA"], "price": None, "analyst_target_price": None}}
    check = value_ticker("AAA", UNIVERSE_HIST, snap, risk_free_rate=0.04, market_risk_premium=0.05)
    assert check.deviation_vs_market is None
    assert check.deviation_vs_consensus is None


def test_value_ticker_includes_reverse_dcf_implied_expectations():
    """engine/reverse_dcf.py (sesión 16): value_ticker() debe adjuntar
    las expectativas implícitas de mercado/consenso, reutilizando los
    mismos base_inputs/assumptions que ya calcula para el DCF -- sin
    reconstruirlos por separado."""
    check = value_ticker("AAA", UNIVERSE_HIST, UNIVERSE_SNAP,
                          risk_free_rate=0.04, market_risk_premium=0.05)
    assert len(check.implied_expectations) == 2
    labels = {e.target_label for e in check.implied_expectations}
    assert labels == {"Mercado", "Consenso analistas"}
    for exp in check.implied_expectations:
        assert isinstance(exp, ImpliedExpectations)
        assert exp.assumed_revenue_growth == pytest.approx(0.10, abs=1e-6)  # _flat_history: 10%/año


def test_value_ticker_skips_implied_expectations_for_missing_prices():
    snap = {**UNIVERSE_SNAP, "AAA": {**UNIVERSE_SNAP["AAA"], "price": None, "analyst_target_price": None}}
    check = value_ticker("AAA", UNIVERSE_HIST, snap, risk_free_rate=0.04, market_risk_premium=0.05)
    assert check.implied_expectations == []


def test_validate_universe_returns_one_row_per_ticker():
    df = validate_universe(UNIVERSE_HIST, UNIVERSE_SNAP, risk_free_rate=0.04, market_risk_premium=0.05)
    assert set(df.index) == {"AAA", "BBB", "CCC"}
    assert "implied_price" in df.columns


def test_summarize_deviation_computes_mean_and_median_absolute():
    df = pd.DataFrame({
        "deviation_vs_market": [0.10, -0.20, 0.30],
        "deviation_vs_consensus": [-0.05, 0.15, -0.25],
    })
    summary = summarize_deviation(df)
    assert summary["n"] == 3
    assert summary["mean_abs_deviation_vs_market"] == pytest.approx((0.10 + 0.20 + 0.30) / 3)
    assert summary["median_abs_deviation_vs_market"] == pytest.approx(0.20)
    assert summary["mean_abs_deviation_vs_consensus"] == pytest.approx((0.05 + 0.15 + 0.25) / 3)
