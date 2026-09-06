import pandas as pd
import pytest

from engine.comps import build_comps_table, comps_implied_share_price, peer_average_multiple

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


# --- comps_implied_share_price -------------------------------------------------

def test_comps_implied_share_price_applies_peer_median_to_target_ebitda_and_revenue():
    """AMZN excluida: mediana EV/EBITDA(MSFT,GOOGL)=16.5, mediana
    EV/Revenue=9.0. EBITDA objetivo=100, ingresos=500, deuda neta=150,
    10 acciones -> precio_ebitda=(1650-150)/10=150.0,
    precio_revenue=(4500-150)/10=435.0."""
    df = build_comps_table(SNAPSHOTS)
    result = comps_implied_share_price(
        df, target_symbol="AMZN", target_ebitda=100.0, target_revenue=500.0,
        cash=50.0, total_debt=200.0, diluted_shares=10.0,
    )
    assert result.ev_ebitda_multiple == pytest.approx(16.5)
    assert result.ev_revenue_multiple == pytest.approx(9.0)
    assert result.net_debt == pytest.approx(150.0)
    assert result.implied_ev_from_ebitda == pytest.approx(1650.0)
    assert result.implied_ev_from_revenue == pytest.approx(4500.0)
    assert result.implied_share_price_from_ebitda == pytest.approx(150.0)
    assert result.implied_share_price_from_revenue == pytest.approx(435.0)


def test_comps_implied_share_price_ebitda_price_is_none_when_ebitda_not_positive():
    """EV/EBITDA no tiene interpretación económica sobre una base
    negativa o nula -- EV/Revenue sigue siendo válido (ingresos
    positivos), lo que confirma por qué es la alternativa robusta para
    compañías sin beneficios."""
    df = build_comps_table(SNAPSHOTS)
    result = comps_implied_share_price(
        df, target_symbol="AMZN", target_ebitda=-20.0, target_revenue=500.0,
        cash=50.0, total_debt=200.0, diluted_shares=10.0,
    )
    assert result.implied_share_price_from_ebitda is None
    assert result.implied_share_price_from_revenue is not None


def test_comps_implied_share_price_rejects_non_positive_diluted_shares():
    df = build_comps_table(SNAPSHOTS)
    with pytest.raises(ValueError):
        comps_implied_share_price(
            df, target_symbol="AMZN", target_ebitda=100.0, target_revenue=500.0,
            cash=50.0, total_debt=200.0, diluted_shares=0.0,
        )


def test_comps_implied_share_price_net_cash_position_increases_price():
    """Con caja neta (cash > total_debt), net_debt es negativo -- restar
    un número negativo SUMA al precio implícito, no lo resta."""
    df = build_comps_table(SNAPSHOTS)
    result = comps_implied_share_price(
        df, target_symbol="AMZN", target_ebitda=100.0, target_revenue=500.0,
        cash=500.0, total_debt=100.0, diluted_shares=10.0,
    )
    assert result.net_debt == pytest.approx(-400.0)
    assert result.implied_share_price_from_ebitda == pytest.approx((1650.0 - (-400.0)) / 10.0)
