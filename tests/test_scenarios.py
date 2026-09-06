"""Tests de engine/scenarios.py con un histórico sintético donde el
margen EBIT tiene una tendencia clara (0.05, 0.05, 0.10, 0.20), para
poder afirmar a mano el resultado exacto de cada escenario.

Sesión 17 (hallazgo I16): esta misma tendencia (R²=0.83, por encima del
umbral TREND_R_SQUARED_THRESHOLD=0.70) hace que el escenario base ya NO
revierta el margen a la media histórica -- lo mantiene en el nivel
actual. D&A/CapEx/ΔNWC son planos en este histórico (sin tendencia real
que detectar en ellos), así que su comportamiento no cambia. Ver
docstring de cada test para el efecto concreto sobre cada escenario."""

from datetime import date

import pandas as pd
import pytest

from engine.projections import default_assumptions_from_history
from engine.scenarios import (
    ANALYST_SCENARIO_NAME,
    BASE_SCENARIO_NAME,
    analyst_scenario,
    base_scenario,
    bullish_scenario,
    hold_current_scenario,
    run_scenarios,
)

# Histórico con tendencia de margen: último año real = 0.20, media de los
# últimos 3 años (0.05, 0.10, 0.20) = 0.1166...
REVENUE = [1000.0, 1000.0, 1000.0, 1000.0]
HISTORY = pd.DataFrame({
    "fiscal_year": [2020, 2021, 2022, 2023],
    "revenue": REVENUE,
    "ebit": [50.0, 50.0, 100.0, 200.0],
    "d_and_a": [50.0, 50.0, 50.0, 50.0],
    "capex": [80.0, 80.0, 80.0, 80.0],
    "change_in_nwc": [None, 20.0, 20.0, 20.0],
    "tax_rate": [0.25] * 4,
})


def _base_assumptions():
    return default_assumptions_from_history(HISTORY, n_years=5, lookback_years=3)


def test_base_scenario_holds_margin_at_current_level_when_trend_detected():
    """Con la tendencia real de este histórico (R²=0.83, por encima del
    umbral), el escenario base ya no revierte el margen a la media
    (I16) -- lo mantiene en el nivel actual. D&A/CapEx/ΔNWC son planos
    en este histórico (sin variación real que detectar como tendencia),
    así que su end == start de cualquier forma."""
    base = _base_assumptions()
    scenario = base_scenario(base)
    assert scenario.assumptions is base
    assert scenario.assumptions.ebit_margin.start == pytest.approx(0.20)
    assert scenario.assumptions.ebit_margin.end == pytest.approx(0.20)
    trend = base.driver_trend_info["ebit_margin"]
    assert trend.is_override
    assert trend.r_squared == pytest.approx(0.8333, abs=1e-3)
    assert trend.historical_mean == pytest.approx((0.05 + 0.10 + 0.20) / 3)


def test_hold_current_scenario_freezes_every_driver_at_year_one_value():
    base = _base_assumptions()
    scenario = hold_current_scenario(base)
    assert scenario.assumptions.ebit_margin.start == pytest.approx(0.20)
    assert scenario.assumptions.ebit_margin.end == pytest.approx(0.20)
    assert scenario.assumptions.da_pct_revenue.end == scenario.assumptions.da_pct_revenue.start
    assert scenario.assumptions.capex_pct_revenue.end == scenario.assumptions.capex_pct_revenue.start
    assert scenario.assumptions.nwc_change_pct_revenue.end == scenario.assumptions.nwc_change_pct_revenue.start
    # el crecimiento de ingresos NO se toca en este escenario
    assert scenario.assumptions.revenue_growth == base.revenue_growth


def test_bullish_scenario_extrapolates_from_true_historical_mean_not_overridden_end():
    """Regresión (sesión 17, hallazgo I16): antes de separar
    driver_trend_info del FadeAssumption ya sobrescrito, bullish_scenario()
    leía base.ebit_margin.end para calcular cuánto extrapolar -- pero
    end ya NO es la media real cuando el trend override está activo (es
    igual a start, 0.20). Sin el fix, "already_moved" saldría 0 y
    bullish colapsaría en un duplicado exacto de "mantener nivel
    actual". Con el fix (leer driver_trend_info[...].historical_mean),
    sigue extrapolando la distancia real a la media histórica (0.1167),
    dando un resultado estrictamente mayor que hold."""
    base = _base_assumptions()
    scenario = bullish_scenario(base)
    historical_mean = base.driver_trend_info["ebit_margin"].historical_mean
    already_moved = base.ebit_margin.start - historical_mean  # 0.20 - 0.1167 = 0.0833
    assert scenario.assumptions.ebit_margin.start == pytest.approx(0.20)
    assert scenario.assumptions.ebit_margin.end == pytest.approx(0.20 + already_moved)
    assert scenario.assumptions.ebit_margin.end > 0.20  # sigue mejorando, no colapsa en "mantener"
    # CapEx/D&A/NWC se mantienen (hold), no se extrapolan también
    assert scenario.assumptions.capex_pct_revenue.end == scenario.assumptions.capex_pct_revenue.start


