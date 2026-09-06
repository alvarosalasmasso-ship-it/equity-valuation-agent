"""Tests del motor de proyección (engine/projections.py).

Los casos de cagr/linear_fade/average_margin son verificables a mano
(matemática pura). default_assumptions_from_history se testea con un
histórico sintético de 4 años donde cada margen es constante a propósito,
para poder afirmar el resultado exacto; y con uno con tendencia de margen
para confirmar el mecanismo de fade "reciente -> media histórica".
"""

from datetime import date

import pandas as pd
import pytest

from engine.projections import (
    FadeAssumption,
    ProjectionAssumptions,
    average_margin,
    cagr,
    default_assumptions_from_history,
    implied_revenue_growth,
    linear_fade,
    project_financials,
    stub_fraction_from_history,
)


def test_cagr_known_case():
    # 100 -> 121 en 2 años = 10% anual compuesto
    assert cagr(100, 121, 2) == pytest.approx(0.10, rel=1e-9)


def test_cagr_rejects_non_positive_first_value():
    with pytest.raises(ValueError):
        cagr(0, 100, 2)
    with pytest.raises(ValueError):
        cagr(100, 100, 0)


def test_average_margin_ignores_missing_pairs():
    numerator = [10, None, 15, float("nan")]
    denominator = [100, 100, 150, 100]
    # pares válidos: 10/100=0.10, 15/150=0.10 -> media 0.10
    assert average_margin(numerator, denominator) == pytest.approx(0.10)


def test_average_margin_raises_when_no_valid_pairs():
    with pytest.raises(ValueError):
        average_margin([None, None], [1, 2])


def test_linear_fade_interpolates_from_start_to_end_inclusive():
    faded = linear_fade(0.10, 0.02, 5)
    assert faded[0] == pytest.approx(0.10)
    assert faded[-1] == pytest.approx(0.02)
    assert len(faded) == 5
    # paso constante
    steps = [faded[i + 1] - faded[i] for i in range(len(faded) - 1)]
    assert all(s == pytest.approx(steps[0]) for s in steps)


def test_linear_fade_single_year_returns_end_value():
    assert linear_fade(0.10, 0.02, 1) == [0.02]


def test_fade_assumption_path_delegates_to_linear_fade():
    fade = FadeAssumption(start=0.10, end=0.02)
    assert fade.path(5) == linear_fade(0.10, 0.02, 5)


def test_project_financials_flat_when_start_equals_end():
    assumptions = ProjectionAssumptions(
        n_years=3,
        revenue_growth=FadeAssumption(0.10, 0.10),  # sin fade: crecimiento constante 10%
        ebit_margin=FadeAssumption(0.20, 0.20),
        da_pct_revenue=FadeAssumption(0.05, 0.05),
        capex_pct_revenue=FadeAssumption(0.08, 0.08),
        nwc_change_pct_revenue=FadeAssumption(0.02, 0.02),
        tax_rate=0.21,
    )
    result = project_financials(last_actual_revenue=1000, assumptions=assumptions)

    expected_revenue = [1100.0, 1210.0, 1331.0]
    for value, expected in zip(result.revenue, expected_revenue):
        assert value == pytest.approx(expected)

    assert result.ebit[0] == pytest.approx(1100.0 * 0.20)
    assert result.d_and_a[1] == pytest.approx(1210.0 * 0.05)
    assert result.capex[2] == pytest.approx(1331.0 * 0.08)
    assert result.change_in_nwc[0] == pytest.approx(1100.0 * 0.02)
    assert result.tax_rate == [0.21, 0.21, 0.21]


def test_project_financials_fades_margin_linearly():
    """EBIT margin en fade de 0.20 (año1) a 0.10 (año N=3): 0.20, 0.15, 0.10."""
    assumptions = ProjectionAssumptions(
        n_years=3,
        revenue_growth=FadeAssumption(0.0, 0.0),  # revenue plano para aislar el efecto del margen
        ebit_margin=FadeAssumption(0.20, 0.10),
        da_pct_revenue=FadeAssumption(0.0, 0.0),
        capex_pct_revenue=FadeAssumption(0.0, 0.0),
        nwc_change_pct_revenue=FadeAssumption(0.0, 0.0),
        tax_rate=0.21,
    )
    result = project_financials(last_actual_revenue=1000, assumptions=assumptions)
    assert result.revenue == [1000.0, 1000.0, 1000.0]
    assert result.ebit == pytest.approx([200.0, 150.0, 100.0])


