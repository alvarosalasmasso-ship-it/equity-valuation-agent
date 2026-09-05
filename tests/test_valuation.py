"""Tests del motor de valoración contra un caso conocido a mano.

Caso de referencia: Amazon (AMZN), extraído de "Advanced DCF.xlsx"
(modelo profesional de un ex-banquero de JP Morgan usado como fuente
de verdad para este proyecto). Todos los "target" de este archivo son
valores calculados por ese Excel (openpyxl, data_only=True), no
inventados. Ver docs/METHODOLOGY.md para el mapeo celda -> función.
"""

import pytest

from engine.valuation import (
    DCFInputs,
    OptionTranche,
    cost_of_debt,
    cost_of_equity,
    diluted_shares_outstanding,
    discount_periods,
    relever_beta,
    run_dcf,
    sensitivity_matrix,
    treasury_stock_method,
    unlever_beta,
    unlevered_fcf,
    wacc,
)

# --- Datos reales extraídos de Advanced DCF.xlsx --------------------------

AAPL = dict(levered_beta=0.99, tax_rate=0.158, net_debt=39503, market_cap=3498463.5)
MSFT = dict(levered_beta=1.23, tax_rate=0.19, net_debt=-33311, market_cap=3041094.8)
GOOGL = dict(levered_beta=1.14, tax_rate=0.16, net_debt=-97663, market_cap=2113086)

AMZN_TAX_RATE_Y1 = 0.1774762952146831
AMZN_MARKET_CAP = 186.63 * 10876.066881
AMZN_NET_DEBT = 54889 - 89092  # deuda - caja (negativo: caja neta)

EBIT = [62270.17, 77775.41, 97823.1, 118182.7060444323, 136838.2435031237, 158304.14054407948]
TAX_RATE = [0.1774762952146831, 0.15276204215606132, 0.16134919427006217,
            0.1702915097440808, 0.1702915097440808, 0.16643411022579363]
D_AND_A = [53278.04087628567, 61079.37994131737, 69829.08817014826,
           78145.76730574104, 88681.08309208773, 101224.34935337916]
CAPEX = [76711.63850327618, 83017.38077503699, 91194.88492136194,
         107386.31370705253, 120577.76244910699, 137639.53611246275]
CHANGE_IN_NWC = [-21708.526602341764, -22317.900211562068, -22459.872455719444,
                  -27267.443637392516, -29175.374132436118, -31906.031922990285]

TARGET_WACC = 0.08331511062550083
TARGET_TGR = 0.025
TARGET_STUB = 0.16666666666666666
TARGET_DILUTED_SHARES = 10876.066881
TARGET_EXIT_MULTIPLE = 19.425961159529265
TARGET_UFCF = [49493.619901362756, 66274.17891671749, 83133.49733850604,
               96084.09184256605, 110814.54720166547, 127447.77693147333]
TARGET_PV_UFCF = [8194.108965784391, 62831.074754124784, 72753.07984206537,
                   77619.7108353791, 82634.70976786438, 87729.00291268414]
TARGET_IMPLIED_PRICE = 216.4061260658347


# --- WACC / CAPM ------------------------------------------------------------

def test_unlever_relever_beta_matches_excel():
    unlevered = [
        unlever_beta(c["levered_beta"], c["tax_rate"], c["net_debt"], c["market_cap"])
        for c in (AAPL, MSFT, GOOGL)
    ]
    avg_unlevered = sum(unlevered) / len(unlevered)
    assert avg_unlevered == pytest.approx(1.1359110788056939, rel=1e-9)

    relevered = relever_beta(avg_unlevered, AMZN_TAX_RATE_Y1, AMZN_NET_DEBT, AMZN_MARKET_CAP)
    assert relevered == pytest.approx(1.1201674938117516, rel=1e-9)


def test_wacc_build_matches_excel():
    beta = 1.1201674938117516
    re = cost_of_equity(0.03909, beta, 0.0406)
    assert re == pytest.approx(0.08456880024875711, rel=1e-9)

    rd = cost_of_debt(1233 * 2, 54889)
    assert rd == pytest.approx(0.04492703456065878, rel=1e-9)

    w = wacc(AMZN_MARKET_CAP, 54889, re, rd, AMZN_TAX_RATE_Y1)
    assert w == pytest.approx(TARGET_WACC, rel=1e-9)


# --- Acciones diluidas (Treasury Stock Method) ------------------------------