def test_bullish_price_above_hold_which_equals_base_when_only_margin_trends():
    """Con este histórico, solo el margen EBIT tiene tendencia real
    (D&A/CapEx/ΔNWC son planos, sin variación que detectar) -- el
    escenario base ya no revierte el margen (I16), así que su path de
    margen coincide EXACTO con "mantener nivel actual": base == hold en
    precio para este caso concreto. No es un error, es la consecuencia
    correcta de que ambos mecanismos dan el mismo `end` para el único
    driver que varía en este histórico. Alcista sigue siendo
    estrictamente mayor, porque es el único que extrapola más allá del
    nivel actual."""
    results = run_scenarios(
        HISTORY, wacc=0.09, cash=100, total_debt=50, diluted_shares=100,
        n_years=5, terminal_growth_rate=0.025, lookback_years=3,
    )
    base_price = results[BASE_SCENARIO_NAME].implied_share_price
    hold = results["Mantener nivel actual"].implied_share_price
    bullish = results["Alcista (continúa la tendencia reciente)"].implied_share_price
    assert base_price == pytest.approx(hold)
    assert hold < bullish


def test_run_scenarios_returns_all_three_named_scenarios():
    results = run_scenarios(
        HISTORY, wacc=0.09, cash=100, total_debt=50, diluted_shares=100,
    )
    assert set(results.keys()) == {
        BASE_SCENARIO_NAME,
        "Mantener nivel actual",
        "Alcista (continúa la tendencia reciente)",
    }
    assert all(r.implied_share_price > 0 for r in results.values())


def test_hold_scenario_equals_base_when_history_is_flat():
    """Si el histórico no tiene tendencia (margen constante), año1 ==
    mediaN y R²=0 (serie sin varianza, ver _detect_structural_trend) --
    base y mantener-actual deben coincidir exactamente, con o sin
    override de tendencia."""
    flat_history = pd.DataFrame({
        "fiscal_year": [2020, 2021, 2022, 2023],
        "revenue": REVENUE,
        "ebit": [200.0, 200.0, 200.0, 200.0],
        "d_and_a": [50.0] * 4,
        "capex": [80.0] * 4,
        "change_in_nwc": [None, 20.0, 20.0, 20.0],
        "tax_rate": [0.25] * 4,
    })
    results = run_scenarios(flat_history, wacc=0.09, cash=100, total_debt=50, diluted_shares=100)
    base_price = results[BASE_SCENARIO_NAME].implied_share_price
    hold = results["Mantener nivel actual"].implied_share_price
    bullish = results["Alcista (continúa la tendencia reciente)"].implied_share_price
    assert base_price == pytest.approx(hold)
    assert hold == pytest.approx(bullish)  # sin tendencia, "seguir mejorando" tampoco cambia nada


def test_run_scenarios_applies_real_stub_when_history_has_fiscal_dates():
    """Regresión (auditoría sesión 15, hallazgo C1): si el histórico trae
    fiscal_year_end_month/day, run_scenarios debe usar un stub real
    (distinto de 1.0) en vez del valor por defecto -- comparado con la
    misma valoración sin esas columnas (stub=1.0), el precio debe ser
    distinto porque el primer flujo se descuenta con un periodo distinto."""
    history_with_dates = HISTORY.copy()
    history_with_dates["fiscal_year_end_month"] = 12
    history_with_dates["fiscal_year_end_day"] = 31

    kwargs = dict(wacc=0.09, cash=100, total_debt=50, diluted_shares=100)
    results_no_stub = run_scenarios(HISTORY, **kwargs)
    results_with_stub = run_scenarios(
        history_with_dates, valuation_date=date(2023, 7, 1), **kwargs
    )

    name = BASE_SCENARIO_NAME
    assert results_with_stub[name].implied_share_price != pytest.approx(
        results_no_stub[name].implied_share_price
    )
    # a mitad de año el stub es ~0.5 -> menos de un año completo de descuento
    # en el primer flujo -> el primer periodo de descuento es menor
    assert results_with_stub[name].discount_periods[0] < results_no_stub[name].discount_periods[0]


# --- Sesión 18: supuestos del analista -----------------------------------


