import pandas as pd
import pytest

from engine.comps import build_comps_table, peer_average_multiple

SNAPSHOTS = [
    {"symbol": "AMZN", "sector": "TECH", "industry": "CLOUD", "market_cap": 2000,
     "price": 200, "ev_to_ebitda": 20.0, "ev_to_revenue": 4.0, "pe_ratio": 40.0,
     "price_to_sales": 3.5, "price_to_book": 8.0, "beta": 1.2},
    {"symbol": "MSFT", "sector": "TECH", "industry": "SOFTWARE", "market_cap": 3000,
     "price": 400, "ev_to_ebitda": 18.0, "ev_to_revenue": 12.0, "pe_ratio": 35.0,
     "price_to_sales": 11.0, "price_to_book": 12.0, "beta": 0.9},
    {"symbol": "GOOGL", "sector": "TECH", "industry": "INTERNET", "market_cap": 1800,
     "price": 150, "ev_to_ebitda": 15.0, "ev_to_revenue": 6.0, "pe_ratio": 25.0,
     "price_to_sales": 5.5, "price_to_book": 6.0, "beta": 1.05},
]


def test_build_comps_table_indexes_by_symbol():
    df = build_comps_table(SNAPSHOTS)
    assert list(df.index) == ["AMZN", "MSFT", "GOOGL"]
    assert df.loc["AMZN", "ev_to_ebitda"] == 20.0


def test_build_comps_table_requires_at_least_one_snapshot():
    with pytest.raises(ValueError):
        build_comps_table([])


def test_peer_average_multiple_median_excludes_target():
    df = build_comps_table(SNAPSHOTS)
    # excluyendo AMZN: EV/EBITDA de MSFT y GOOGL = [18, 15] -> mediana 16.5
    median = peer_average_multiple(df, "ev_to_ebitda", exclude_symbol="AMZN", method="median")
    assert median == pytest.approx(16.5)


def test_peer_average_multiple_mean_includes_all_when_not_excluded():
    df = build_comps_table(SNAPSHOTS)
    mean = peer_average_multiple(df, "ev_to_ebitda", method="mean")
    assert mean == pytest.approx((20.0 + 18.0 + 15.0) / 3)


def test_peer_average_multiple_ignores_missing_values():
    snapshots = SNAPSHOTS + [{"symbol": "META", "sector": "TECH", "industry": "SOCIAL",
                               "market_cap": 900, "price": 300, "ev_to_ebitda": None,
                               "ev_to_revenue": 5.0, "pe_ratio": 22.0,
                               "price_to_sales": 4.0, "price_to_book": 5.0, "beta": 1.3}]
    df = build_comps_table(snapshots)
    mean = peer_average_multiple(df, "ev_to_ebitda", method="mean")
    assert mean == pytest.approx((20.0 + 18.0 + 15.0) / 3)


def test_peer_average_multiple_invalid_column_raises():
    df = build_comps_table(SNAPSHOTS)
    with pytest.raises(ValueError):
        peer_average_multiple(df, "not_a_column")


def test_peer_average_multiple_invalid_method_raises():
    df = build_comps_table(SNAPSHOTS)
    with pytest.raises(ValueError):
        peer_average_multiple(df, "ev_to_ebitda", method="mode")