def test_diluted_shares_no_dilutive_options():
    net_dilutive, repurchased = treasury_stock_method(186.63, [])
    assert net_dilutive == 0
    assert repurchased == 0
    diluted = diluted_shares_outstanding(10495.566881, net_dilutive, other_dilutive_securities=380.5)
    assert diluted == pytest.approx(TARGET_DILUTED_SHARES, rel=1e-9)


def test_treasury_stock_method_only_counts_in_the_money_options():
    tranches = [
        OptionTranche(shares_outstanding=100, exercise_price=50),   # in the money
        OptionTranche(shares_outstanding=200, exercise_price=500),  # out of the money
    ]
    net_dilutive, repurchased = treasury_stock_method(current_price=186.63, tranches=tranches)
    # Solo el primer tramo diluye: 100 acciones a $50, recompradas con lo recaudado
    expected_repurchased = (100 * 50) / 186.63
    assert repurchased == pytest.approx(expected_repurchased)
    assert net_dilutive == pytest.approx(100 - expected_repurchased)


# --- UFCF y descuento --------------------------------------------------------

def test_unlevered_fcf_matches_excel():
    ufcf = [unlevered_fcf(e, t, d, c, n)
            for e, t, d, c, n in zip(EBIT, TAX_RATE, D_AND_A, CAPEX, CHANGE_IN_NWC)]
    for value, target in zip(ufcf, TARGET_UFCF):
        assert value == pytest.approx(target, rel=1e-9)


def test_discount_periods_stub_mid_year_convention():
    periods = discount_periods(6, stub_fraction=TARGET_STUB)
    target = [0.08333333333333333, 0.6666666666666666, 1.6666666666666665,
              2.6666666666666665, 3.6666666666666665, 4.666666666666666]
    for value, expected in zip(periods, target):
        assert value == pytest.approx(expected, rel=1e-9)


def test_discount_periods_rejects_invalid_stub():
    with pytest.raises(ValueError):
        discount_periods(3, stub_fraction=0)
    with pytest.raises(ValueError):
        discount_periods(3, stub_fraction=1.5)


# --- Pipeline completo --------------------------------------------------------

def test_full_dcf_pure_gordon_growth_is_internally_consistent():
    """Sin múltiplo de salida (gordon_weight=1.0 por defecto), el motor debe
    dar un precio objetivo MENOR que el modelo real (que combina Gordon Growth
    con múltiplo de salida). Es la metodología por defecto del blueprint."""
    inputs = DCFInputs(
        ebit=EBIT, tax_rate=TAX_RATE, d_and_a=D_AND_A, capex=CAPEX,
        change_in_nwc=CHANGE_IN_NWC, wacc=TARGET_WACC,
        terminal_growth_rate=TARGET_TGR, stub_fraction=TARGET_STUB,
        cash=89092, total_debt=54889, diluted_shares=TARGET_DILUTED_SHARES,
    )
    result = run_dcf(inputs)

    for value, target in zip(result.unlevered_fcf, TARGET_UFCF):
        assert value == pytest.approx(target, rel=1e-9)
    for value, target in zip(result.pv_unlevered_fcf, TARGET_PV_UFCF):
        assert value == pytest.approx(target, rel=1e-9)

    assert result.exit_multiple_terminal_value is None
    assert result.implied_share_price < TARGET_IMPLIED_PRICE
    assert result.implied_share_price == pytest.approx(180.94503490576435, rel=1e-9)


def test_full_dcf_blended_terminal_value_matches_excel_exactly():
    """Con el múltiplo de salida real de Comps (EV/EBITDA) y el mismo peso
    80/20 Gordon/múltiplo que usa el modelo por segmento, la versión
    consolidada (sin desglose de segmentos) reproduce EXACTAMENTE el precio
    objetivo del modelo Excel real ($216.41), porque el modelo de referencia
    aplica el mismo peso y el mismo múltiplo a los tres segmentos (AWS,
    North America, International) -- por linealidad, blend(suma) = suma(blend)."""
    inputs = DCFInputs(
        ebit=EBIT, tax_rate=TAX_RATE, d_and_a=D_AND_A, capex=CAPEX,
        change_in_nwc=CHANGE_IN_NWC, wacc=TARGET_WACC,
        terminal_growth_rate=TARGET_TGR, stub_fraction=TARGET_STUB,
        cash=89092, total_debt=54889, diluted_shares=TARGET_DILUTED_SHARES,
        terminal_ev_ebitda_multiple=TARGET_EXIT_MULTIPLE, gordon_weight=0.8,
    )
    result = run_dcf(inputs)
    assert result.implied_share_price == pytest.approx(TARGET_IMPLIED_PRICE, rel=1e-9)


