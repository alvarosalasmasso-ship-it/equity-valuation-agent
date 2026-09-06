"""Tests de engine/yfinance_provider.py con un doble de prueba (sin red)
que replica la forma real de un yfinance.Ticker: DataFrames con columnas
= Timestamp (fechas de cierre fiscal) e índice = nombre de la línea,
más un dict .info. Confirmado a mano contra la API real de yfinance
(PG, KO) durante el desarrollo -- ver docs/METHODOLOGY.md.
"""

import pandas as pd
import pytest

from engine.yfinance_provider import (
    HISTORICAL_FINANCIALS_COLUMNS,
    historical_financials,
    live_price,
    market_snapshot,
    treasury_yield_10y,
)


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
    "financialCurrency": "USD",
    "currency": "USD",
    "fiftyTwoWeekHigh": 135.0,
    "fiftyTwoWeekLow": 82.0,
    "recommendationKey": "buy",
    "recommendationMean": 1.9,
    "numberOfAnalystOpinions": 22,
    "targetLowPrice": 90.0,
    "targetHighPrice": 150.0,
}


def make_fake_ticker() -> FakeTicker:
    return FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW, INFO, ticker="TEST")


def test_historical_financials_normalizes_and_sorts_ascending():
    df = historical_financials(make_fake_ticker())
    assert list(df["fiscal_year"]) == [2022, 2023]
    assert list(df["revenue"]) == [90.0, 100.0]
    assert list(df["ebit"]) == [18.0, 20.0]


def test_historical_financials_exposes_fiscal_year_end_month_and_day():
    """Necesario para engine.valuation.compute_stub_fraction (auditoría
    sesión 15, hallazgo C1) -- DATES usa 30 de junio (estilo MSFT)."""
    df = historical_financials(make_fake_ticker())
    row_2023 = df[df["fiscal_year"] == 2023].iloc[0]
    assert row_2023["fiscal_year_end_month"] == 6
    assert row_2023["fiscal_year_end_day"] == 30


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
        "currency", "week_52_high", "week_52_low",
        "analyst_recommendation_key", "analyst_recommendation_mean", "analyst_num_opinions",
        "analyst_target_price_low", "analyst_target_price_high",
    }
    assert expected_keys.issubset(snapshot.keys())
    assert snapshot["price"] == pytest.approx(100.0)
    assert snapshot["beta"] == pytest.approx(1.2)
    assert snapshot["symbol"] == "TEST"
    assert snapshot["currency"] == "USD"
    assert snapshot["week_52_high"] == pytest.approx(135.0)
    assert snapshot["week_52_low"] == pytest.approx(82.0)
    assert snapshot["analyst_recommendation_key"] == "buy"
    assert snapshot["analyst_recommendation_mean"] == pytest.approx(1.9)
    assert snapshot["analyst_num_opinions"] == pytest.approx(22)
    assert snapshot["analyst_target_price_low"] == pytest.approx(90.0)
    assert snapshot["analyst_target_price_high"] == pytest.approx(150.0)


def test_market_snapshot_prefers_balance_sheet_over_info_for_cash_and_debt():
    """Auditoría sesión 17: `info["totalDebt"]`/`["totalCash"]` resultaron
    NO ser fiables -- verificado con datos reales de AMZN, donde
    `info["totalDebt"]` daba $251.6bn frente a los $153.0bn de
    `balance_sheet.loc["Total Debt"]` (que sí coincide exacto con Alpha
    Vantage). Reproduce ese caso: `.info` y `.balance_sheet` en
    desacuerdo deliberado -- debe ganar `.balance_sheet`, la misma
    fuente que ya usa (y siempre usó, sin este bug) historical_financials()."""
    ticker = FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW,
                         {**INFO, "totalDebt": 999.0, "totalCash": 999.0}, ticker="TEST")
    snapshot = market_snapshot(ticker)
    assert snapshot["total_debt"] == pytest.approx(60.0)  # de BALANCE_SHEET, no de info
    assert snapshot["cash"] == pytest.approx(10.0)