def test_analyst_scenario_overrides_only_specified_drivers_preserving_start():
    """Solo el margen EBIT se anula -- start (año 1, dato real) se
    conserva intacto, y los demás drivers quedan exactamente igual que en
    base_scenario() (el analista no tocó nada ahí)."""
    base = _base_assumptions()
    base_result = base_scenario(base)
    scenario = analyst_scenario(base, {"ebit_margin": 0.30}, "Espero mejora adicional por escala.")
    assert scenario.name == ANALYST_SCENARIO_NAME
    assert scenario.assumptions.ebit_margin.start == pytest.approx(base.ebit_margin.start)
    assert scenario.assumptions.ebit_margin.end == pytest.approx(0.30)
    assert scenario.assumptions.da_pct_revenue == base_result.assumptions.da_pct_revenue
    assert scenario.assumptions.capex_pct_revenue == base_result.assumptions.capex_pct_revenue
    assert scenario.assumptions.nwc_change_pct_revenue == base_result.assumptions.nwc_change_pct_revenue
    assert scenario.assumptions.revenue_growth == base.revenue_growth


@pytest.mark.parametrize("rationale", ["", "   ", None])
def test_analyst_scenario_raises_without_rationale(rationale):
    base = _base_assumptions()
    with pytest.raises(ValueError, match="justificación"):
        analyst_scenario(base, {"ebit_margin": 0.30}, rationale)


def test_analyst_scenario_empty_overrides_does_not_require_rationale():
    """Sin overrides, no hay nada que justificar -- no debe lanzar aunque
    la justificación esté vacía (defensivo: run_scenarios() ya evita
    llamar aquí en ese caso, pero la función no debe depender de eso)."""
    base = _base_assumptions()
    scenario = analyst_scenario(base, {}, "")
    assert scenario.name == ANALYST_SCENARIO_NAME


def test_analyst_scenario_name_never_collides_with_objective_scenarios():
    """Regresión directa (hallazgo del agente de planificación): un nombre
    de escenario del analista generado dinámicamente podría colisionar con
    uno de los 3 objetivos en el dict indexado por nombre de
    run_scenarios(), sobrescribiendo su DCFResult en silencio."""
    assert ANALYST_SCENARIO_NAME not in {
        BASE_SCENARIO_NAME, "Mantener nivel actual", "Alcista (continúa la tendencia reciente)",
    }


def test_analyst_scenario_revenue_growth_description_reflects_flat_design_when_not_overridden():
    """revenue_growth NUNCA revierte a una media histórica (queda plano al
    CAGR reciente por diseño) -- la descripción no debe afirmar lo
    contrario cuando no se anula (hallazgo del agente de planificación:
    reutilizar el patrón de los otros 4 drivers generaría una frase
    falsa: 'revierte hacia su media histórica')."""
    base = _base_assumptions()
    scenario = analyst_scenario(base, {"ebit_margin": 0.30}, "Justificación de prueba.")
    assert "permanece plano" in scenario.description
    assert "Crecimiento de ingresos: revierte hacia su media histórica" not in scenario.description


def test_analyst_scenario_description_cites_rationale_and_overridden_driver():
    base = _base_assumptions()
    scenario = analyst_scenario(base, {"capex_pct_revenue": 0.05}, "Guidance de management en el 10-K.")
    assert "CapEx % ventas" in scenario.description
    assert "0.05" in scenario.description or "5.0%" in scenario.description
    assert "Guidance de management en el 10-K." in scenario.description


def test_analyst_scenario_warns_when_override_outside_plausible_range():
    base = _base_assumptions()
    with pytest.warns(UserWarning, match="rango plausible"):
        analyst_scenario(base, {"ebit_margin": 5.0}, "Fat-finger deliberado para el test.")


def test_analyst_scenario_rejects_unknown_driver():
    base = _base_assumptions()
    with pytest.raises(ValueError, match="no reconocido"):
        analyst_scenario(base, {"wacc": 0.10}, "Driver inexistente.")


def test_run_scenarios_without_analyst_overrides_is_unchanged():
    """Retrocompatibilidad: sin analyst_overrides, run_scenarios() sigue
    devolviendo exactamente los 3 escenarios objetivos, ni uno más."""
    results = run_scenarios(HISTORY, wacc=0.09, cash=100, total_debt=50, diluted_shares=100)
    assert set(results.keys()) == {
        BASE_SCENARIO_NAME, "Mantener nivel actual", "Alcista (continúa la tendencia reciente)",
    }


def test_run_scenarios_includes_analyst_scenario_when_overrides_given():
    results = run_scenarios(
        HISTORY, wacc=0.09, cash=100, total_debt=50, diluted_shares=100,
        analyst_overrides={"ebit_margin": 0.30}, analyst_rationale="Justificación de prueba.",
    )
    assert set(results.keys()) == {
        BASE_SCENARIO_NAME, "Mantener nivel actual", "Alcista (continúa la tendencia reciente)",
        ANALYST_SCENARIO_NAME,
    }
    assert results[ANALYST_SCENARIO_NAME].implied_share_price > 0
