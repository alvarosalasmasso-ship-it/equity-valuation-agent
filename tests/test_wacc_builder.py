"""Test de wacc_builder.py contra el mismo caso conocido de AMZN/Excel
usado en tests/test_valuation.py (mismos targets, misma fuente: los
propios comparables y datos de mercado del Excel de referencia).

El objetivo de este test no es re-verificar la matemática de CAPM/WACC
(ya cubierto en test_valuation.py) sino confirmar que la capa de
orquestación (agrupar peers, promediar sus betas desapalancadas,
reapalancar y montar el WACC) ensambla esas piezas sin introducir
errores, reproduciendo el WACC real de Amazon de forma exacta.
"""

import pytest

from engine.wacc_builder import PeerInput, build_wacc, industry_unlevered_beta

AAPL = PeerInput(symbol="AAPL", levered_beta=0.99, tax_rate=0.158, net_debt=39503, market_cap=3498463.5)
MSFT = PeerInput(symbol="MSFT", levered_beta=1.23, tax_rate=0.19, net_debt=-33311, market_cap=3041094.8)
GOOGL = PeerInput(symbol="GOOGL", levered_beta=1.14, tax_rate=0.16, net_debt=-97663, market_cap=2113086)

AMZN_TAX_RATE = 0.1774762952146831
AMZN_MARKET_CAP = 186.63 * 10876.066881
AMZN_NET_DEBT = 54889 - 89092
AMZN_TOTAL_DEBT = 54889
AMZN_INTEREST_EXPENSE = 1233 * 2

TARGET_WACC = 0.08331511062550083
TARGET_RELEVERED_BETA = 1.1201674938117516
TARGET_INDUSTRY_UNLEVERED_BETA = 1.1359110788056939


def test_industry_unlevered_beta_matches_excel_per_peer():
    betas = industry_unlevered_beta([AAPL, MSFT, GOOGL])
    assert betas["AAPL"] == pytest.approx(0.9806762529648875, rel=1e-9)
    assert betas["MSFT"] == pytest.approx(1.2410107851003551, rel=1e-9)
    assert betas["GOOGL"] == pytest.approx(1.1860461983518389, rel=1e-9)


def test_industry_unlevered_beta_rejects_empty_peer_list():
    with pytest.raises(ValueError):
        industry_unlevered_beta([])


def test_build_wacc_end_to_end_matches_excel_exactly():
    result = build_wacc(
        peers=[AAPL, MSFT, GOOGL],
        target_tax_rate=AMZN_TAX_RATE,
        target_net_debt=AMZN_NET_DEBT,
        target_market_cap=AMZN_MARKET_CAP,
        risk_free_rate=0.03909,
        market_risk_premium=0.0406,
        target_interest_expense=AMZN_INTEREST_EXPENSE,
        target_total_debt=AMZN_TOTAL_DEBT,
    )

    assert result.industry_unlevered_beta == pytest.approx(TARGET_INDUSTRY_UNLEVERED_BETA, rel=1e-9)
    assert result.relevered_beta == pytest.approx(TARGET_RELEVERED_BETA, rel=1e-9)
    assert result.cost_of_equity == pytest.approx(0.08456880024875711, rel=1e-9)
    assert result.cost_of_debt == pytest.approx(0.04492703456065878, rel=1e-9)
    assert result.wacc == pytest.approx(TARGET_WACC, rel=1e-9)
    assert set(result.peer_unlevered_betas) == {"AAPL", "MSFT", "GOOGL"}
