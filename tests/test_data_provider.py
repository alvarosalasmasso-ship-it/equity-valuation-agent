"""Tests del normalizador de datos (engine/data_provider.py).

No hacen llamadas de red: se simulan las respuestas de Alpha Vantage
(mismo formato real, verificado a mano contra la API en desarrollo) para
poder testear la lógica de normalización sin gastar cuota de API ni
depender de conexión a internet.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from engine.data_provider import AlphaVantageClient, AlphaVantageError, historical_financials, market_snapshot

INCOME_FIXTURE = {
    "annualReports": [
        {
            "fiscalDateEnding": "2022-12-31",
            "totalRevenue": "100",
            "ebit": "20",
            "incomeBeforeTax": "18",
            "incomeTaxExpense": "3.6",
            "depreciationAndAmortization": "5",
            "netIncome": "14.4",
        },
        {
            "fiscalDateEnding": "2021-12-31",
            "totalRevenue": "90",
            "ebit": "15",
            "incomeBeforeTax": "14",
            "incomeTaxExpense": "2.8",
            "depreciationAndAmortization": "4",
            "netIncome": "11.2",
        },
    ]
}

BALANCE_FIXTURE = {
    "annualReports": [
        {
            "fiscalDateEnding": "2022-12-31",
            "totalCurrentAssets": "50",
            "cashAndShortTermInvestments": "10",
            "totalCurrentLiabilities": "30",
            "shortTermDebt": "5",
            "shortLongTermDebtTotal": "40",
        },
        {
            "fiscalDateEnding": "2021-12-31",
            "totalCurrentAssets": "40",
            "cashAndShortTermInvestments": "8",
            "totalCurrentLiabilities": "25",
            "shortTermDebt": "3",
            "shortLongTermDebtTotal": "35",
        },
    ]
}

CASH_FLOW_FIXTURE = {
    "annualReports": [
        {
            "fiscalDateEnding": "2022-12-31",
            "capitalExpenditures": "12",
            "depreciationDepletionAndAmortization": "5",
        },
        {
            "fiscalDateEnding": "2021-12-31",
            "capitalExpenditures": "10",
            "depreciationDepletionAndAmortization": "4",
        },
    ]
}

OVERVIEW_FIXTURE = {
    "Sector": "TECHNOLOGY",
    "Industry": "SOFTWARE",
    "MarketCapitalization": "1000",
    "SharesOutstanding": "10",
    "Beta": "1.2",
    "EVToEBITDA": "15.5",
    "AnalystTargetPrice": "120.5",
    "Currency": "USD",
    "52WeekHigh": "135.0",
    "52WeekLow": "82.0",
    "AnalystRatingStrongBuy": "5",
    "AnalystRatingBuy": "10",
    "AnalystRatingHold": "3",
    "AnalystRatingSell": "1",
    "AnalystRatingStrongSell": "0",
}


def make_fake_client() -> AlphaVantageClient:
    client = AlphaVantageClient(api_key="test-key")
    client.income_statement = MagicMock(return_value=INCOME_FIXTURE)
    client.balance_sheet = MagicMock(return_value=BALANCE_FIXTURE)
    client.cash_flow = MagicMock(return_value=CASH_FLOW_FIXTURE)
    client.company_overview = MagicMock(return_value=OVERVIEW_FIXTURE)
    return client


def test_historical_financials_normalizes_and_sorts_ascending():
    client = make_fake_client()
    df = historical_financials(client, "TEST")

    assert list(df["fiscal_year"]) == [2021, 2022]
    assert list(df["revenue"]) == [90.0, 100.0]
    assert list(df["ebit"]) == [15.0, 20.0]


def test_historical_financials_exposes_fiscal_year_end_month_and_day():
    """Necesario para engine.valuation.compute_stub_fraction (auditoría
    sesión 15, hallazgo C1) -- parseado de 'fiscalDateEnding' (p.ej.
    '2022-12-31')."""
    client = make_fake_client()
    df = historical_financials(client, "TEST")
    row_2022 = df[df["fiscal_year"] == 2022].iloc[0]
    assert row_2022["fiscal_year_end_month"] == 12
    assert row_2022["fiscal_year_end_day"] == 31


def test_historical_financials_computes_tax_rate_from_pretax_income():
    client = make_fake_client()
    df = historical_financials(client, "TEST")

    row_2021 = df[df["fiscal_year"] == 2021].iloc[0]
    row_2022 = df[df["fiscal_year"] == 2022].iloc[0]
    assert row_2021["tax_rate"] == pytest.approx(2.8 / 14)
    assert row_2022["tax_rate"] == pytest.approx(3.6 / 18)


def test_historical_financials_computes_change_in_nwc():
    """NWC = (Current Assets - Cash) - (Current Liabilities - Deuda corto)
    2021: (40-8) - (25-3) = 32 - 22 = 10   -> primer año, sin dato previo -> None
    2022: (50-10) - (30-5) = 40 - 25 = 15  -> Delta = 15 - 10 = 5
    """
    client = make_fake_client()
    df = historical_financials(client, "TEST")

    row_2021 = df[df["fiscal_year"] == 2021].iloc[0]
    row_2022 = df[df["fiscal_year"] == 2022].iloc[0]
    assert pd.isna(row_2021["change_in_nwc"])
    assert row_2022["change_in_nwc"] == pytest.approx(5.0)


def test_historical_financials_only_keeps_years_present_in_all_three_statements():
    client = make_fake_client()
    client.income_statement.return_value = {
        "annualReports": INCOME_FIXTURE["annualReports"] + [
            {"fiscalDateEnding": "2020-12-31", "totalRevenue": "80"}
        ]
    }
    df = historical_financials(client, "TEST")
    assert 2020 not in list(df["fiscal_year"])


def test_market_snapshot_derives_price_from_market_cap_and_shares():
    client = make_fake_client()
    snapshot = market_snapshot(client, "TEST")

    assert snapshot["price"] == pytest.approx(100.0)  # 1000 / 10
    assert snapshot["beta"] == pytest.approx(1.2)
    assert snapshot["cash"] == pytest.approx(10.0)  # último año = 2022
    assert snapshot["total_debt"] == pytest.approx(40.0)
    assert snapshot["sector"] == "TECHNOLOGY"
    assert snapshot["currency"] == "USD"
    assert snapshot["week_52_high"] == pytest.approx(135.0)
    assert snapshot["week_52_low"] == pytest.approx(82.0)
    assert snapshot["analyst_rating_strong_buy"] == pytest.approx(5)
    assert snapshot["analyst_rating_buy"] == pytest.approx(10)
    assert snapshot["analyst_rating_hold"] == pytest.approx(3)
    assert snapshot["analyst_rating_sell"] == pytest.approx(1)
    assert snapshot["analyst_rating_strong_sell"] == pytest.approx(0)


def test_market_snapshot_exposes_non_usd_currency():
    """Auditoría sesión 15/16, hallazgo M5: una compañía que reporta en
    otra divisa debe quedar expuesta tal cual, sin normalizar ni
    esconder -- es la app quien decide qué hacer con ese dato (bloquear
    la valoración, ver app/streamlit_app.py)."""
    client = make_fake_client()
    client.company_overview.return_value = {**OVERVIEW_FIXTURE, "Currency": "EUR"}
    snapshot = market_snapshot(client, "TEST")
    assert snapshot["currency"] == "EUR"


def test_market_snapshot_currency_is_none_when_missing():
    client = make_fake_client()
    overview_without_currency = {k: v for k, v in OVERVIEW_FIXTURE.items() if k != "Currency"}
    client.company_overview.return_value = overview_without_currency
    snapshot = market_snapshot(client, "TEST")
    assert snapshot["currency"] is None


def test_historical_financials_treats_zero_interest_expense_as_missing_when_debt_exists():
    """Regresión: caso real encontrado con AAPL. Alpha Vantage reportó
    interestExpense=0 en el año más reciente pese a total_debt>0 (deuda
    real de $119bn) -- un coste de deuda real de cero es casi imposible
    con deuda de ese tamaño, así que se trata como dato faltante para
    que cost_of_debt() no calcule silenciosamente un 0%."""
    client = make_fake_client()
    client.income_statement.return_value = {
        "annualReports": [
            {**INCOME_FIXTURE["annualReports"][0], "interestExpense": "0"},  # 2022, año más reciente
            {**INCOME_FIXTURE["annualReports"][1], "interestExpense": "2.8"},  # 2021
        ]
    }
    df = historical_financials(client, "TEST")

    row_2022 = df[df["fiscal_year"] == 2022].iloc[0]
    row_2021 = df[df["fiscal_year"] == 2021].iloc[0]
    assert pd.isna(row_2022["interest_expense"])  # tratado como faltante, no como 0 real
    assert row_2021["interest_expense"] == pytest.approx(2.8)
    # dropna().iloc[-1] debe caer en el último año con dato genuino (2021), no en el 0 espurio
    assert df["interest_expense"].dropna().iloc[-1] == pytest.approx(2.8)


def test_historical_financials_keeps_genuine_zero_interest_expense_when_no_debt():
    """Una compañía sin deuda (total_debt=0) SÍ puede tener
    interest_expense=0 legítimo -- no debe filtrarse en ese caso."""
    client = make_fake_client()
    client.income_statement.return_value = {
        "annualReports": [
            {**INCOME_FIXTURE["annualReports"][0], "interestExpense": "0"},
            INCOME_FIXTURE["annualReports"][1],
        ]
    }
    client.balance_sheet.return_value = {
        "annualReports": [
            {**BALANCE_FIXTURE["annualReports"][0], "shortLongTermDebtTotal": "0"},
            BALANCE_FIXTURE["annualReports"][1],
        ]
    }
    df = historical_financials(client, "TEST")
    row_2022 = df[df["fiscal_year"] == 2022].iloc[0]
    assert row_2022["interest_expense"] == pytest.approx(0.0)


def test_historical_financials_returns_empty_dataframe_when_no_years_overlap():
    """Regresión (auditoría sesión 15, hallazgo I3): sin ningún año en
    común entre los tres estados (p.ej. símbolo inválido/sin datos),
    pd.DataFrame([]).sort_values('fiscal_year') lanzaría
    KeyError('fiscal_year') -- debe devolver un DataFrame vacío pero con
    las columnas esperadas, no crashear."""
    from engine.data_provider import HISTORICAL_FINANCIALS_COLUMNS

    client = make_fake_client()
    client.income_statement.return_value = {"annualReports": []}
    client.balance_sheet.return_value = {"annualReports": []}
    client.cash_flow.return_value = {"annualReports": []}

    df = historical_financials(client, "INVALID")
    assert df.empty
    assert list(df.columns) == HISTORICAL_FINANCIALS_COLUMNS


def test_treasury_yield_converts_percentage_points_to_fraction():
    """Auditoría sesión 15, hallazgo I1: risk-free rate en vivo en vez de
    una constante congelada. Alpha Vantage devuelve el valor en puntos
    porcentuales (p.ej. '4.15'), treasury_yield() debe convertirlo a
    fracción (0.0415)."""
    client = make_fake_client()
    client._fetch_economic_indicator = MagicMock(return_value={
        "data": [
            {"date": "2026-09-04", "value": "4.15"},
            {"date": "2026-09-03", "value": "4.12"},
        ]
    })
    assert client.treasury_yield() == pytest.approx(0.0415)


def test_treasury_yield_picks_most_recent_date_regardless_of_order():
    """No se puede asumir que Alpha Vantage devuelve la serie ordenada --
    debe elegir la fecha más reciente por comparación explícita, no
    tomar el primer elemento a ciegas."""
    client = make_fake_client()
    client._fetch_economic_indicator = MagicMock(return_value={
        "data": [
            {"date": "2026-08-01", "value": "4.00"},
            {"date": "2026-09-04", "value": "4.15"},  # más reciente, no es el primero
            {"date": "2026-09-01", "value": "4.10"},
        ]
    })
    assert client.treasury_yield() == pytest.approx(0.0415)


def test_treasury_yield_skips_placeholder_values():
    """Alpha Vantage usa '.' como marcador de dato faltante en algunas
    series económicas -- no debe tratarse como un 0% real."""
    client = make_fake_client()
    client._fetch_economic_indicator = MagicMock(return_value={
        "data": [
            {"date": "2026-09-04", "value": "."},
            {"date": "2026-09-03", "value": "4.12"},
        ]
    })
    assert client.treasury_yield() == pytest.approx(0.0412)


def test_treasury_yield_raises_when_no_valid_data():
    client = make_fake_client()
    client._fetch_economic_indicator = MagicMock(return_value={"data": []})
    with pytest.raises(AlphaVantageError):
        client.treasury_yield()


def test_client_requires_api_key():
    import os
    saved = os.environ.pop("ALPHA_VANTAGE_API_KEY", None)
    try:
        with pytest.raises(RuntimeError):
            AlphaVantageClient(api_key=None)
    finally:
        if saved is not None:
            os.environ["ALPHA_VANTAGE_API_KEY"] = saved
