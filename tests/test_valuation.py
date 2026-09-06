"""Tests del motor de valoración contra un caso conocido a mano.

Caso de referencia: Amazon (AMZN), extraído de "Advanced DCF.xlsx"
(modelo profesional de un ex-banquero de JP Morgan usado como fuente
de verdad para este proyecto). Todos los "target" de este archivo son
valores calculados por ese Excel (openpyxl, data_only=True), no
inventados. Ver docs/METHODOLOGY.md para el mapeo celda -> función.
"""

from dataclasses import replace
from datetime import date

import pytest

from engine.valuation import (
    DCFInputs,
    OptionTranche,
    compute_stub_fraction,
    cost_of_debt,
    cost_of_equity,
    diluted_shares_outstanding,
    discount_periods,
    implied_terminal_growth_rate,
    relever_beta,
    run_dcf,
    sensitivity_matrix,
    solve_for_target_price,
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


def test_cost_of_debt_returns_zero_for_debt_free_company():
    """Auditoría sesión 17: antes hacía crashear con ZeroDivisionError.
    0.0 es seguro porque wacc() pondera por total_debt/(total_debt+
    market_cap), que también es 0 en este caso -- nunca influye en el
    resultado final."""
    assert cost_of_debt(0.0, 0.0) == 0.0
    assert cost_of_debt(100.0, -5.0) == 0.0  # deuda neta negativa no debería llegar aquí, pero no debe crashear


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


# --- compute_stub_fraction (auditoría sesión 15, hallazgo C1) ---------------

def test_compute_stub_fraction_at_start_of_fiscal_year_gives_full_year():
    """Si la fecha de valoración es exactamente el cierre del ejercicio
    ANTERIOR (el instante en que arranca el nuevo año fiscal), el stub
    del año que empieza debe ser 1.0 completo -- nunca 0.0, que violaría
    la restricción de discount_periods()."""
    stub = compute_stub_fraction(
        fiscal_year_end_month=12, fiscal_year_end_day=31,
        valuation_date=date(2022, 12, 31),
    )
    assert stub == pytest.approx(1.0)


def test_compute_stub_fraction_at_midyear():
    """Valorando el 1 de julio de 2023 con cierre fiscal 31 de diciembre:
    quedan 183 días de un ejercicio de 365 (2023 no es bisiesto)."""
    stub = compute_stub_fraction(
        fiscal_year_end_month=12, fiscal_year_end_day=31,
        valuation_date=date(2023, 7, 1),
    )
    assert stub == pytest.approx(183 / 365)


def test_compute_stub_fraction_non_december_fiscal_year_end():
    """Cierre fiscal en junio (como MSFT): valorando el 31 de marzo de
    2025, quedan 91 días hasta el 30 de junio de 2025, de un ejercicio
    de 365 días (30-jun-2024 a 30-jun-2025, ninguno bisiesto en medio)."""
    stub = compute_stub_fraction(
        fiscal_year_end_month=6, fiscal_year_end_day=30,
        valuation_date=date(2025, 3, 31),
    )
    assert stub == pytest.approx(91 / 365)


def test_compute_stub_fraction_handles_february_29_fallback():
    """Cierre fiscal el 29 de febrero (bisiesto) valorado en un año NO
    bisiesto -- no debe lanzar ValueError, cae al día 28."""
    stub = compute_stub_fraction(
        fiscal_year_end_month=2, fiscal_year_end_day=29,
        valuation_date=date(2025, 1, 1),
    )
    assert 0 < stub <= 1


def test_compute_stub_fraction_result_feeds_directly_into_discount_periods():
    """Integración: el resultado siempre debe ser válido para
    discount_periods() (0, 1], nunca fuera de rango."""
    for valuation_date in [date(2024, 1, 1), date(2024, 6, 15), date(2024, 12, 31)]:
        stub = compute_stub_fraction(12, 31, valuation_date)
        periods = discount_periods(5, stub_fraction=stub)  # no debe lanzar
        assert len(periods) == 5


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


def test_dcf_inputs_rejects_non_positive_wacc():
    """Auditoría sesión 15, hallazgo M3: nunca ocurre viniendo del
    pipeline real, pero DCFInputs no lo garantizaba si se construye de
    forma directa -- un WACC<=0 no es un escenario válido de DCF."""
    with pytest.raises(ValueError, match="wacc"):
        DCFInputs(
            ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
            wacc=0.0, terminal_growth_rate=0.02, diluted_shares=1,
        )


def test_dcf_inputs_rejects_gordon_weight_above_one():
    with pytest.raises(ValueError, match="gordon_weight"):
        DCFInputs(
            ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
            wacc=0.08, terminal_growth_rate=0.02, diluted_shares=1, gordon_weight=1.5,
        )


def test_dcf_inputs_rejects_negative_gordon_weight():
    with pytest.raises(ValueError, match="gordon_weight"):
        DCFInputs(
            ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
            wacc=0.08, terminal_growth_rate=0.02, diluted_shares=1, gordon_weight=-0.1,
        )


def test_dcf_inputs_accepts_gordon_weight_boundaries():
    """0.0 y 1.0 son válidos (Gordon puro o múltiplo puro), no deben
    rechazarse por un error de comparación estricta vs. inclusiva."""
    for boundary in (0.0, 1.0):
        DCFInputs(
            ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
            wacc=0.08, terminal_growth_rate=0.02, diluted_shares=1, gordon_weight=boundary,
        )


def test_gordon_growth_requires_wacc_above_terminal_growth():
    inputs = DCFInputs(
        ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
        wacc=0.02, terminal_growth_rate=0.03, diluted_shares=1,
    )
    with pytest.raises(ValueError):
        run_dcf(inputs)


def test_run_dcf_skips_gordon_growth_when_weight_is_zero_and_exit_multiple_present():
    """Auditoría sesión 15, hallazgo M4: con gordon_weight=0 y un múltiplo
    de salida disponible, Gordon Growth es irrelevante para el resultado
    -- antes se calculaba igual de todos modos. Aquí wacc<=terminal_growth_rate
    haría que gordon_growth_terminal_value() lance ValueError si se
    llamara; con el fix, run_dcf() no debe siquiera intentarlo."""
    inputs = DCFInputs(
        ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
        wacc=0.02, terminal_growth_rate=0.03, diluted_shares=1,
        terminal_ev_ebitda_multiple=10.0, gordon_weight=0.0,
    )
    result = run_dcf(inputs)
    assert result.gordon_terminal_value == 0.0
    assert result.terminal_value == pytest.approx(result.exit_multiple_terminal_value)


def test_run_dcf_skips_gordon_growth_warning_when_weight_is_zero():
    """Mismo hallazgo M4, pero para el caso de aviso (no error): con
    gordon_weight=0, el margen WACC-g estrecho de Gordon es irrelevante
    -- no debe emitirse el aviso de 'margen prudente' para un componente
    que no cuenta en el resultado."""
    import warnings as warnings_module

    inputs = DCFInputs(
        ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
        wacc=0.045, terminal_growth_rate=0.025, diluted_shares=1,
        terminal_ev_ebitda_multiple=10.0, gordon_weight=0.0,
    )
    with warnings_module.catch_warnings():
        warnings_module.simplefilter("error")
        run_dcf(inputs)  # no debe lanzar (ni avisar)


def test_run_dcf_still_computes_gordon_growth_when_no_exit_multiple_even_with_zero_weight():
    """Si no hay múltiplo de salida, Gordon Growth es la única fuente de
    valor terminal posible -- debe calcularse sin importar gordon_weight
    (que en ese caso no tiene ningún múltiplo con el que ponderar)."""
    inputs = DCFInputs(
        ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
        wacc=0.08, terminal_growth_rate=0.02, diluted_shares=1,
        terminal_ev_ebitda_multiple=None, gordon_weight=0.0,
    )
    result = run_dcf(inputs)
    assert result.gordon_terminal_value > 0.0
    assert result.terminal_value == pytest.approx(result.gordon_terminal_value)


def test_run_dcf_still_computes_gordon_growth_when_weight_is_positive():
    """Con gordon_weight>0 y múltiplo de salida presente, Gordon SÍ debe
    calcularse y contribuir al blend -- no solo se desactivó por error."""
    inputs = DCFInputs(
        ebit=[100], tax_rate=[0.2], d_and_a=[10], capex=[10], change_in_nwc=[0],
        wacc=0.08, terminal_growth_rate=0.02, diluted_shares=1,
        terminal_ev_ebitda_multiple=10.0, gordon_weight=0.5,
    )
    result = run_dcf(inputs)
    assert result.gordon_terminal_value > 0.0
    assert result.terminal_value == pytest.approx(
        0.5 * result.gordon_terminal_value + 0.5 * result.exit_multiple_terminal_value
    )


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


def test_gordon_growth_warns_on_negative_final_year_fcf():
    """Auditoría sesión 17: caso real encontrado con TSLA (ΔNWC
    proyectado crece más rápido que EBIT+D&A-CapEx, UFCF negativo todos
    los años del horizonte) y BA (margen EBIT revierte a una media
    histórica con años de pérdidas reales). El valor terminal sale
    negativo -- matemáticamente consistente, pero merece un aviso
    explícito, no pasar desapercibido como si fuera un resultado normal."""
    from engine.valuation import gordon_growth_terminal_value

    with pytest.warns(UserWarning, match="negativo"):
        result = gordon_growth_terminal_value(final_year_fcf=-100, wacc_=0.09, terminal_growth_rate=0.025)
    assert result < 0


def test_gordon_growth_warns_on_zero_final_year_fcf():
    from engine.valuation import gordon_growth_terminal_value

    with pytest.warns(UserWarning, match="cero"):
        result = gordon_growth_terminal_value(final_year_fcf=0, wacc_=0.09, terminal_growth_rate=0.025)
    assert result == 0.0


def test_gordon_growth_does_not_warn_on_positive_final_year_fcf():
    from engine.valuation import gordon_growth_terminal_value
    import warnings as warnings_module

    with warnings_module.catch_warnings():
        warnings_module.simplefilter("error")
        gordon_growth_terminal_value(final_year_fcf=1.0, wacc_=0.09, terminal_growth_rate=0.025)


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


# --- Reverse DCF: solve_for_target_price -------------------------------------

def test_solve_for_target_price_recovers_root_of_simple_increasing_function():
    """Caso hand-verificable, sin nada financiero de por medio: x**2 en
    [0, 10] para target=64 -> x=8 exacto."""
    root = solve_for_target_price(lambda x: x ** 2, target_price=64.0,
                                   lower_bound=0.0, upper_bound=10.0, price_tolerance=1e-6)
    assert root == pytest.approx(8.0, abs=1e-3)


def test_solve_for_target_price_rejects_non_increasing_function():
    with pytest.raises(ValueError, match="creciente"):
        solve_for_target_price(lambda x: -x, target_price=5.0, lower_bound=0.0, upper_bound=10.0)


def test_solve_for_target_price_rejects_target_outside_reachable_range():
    with pytest.raises(ValueError, match="fuera del rango alcanzable"):
        solve_for_target_price(lambda x: x, target_price=100.0, lower_bound=0.0, upper_bound=10.0)


# --- Reverse DCF: implied_terminal_growth_rate -------------------------------

def test_implied_terminal_growth_rate_recovers_known_growth_rate():
    """Round-trip sobre el caso AMZN real: el precio objetivo generado con
    TARGET_TGR debe, al resolver hacia atrás, devolver TARGET_TGR de
    nuevo -- la prueba de rigor estándar de un solver: ida y vuelta."""
    inputs = _amzn_blended_inputs()
    solved_g = implied_terminal_growth_rate(inputs, target_price=TARGET_IMPLIED_PRICE)
    assert solved_g == pytest.approx(TARGET_TGR, abs=1e-4)


def test_implied_terminal_growth_rate_higher_target_price_implies_higher_growth():
    """Monotonía de negocio, no solo del solver: pedir un precio objetivo
    más alto debe implicar un crecimiento de mercado más exigente."""
    inputs = _amzn_blended_inputs()
    low_target_g = implied_terminal_growth_rate(inputs, target_price=TARGET_IMPLIED_PRICE * 0.8)
    high_target_g = implied_terminal_growth_rate(inputs, target_price=TARGET_IMPLIED_PRICE * 1.2)
    assert high_target_g > low_target_g


def test_implied_terminal_growth_rate_rejects_when_gordon_weight_is_zero():
    """Auditoría sesión 15, hallazgo M4: con gordon_weight=0 y múltiplo de
    salida presente, g no afecta al precio -- no hay nada que resolver."""
    inputs = replace(_amzn_blended_inputs(), gordon_weight=0.0)
    with pytest.raises(ValueError, match="gordon_weight"):
        implied_terminal_growth_rate(inputs, target_price=TARGET_IMPLIED_PRICE)


def test_implied_terminal_growth_rate_warns_when_solved_value_is_in_fragile_zone():
    """Si el precio objetivo solo se explica con un g muy cercano al WACC
    (spread estrecho), debe emitirse el mismo aviso que cualquier otra
    llamada a gordon_growth_terminal_value() -- no uno nuevo ni distinto."""
    inputs = replace(_amzn_blended_inputs(), terminal_ev_ebitda_multiple=None, gordon_weight=1.0)
    # Un precio deliberadamente muy alto empuja el g resuelto muy cerca del WACC.
    very_high_target = run_dcf(replace(inputs, terminal_growth_rate=0.07)).implied_share_price
    with pytest.warns(UserWarning, match="margen prudente"):
        implied_terminal_growth_rate(inputs, target_price=very_high_target)
