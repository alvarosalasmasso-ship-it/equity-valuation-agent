"""Tests del normalizador de datos (engine/data_provider.py).

No hacen llamadas de red: se simulan las respuestas de Alpha Vantage
(mismo formato real, verificado a mano contra la API en desarrollo) para
poder testear la lógica de normalización sin gastar cuota de API ni
depender de conexión a internet.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from engine.data_provider import AlphaVantageClient, historical_financials, market_snapshot

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


def test_client_requires_api_key():
    import os
    saved = os.environ.pop("ALPHA_VANTAGE_API_KEY", None)
    try:
        with pytest.raises(RuntimeError):
            AlphaVantageClient(api_key=None)
    finally:
        if saved is not None:
            os.environ["ALPHA_VANTAGE_API_KEY"] = saved