def test_dcf_inputs_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        DCFInputs(
            ebit=[1, 2], tax_rate=[0.2], d_and_a=[1, 2], capex=[1, 2],
            change_in_nwc=[1, 2], wacc=0.08, terminal_growth_rate=0.02,
        )


def test_gordon_growth_requires_wacc_above_terminal_growth():
    inputs = DCFInputs(
        ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
        wacc=0.02, terminal_growth_rate=0.03, diluted_shares=1,
    )
    with pytest.raises(ValueError):
        run_dcf(inputs)


def test_gordon_growth_warns_on_thin_wacc_growth_spread():
    """WACC-g = 2% < MIN_PRUDENT_WACC_GROWTH_SPREAD (3%) -- caso real
    encontrado con PG/JNJ (beta bajo -> WACC bajo, g fijo en 2.5%)."""
    from engine.valuation import gordon_growth_terminal_value

    with pytest.warns(UserWarning, match="margen prudente"):
        gordon_growth_terminal_value(final_year_fcf=100, wacc_=0.045, terminal_growth_rate=0.025)


def test_gordon_growth_does_not_warn_on_healthy_spread():
    from engine.valuation import gordon_growth_terminal_value
    import warnings as warnings_module

    with warnings_module.catch_warnings():
        warnings_module.simplefilter("error")
        gordon_growth_terminal_value(final_year_fcf=100, wacc_=0.09, terminal_growth_rate=0.025)


# --- Matriz de sensibilidad WACC x g -----------------------------------------

def _amzn_blended_inputs() -> DCFInputs:
    return DCFInputs(
        ebit=EBIT, tax_rate=TAX_RATE, d_and_a=D_AND_A, capex=CAPEX,
        change_in_nwc=CHANGE_IN_NWC, wacc=TARGET_WACC,
        terminal_growth_rate=TARGET_TGR, stub_fraction=TARGET_STUB,
        cash=89092, total_debt=54889, diluted_shares=TARGET_DILUTED_SHARES,
        terminal_ev_ebitda_multiple=TARGET_EXIT_MULTIPLE, gordon_weight=0.8,
    )


def test_sensitivity_matrix_center_cell_matches_single_run_dcf():
    inputs = _amzn_blended_inputs()
    matrix = sensitivity_matrix(inputs, wacc_values=[TARGET_WACC], growth_values=[TARGET_TGR])
    assert matrix.implied_share_price[0][0] == pytest.approx(TARGET_IMPLIED_PRICE, rel=1e-9)


def test_sensitivity_matrix_shape_matches_inputs():
    inputs = _amzn_blended_inputs()
    wacc_values = [0.07, 0.08, 0.09]
    growth_values = [0.015, 0.025, 0.035]
    matrix = sensitivity_matrix(inputs, wacc_values, growth_values)
    assert len(matrix.implied_share_price) == 3
    assert all(len(row) == 3 for row in matrix.implied_share_price)
    assert matrix.wacc_values == wacc_values
    assert matrix.growth_values == growth_values


def test_sensitivity_matrix_price_decreases_with_wacc_for_fixed_growth():
    inputs = _amzn_blended_inputs()
    matrix = sensitivity_matrix(inputs, wacc_values=[0.07, 0.08, 0.09], growth_values=[0.025])
    prices = [row[0] for row in matrix.implied_share_price]
    assert prices[0] > prices[1] > prices[2]


def test_sensitivity_matrix_price_increases_with_growth_for_fixed_wacc():
    inputs = _amzn_blended_inputs()
    matrix = sensitivity_matrix(inputs, wacc_values=[0.09], growth_values=[0.015, 0.025, 0.035])
    prices = matrix.implied_share_price[0]
    assert prices[0] < prices[1] < prices[2]


def test_sensitivity_matrix_rejects_empty_axes():
    inputs = _amzn_blended_inputs()
    with pytest.raises(ValueError):
        sensitivity_matrix(inputs, wacc_values=[], growth_values=[0.025])
    with pytest.raises(ValueError):
        sensitivity_matrix(inputs, wacc_values=[0.08], growth_values=[])