def _synthetic_flat_history() -> pd.DataFrame:
    # 4 años, revenue creciendo 10%/año desde 1000, márgenes CONSTANTES
    # a propósito para poder afirmar el resultado exacto de las medias.
    revenue = [1000.0, 1100.0, 1210.0, 1331.0]
    return pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": revenue,
        "ebit": [r * 0.20 for r in revenue],
        "d_and_a": [r * 0.05 for r in revenue],
        "capex": [r * 0.08 for r in revenue],
        "change_in_nwc": [None] + [r * 0.02 for r in revenue[1:]],
        "tax_rate": [0.25, 0.25, 0.25, 0.25],
    })


def test_default_assumptions_flat_history_collapses_fade_to_constant():
    """Con márgenes históricos constantes, año1 (reciente) == añoN (media),
    así que el fade colapsa a un valor plano — mismo comportamiento que
    antes de introducir el mecanismo de fade."""
    history = _synthetic_flat_history()
    assumptions = default_assumptions_from_history(
        history, n_years=5, lookback_years=3
    )

    assert assumptions.ebit_margin.start == pytest.approx(0.20)
    assert assumptions.ebit_margin.end == pytest.approx(0.20)
    assert assumptions.da_pct_revenue.start == pytest.approx(0.05)
    assert assumptions.capex_pct_revenue.start == pytest.approx(0.08)
    assert assumptions.nwc_change_pct_revenue.start == pytest.approx(0.02)
    assert assumptions.tax_rate == pytest.approx(0.25)
    assert assumptions.revenue_growth.start == pytest.approx(0.10, rel=1e-6)
    assert assumptions.n_years == 5


def test_default_assumptions_revenue_growth_is_flat_not_faded_to_terminal_rate():
    """Verificado contra el Excel de referencia (docs/METHODOLOGY.md
    sección 14): el analista mantiene el crecimiento prácticamente plano
    durante los 6 años de previsión explícita (~10-11%), y solo lo hace
    converger a la tasa terminal en la fórmula de Gordon Growth -- nunca
    dentro del horizonte explícito. Por eso revenue_growth debe salir
    plano (start == end == CAGR reciente), sin depender de ninguna tasa
    terminal (que ya ni siquiera es un parámetro de esta función)."""
    revenue = [1000.0, 1210.0, 1464.1, 1771.56]  # CAGR ~21% sostenido
    history = pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": revenue,
        "ebit": [r * 0.20 for r in revenue],
        "d_and_a": [r * 0.05 for r in revenue],
        "capex": [r * 0.08 for r in revenue],
        "change_in_nwc": [None] + [r * 0.02 for r in revenue[1:]],
        "tax_rate": [0.25] * 4,
    })
    assumptions = default_assumptions_from_history(history, lookback_years=3)
    assert assumptions.revenue_growth.start == pytest.approx(assumptions.revenue_growth.end)
    assert assumptions.revenue_growth.start == pytest.approx(0.21, rel=1e-3)
    # el crecimiento proyectado en TODOS los años del horizonte es el mismo
    path = assumptions.revenue_growth.path(5)
    assert all(g == pytest.approx(path[0]) for g in path)


def test_default_assumptions_fades_margin_from_recent_actual_to_historical_average():
    """Histórico con tendencia de margen: 0.05, 0.05, 0.10, 0.20 (últimos
    3 años usados: 0.05, 0.10, 0.20 -> media = 0.1166...).
    Año 1 debe ser el margen del ÚLTIMO año real (0.20), año N la media
    de la ventana (no el propio 0.20) -> el fade captura la tendencia
    reciente en vez de diluirla en un promedio plano.

    z modificado de este caso (0.05, 0.10 de referencia, 0.20 candidato)
    = 3.37, deliberadamente justo por DEBAJO del umbral de outlier (3.5,
    ver test_default_assumptions_warns_but_keeps_real_anchor_when_last_year_is_a_statistical_outlier)
    -- este caso no debe disparar ni siquiera el aviso."""
    revenue = [1000.0, 1000.0, 1000.0, 1000.0]
    history = pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": revenue,
        "ebit": [50.0, 50.0, 100.0, 200.0],
        "d_and_a": [50.0] * 4,
        "capex": [80.0] * 4,
        "change_in_nwc": [None, 20.0, 20.0, 20.0],
        "tax_rate": [0.25] * 4,
    })
    assumptions = default_assumptions_from_history(history, lookback_years=3)

    expected_average = (0.05 + 0.10 + 0.20) / 3
    assert assumptions.ebit_margin.start == pytest.approx(0.20)  # último año real
    assert assumptions.ebit_margin.end == pytest.approx(expected_average)
    assert assumptions.ebit_margin.start > assumptions.ebit_margin.end  # capta la tendencia alcista


