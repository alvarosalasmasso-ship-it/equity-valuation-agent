"""Tests de engine/yfinance_provider.py con un doble de prueba (sin red)
que replica la forma real de un yfinance.Ticker: DataFrames con columnas
= Timestamp (fechas de cierre fiscal) e índice = nombre de la línea,
más un dict .info. Confirmado a mano contra la API real de yfinance
(PG, KO) durante el desarrollo -- ver docs/METHODOLOGY.md.
"""

import pandas as pd
import pytest

from engine.yfinance_provider import historical_financials, market_snapshot


class FakeTicker:
    def __init__(self, financials, balance_sheet, cashflow, info, ticker="TEST"):
        self.financials = financials
        self.balance_sheet = balance_sheet
        self.cashflow = cashflow
        self.info = info
        self.ticker = ticker


def _make_frame(data: dict, dates: list) -> pd.DataFrame:
    """data: nombre de línea -> lista de valores (mismo orden que dates)."""
    return pd.DataFrame(data, index=dates).T


DATES = [pd.Timestamp("2023-06-30"), pd.Timestamp("2022-06-30")]  # yfinance: más reciente primero

FINANCIALS = _make_frame({
    "Total Revenue": [100.0, 90.0],
    "EBIT": [20.0, 18.0],
    "EBITDA": [25.0, 22.0],
    "Pretax Income": [18.0, 16.0],
    "Tax Provision": [3.6, 2.8],  # tax_rate: 0.20 y 0.175
    "Net Income": [14.4, 13.2],
    "Interest Expense": [1.0, 0.9],
}, DATES)

BALANCE_SHEET = _make_frame({
    "Current Assets": [50.0, 40.0],
    "Cash And Cash Equivalents": [10.0, 8.0],
    "Current Liabilities": [30.0, 25.0],
    "Current Debt": [5.0, 3.0],
    "Total Assets": [200.0, 180.0],
    "Stockholders Equity": [120.0, 110.0],
    "Total Debt": [60.0, 55.0],
}, DATES)

CASHFLOW = _make_frame({
    "Depreciation And Amortization": [5.0, 4.0],
    "Capital Expenditure": [-12.0, -10.0],  # yfinance: negativo (salida de caja)
}, DATES)

INFO = {
    "sector": "TECHNOLOGY",
    "industry": "SOFTWARE",
    "marketCap": 1000.0,
    "sharesOutstanding": 10.0,
    "currentPrice": 100.0,
    "beta": 1.2,
    "enterpriseToEbitda": 15.5,
    "enterpriseToRevenue": 4.0,
    "trailingPE": 20.0,
    "priceToSalesTrailing12Months": 3.5,
    "priceToBook": 5.0,
    "targetMeanPrice": 120.5,
    "totalCash": 10.0,
    "totalDebt": 60.0,
}


def make_fake_ticker() -> FakeTicker:
    return FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW, INFO, ticker="TEST")


def test_historical_financials_normalizes_and_sorts_ascending():
    df = historical_financials(make_fake_ticker())
    assert list(df["fiscal_year"]) == [2022, 2023]
    assert list(df["revenue"]) == [90.0, 100.0]
    assert list(df["ebit"]) == [18.0, 20.0]


def test_historical_financials_takes_absolute_value_of_capex():
    """yfinance reporta CapEx como salida de caja (negativo) -- el
    esquema de data_provider.py usa CapEx positivo, hay que homogeneizar."""
    df = historical_financials(make_fake_ticker())
    assert list(df["capex"]) == [10.0, 12.0]


def test_historical_financials_computes_tax_rate_from_pretax_income():
    df = historical_financials(make_fake_ticker())
    row_2022 = df[df["fiscal_year"] == 2022].iloc[0]
    row_2023 = df[df["fiscal_year"] == 2023].iloc[0]
    assert row_2022["tax_rate"] == pytest.approx(2.8 / 16)
    assert row_2023["tax_rate"] == pytest.approx(3.6 / 18)


def test_historical_financials_computes_change_in_nwc():
    """NWC = (Activo corriente - Caja) - (Pasivo corriente - Deuda corto)
    2022: (40-8) - (25-3) = 32 - 22 = 10  -> primer año, sin dato previo -> NaN
    2023: (50-10) - (30-5) = 40 - 25 = 15 -> Delta = 15 - 10 = 5
    Misma fórmula que engine.data_provider, sobre datos de yfinance."""
    df = historical_financials(make_fake_ticker())
    row_2022 = df[df["fiscal_year"] == 2022].iloc[0]
    row_2023 = df[df["fiscal_year"] == 2023].iloc[0]
    assert pd.isna(row_2022["change_in_nwc"])
    assert row_2023["change_in_nwc"] == pytest.approx(5.0)


def test_historical_financials_includes_extended_fields_for_ratios():
    df = historical_financials(make_fake_ticker())
    row = df[df["fiscal_year"] == 2023].iloc[0]
    assert row["ebitda"] == pytest.approx(25.0)
    assert row["interest_expense"] == pytest.approx(1.0)
    assert row["total_assets"] == pytest.approx(200.0)
    assert row["total_equity"] == pytest.approx(120.0)
    assert row["total_debt"] == pytest.approx(60.0)


def test_market_snapshot_matches_data_provider_schema():
    snapshot = market_snapshot(make_fake_ticker())
    expected_keys = {
        "symbol", "sector", "industry", "market_cap", "shares_outstanding",
        "price", "beta", "ev_to_ebitda", "ev_to_revenue", "pe_ratio",
        "price_to_sales", "price_to_book", "analyst_target_price", "cash", "total_debt",
    }
    assert expected_keys.issubset(snapshot.keys())
    assert snapshot["price"] == pytest.approx(100.0)
    assert snapshot["beta"] == pytest.approx(1.2)
    assert snapshot["symbol"] == "TEST"


def test_historical_financials_treats_zero_interest_expense_as_missing_when_debt_exists():
    """Regresión: mismo caso real encontrado con AAPL vía Alpha Vantage,
    verificado también en yfinance_provider por consistencia. Interest
    Expense=0 en el año más reciente con Total Debt>0 se trata como dato
    faltante, no como coste de deuda real de cero."""
    financials = _make_frame({
        "Total Revenue": [100.0, 90.0],
        "EBIT": [20.0, 18.0],
        "EBITDA": [25.0, 22.0],
        "Pretax Income": [18.0, 16.0],
        "Tax Provision": [3.6, 2.8],
        "Net Income": [14.4, 13.2],
        "Interest Expense": [0.0, 0.9],  # 2023 (más reciente) = 0 espurio
    }, DATES)
    ticker = FakeTicker(financials, BALANCE_SHEET, CASHFLOW, INFO, ticker="TEST")

    df = historical_financials(ticker)
    row_2023 = df[df["fiscal_year"] == 2023].iloc[0]
    row_2022 = df[df["fiscal_year"] == 2022].iloc[0]
    assert pd.isna(row_2023["interest_expense"])
    assert row_2022["interest_expense"] == pytest.approx(0.9)
    assert df["interest_expense"].dropna().iloc[-1] == pytest.approx(0.9)


def test_market_snapshot_falls_back_to_market_cap_over_shares_when_no_price_field():
    info = dict(INFO)
    info["currentPrice"] = None
    info["regularMarketPrice"] = None
    ticker = FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW, info)
    snapshot = market_snapshot(ticker)
    assert snapshot["price"] == pytest.approx(1000.0 / 10.0)