def test_market_snapshot_falls_back_to_info_when_balance_sheet_unavailable():
    """Si `.balance_sheet` viene vacío (yfinance a veces lo devuelve así
    para tickers con datos incompletos), cae a `.info` como mejor
    esfuerzo en vez de fallar -- degradación, no un crash."""
    ticker = FakeTicker(FINANCIALS, pd.DataFrame(), CASHFLOW, INFO, ticker="TEST")
    snapshot = market_snapshot(ticker)
    assert snapshot["total_debt"] == pytest.approx(60.0)  # de info, único disponible
    assert snapshot["cash"] == pytest.approx(10.0)


def test_market_snapshot_prefers_financial_currency_over_quote_currency():
    """Auditoría sesión 15/16, hallazgo M5: un ADR puede cotizar en USD
    ("currency") con estados financieros en otra divisa
    ("financialCurrency") -- la relevante para comparar contra el USD de
    risk_free_rate/market_risk_premium es la de los estados financieros."""
    info = {**INFO, "financialCurrency": "JPY", "currency": "USD"}
    ticker = FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW, info)
    snapshot = market_snapshot(ticker)
    assert snapshot["currency"] == "JPY"


def test_market_snapshot_falls_back_to_quote_currency_when_financial_currency_missing():
    info = {k: v for k, v in INFO.items() if k != "financialCurrency"}
    ticker = FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW, info)
    snapshot = market_snapshot(ticker)
    assert snapshot["currency"] == "USD"


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


class FakeIndexTicker:
    """Doble de prueba para yf.Ticker('^TNX') -- solo necesita .history()."""

    def __init__(self, close_values: list):
        self._history = pd.DataFrame({"Close": close_values})

    def history(self, period="5d"):
        return self._history


def test_treasury_yield_10y_converts_percentage_points_to_fraction():
    """Auditoría sesión 15, hallazgo I1: risk-free rate en vivo. ^TNX
    cotiza en puntos porcentuales (Close=4.15 significa 4.15%, no 415%)."""
    ticker = FakeIndexTicker([4.10, 4.12, 4.15])
    assert treasury_yield_10y(ticker) == pytest.approx(0.0415)


def test_treasury_yield_10y_uses_most_recent_close():
    ticker = FakeIndexTicker([4.30, 4.20, 4.10])  # el más reciente es el último, no el mayor
    assert treasury_yield_10y(ticker) == pytest.approx(0.0410)


def test_treasury_yield_10y_raises_when_history_empty():
    ticker = FakeIndexTicker([])
    with pytest.raises(ValueError):
        treasury_yield_10y(ticker)


# --- live_price (auditoría sesión 17, fuente de cotización fiable para AV) --

def test_live_price_prefers_current_price():
    ticker = FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW, {"currentPrice": 123.45, "regularMarketPrice": 999.0})
    assert live_price(ticker) == pytest.approx(123.45)


def test_live_price_falls_back_to_regular_market_price():
    ticker = FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW, {"regularMarketPrice": 88.0})
    assert live_price(ticker) == pytest.approx(88.0)


def test_live_price_falls_back_to_market_cap_over_shares():
    ticker = FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW, {"marketCap": 1000.0, "sharesOutstanding": 10.0})
    assert live_price(ticker) == pytest.approx(100.0)


def test_live_price_returns_none_when_nothing_available():
    ticker = FakeTicker(FINANCIALS, BALANCE_SHEET, CASHFLOW, {})
    assert live_price(ticker) is None


def test_historical_financials_returns_empty_dataframe_for_invalid_ticker():
    """Regresión (auditoría sesión 15, hallazgo I3): confirmado
    reproducible con un símbolo inexistente real -- yfinance devuelve
    financials/balance_sheet/cashflow vacíos (sin fechas en común), y
    pd.DataFrame([]).sort_values('fiscal_year') lanzaba
    KeyError('fiscal_year'). Debe devolver un DataFrame vacío pero con
    las columnas esperadas."""
    empty = pd.DataFrame()
    ticker = FakeTicker(empty, empty, empty, {}, ticker="ZZZZINVALID")
    df = historical_financials(ticker)
    assert df.empty
    assert list(df.columns) == HISTORICAL_FINANCIALS_COLUMNS