def test_default_assumptions_warns_but_keeps_real_anchor_when_last_year_is_a_statistical_outlier():
    """Reproduce el mecanismo real documentado en docs/METHODOLOGY.md
    sección 9 (JNJ: margen EBIT 2025 = 35.6% frente a 18.6%/19.6% en
    2023/2024, un ítem no recurrente por la escisión de Kenvue). Con
    margen histórico 0.19, 0.21, 0.55 (últimos 3 años), el último año es
    un outlier estadístico (z modificado >> 3.5) frente a los dos
    previos -- se emite un aviso explícito, pero el año 1 del fade sigue
    siendo el 0.55 real: probado con el universo piloto completo, un
    z-score de muestra pequeña no distingue esto de una tendencia
    estructural real (p.ej. el CapEx de MSFT/META), así que el motor
    nunca sustituye el ancla en automático (ver docstring de
    _margin_fade_from_recent_to_average)."""
    revenue = [1000.0, 1000.0, 1000.0, 1000.0]
    history = pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": revenue,
        "ebit": [200.0, 190.0, 210.0, 550.0],
        "d_and_a": [50.0] * 4,
        "capex": [80.0] * 4,
        "change_in_nwc": [None, 20.0, 20.0, 20.0],
        "tax_rate": [0.25] * 4,
    })
    with pytest.warns(UserWarning, match="outlier"):
        assumptions = default_assumptions_from_history(history, lookback_years=3)

    assert assumptions.ebit_margin.start == pytest.approx(0.55)  # último año real, sin sustituir
    assert assumptions.ebit_margin.end == pytest.approx((0.19 + 0.21 + 0.55) / 3)


def test_default_assumptions_does_not_flag_outlier_with_insufficient_reference_years():
    """Con lookback_years=2 solo hay 1 año de referencia frente al
    candidato -- no hay base estadística para juzgar un outlier (ver
    docstring de _detect_anchor_outlier), así que el ancla debe seguir
    siendo el último año real tal cual, sin aviso, aunque la diferencia
    sea enorme."""
    revenue = [1000.0, 1000.0, 1000.0]
    history = pd.DataFrame({
        "fiscal_year": [2021, 2022, 2023],
        "revenue": revenue,
        "ebit": [190.0, 210.0, 550.0],
        "d_and_a": [50.0] * 3,
        "capex": [80.0] * 3,
        "change_in_nwc": [20.0, 20.0, 20.0],
        "tax_rate": [0.25] * 3,
    })
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assumptions = default_assumptions_from_history(history, lookback_years=2)

    assert assumptions.ebit_margin.start == pytest.approx(0.55)  # último año real, tal cual


def test_default_assumptions_requires_at_least_two_revenue_points():
    history = pd.DataFrame({"revenue": [1000.0], "ebit": [200.0], "d_and_a": [50.0],
                             "capex": [80.0], "change_in_nwc": [None], "tax_rate": [0.25]})
    with pytest.raises(ValueError):
        default_assumptions_from_history(history)


def test_default_assumptions_margin_window_excludes_extra_older_year():
    """Regresión: el CAGR de ingresos necesita lookback_years+1 puntos
    (los extremos), pero la ventana de márgenes debe usar EXACTAMENTE
    lookback_years puntos, no uno más. Aquí el año más antiguo (2020)
    tiene un margen muy distinto (0.50) al resto (0.20); con
    lookback_years=3 no debe entrar en la ventana de márgenes."""
    revenue = [1000.0, 1100.0, 1210.0, 1331.0]
    history = pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": revenue,
        "ebit": [500.0, 220.0, 242.0, 266.2],  # 2020 margen=0.50, resto=0.20
        "d_and_a": [r * 0.05 for r in revenue],
        "capex": [r * 0.08 for r in revenue],
        "change_in_nwc": [None] + [r * 0.02 for r in revenue[1:]],
        "tax_rate": [0.25, 0.25, 0.25, 0.25],
    })
    assumptions = default_assumptions_from_history(history, lookback_years=3)
    assert assumptions.ebit_margin.start == pytest.approx(0.20)
    assert assumptions.ebit_margin.end == pytest.approx(0.20)


def test_projection_pipeline_feeds_directly_into_dcf_inputs():
    """Integración: los outputs de project_financials deben ser
    directamente aceptados por engine.valuation.DCFInputs sin transformar."""
    from engine.valuation import DCFInputs, run_dcf

    history = _synthetic_flat_history()
    assumptions = default_assumptions_from_history(history, n_years=5)
    projection = project_financials(history["revenue"].iloc[-1], assumptions)

    inputs = DCFInputs(
        ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
        capex=projection.capex, change_in_nwc=projection.change_in_nwc,
        wacc=0.09, terminal_growth_rate=0.025, cash=100, total_debt=50, diluted_shares=100,
    )
    result = run_dcf(inputs)
    assert result.implied_share_price > 0


# --- stub_fraction_from_history (auditoría sesión 15, hallazgo C1) ---------

def test_stub_fraction_from_history_returns_1_when_no_fiscal_date_columns():
    """Histórico sintético (como el resto de este archivo) no trae
    fiscal_year_end_month/day -- debe degradar a 1.0, el comportamiento
    de antes de la auditoría, no lanzar una excepción."""
    history = _synthetic_flat_history()
    assert "fiscal_year_end_month" not in history.columns
    stub = stub_fraction_from_history(history, valuation_date=date(2024, 7, 1))
    assert stub == pytest.approx(1.0)


# --- Reverse DCF: implied_revenue_growth -------------------------------------

def _dcf_kwargs() -> dict:
    return dict(
        wacc=0.09, terminal_growth_rate=0.025, cash=100, total_debt=50,
        diluted_shares=100, terminal_ev_ebitda_multiple=None, gordon_weight=1.0,
    )


def test_implied_revenue_growth_recovers_the_assumed_growth_when_target_is_the_base_price():
    """Round-trip: si el precio objetivo es exactamente el que produce el
    propio escenario base (10% de crecimiento, ver
    test_default_assumptions_flat_history_collapses_fade_to_constant),
    resolver hacia atrás debe devolver ese mismo 10% -- la prueba de
    rigor estándar de un solver, ida y vuelta."""
    history = _synthetic_flat_history()
    assumptions = default_assumptions_from_history(history, n_years=5, lookback_years=3)
    last_revenue = history["revenue"].iloc[-1]

    from engine.valuation import DCFInputs, run_dcf
    kwargs = _dcf_kwargs()
    projection = project_financials(last_revenue, assumptions)
    base_price = run_dcf(DCFInputs(
        ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
        capex=projection.capex, change_in_nwc=projection.change_in_nwc, **kwargs,
    )).implied_share_price

    result = implied_revenue_growth(last_revenue, assumptions, kwargs, target_price=base_price)
    assert result.implied_growth == pytest.approx(0.10, abs=1e-4)
    assert result.assumed_growth == pytest.approx(0.10, abs=1e-6)
    assert result.gap == pytest.approx(0.0, abs=1e-4)


def test_implied_revenue_growth_higher_target_price_implies_higher_growth():
    history = _synthetic_flat_history()
    assumptions = default_assumptions_from_history(history, n_years=5, lookback_years=3)
    last_revenue = history["revenue"].iloc[-1]
    kwargs = _dcf_kwargs()

    low = implied_revenue_growth(last_revenue, assumptions, kwargs, target_price=40.0)
    high = implied_revenue_growth(last_revenue, assumptions, kwargs, target_price=120.0)
    assert high.implied_growth > low.implied_growth


def test_implied_revenue_growth_raises_informative_error_when_target_out_of_range():
    """Un precio objetivo inalcanzable incluso en el extremo superior del
    rango de búsqueda debe fallar con un mensaje que incluya el precio
    real alcanzable -- esa cifra es en sí misma informativa (cuantifica
    la brecha), no un error genérico a esconder."""
    history = _synthetic_flat_history()
    assumptions = default_assumptions_from_history(history, n_years=5, lookback_years=3)
    last_revenue = history["revenue"].iloc[-1]
    kwargs = _dcf_kwargs()

    with pytest.raises(ValueError, match="fuera del rango alcanzable"):
        implied_revenue_growth(last_revenue, assumptions, kwargs, target_price=1_000_000.0)


def test_stub_fraction_from_history_uses_last_year_fiscal_date():
    history = _synthetic_flat_history()
    history["fiscal_year_end_month"] = 12
    history["fiscal_year_end_day"] = 31
    stub = stub_fraction_from_history(history, valuation_date=date(2023, 7, 1))
    assert stub == pytest.approx(183 / 365)  # mismo caso que test_compute_stub_fraction_at_midyear


def test_stub_fraction_from_history_defaults_to_today_when_no_valuation_date():
    """Sin valuation_date explícito, usa date.today() -- solo se
    comprueba que no lanza y devuelve un valor en rango válido."""
    history = _synthetic_flat_history()
    history["fiscal_year_end_month"] = 12
    history["fiscal_year_end_day"] = 31
    stub = stub_fraction_from_history(history)
    assert 0 < stub <= 1
