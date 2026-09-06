"""Tests de ai/memo_generator.py. Ninguno llama a la API de Anthropic:
generate_memo() recibe un cliente falso inyectado; build_memo_input/
build_prompt son funciones puras sobre datos ya calculados."""

import json

import numpy as np
import pandas as pd
import pytest

from ai.memo_generator import (
    MemoInput,
    ScenarioSummary,
    build_memo_input,
    build_prompt,
    generate_memo,
    run_scenarios_capturing_warnings,
)
from engine.scenarios import BASE_SCENARIO_NAME

# Histórico con tendencia de margen (igual que tests/test_scenarios.py)
HISTORY = pd.DataFrame({
    "fiscal_year": [2020, 2021, 2022, 2023],
    "revenue": [1000.0, 1000.0, 1000.0, 1000.0],
    "ebit": [50.0, 50.0, 100.0, 200.0],
    "d_and_a": [50.0, 50.0, 50.0, 50.0],
    "capex": [80.0, 80.0, 80.0, 80.0],
    "change_in_nwc": [None, 20.0, 20.0, 20.0],
    "tax_rate": [0.25] * 4,
})


class FakeResponse:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]


class FakeMessages:
    def __init__(self):
        self.last_call = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return FakeResponse("Memo redactado de prueba.")


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()


# --- run_scenarios_capturing_warnings ---------------------------------------

def test_run_scenarios_capturing_warnings_captures_thin_spread_warning():
    """WACC=0.045, g=0.025 -> spread=0.02 < MIN_PRUDENT_WACC_GROWTH_SPREAD (0.03)."""
    results, warnings_text = run_scenarios_capturing_warnings(
        HISTORY, wacc=0.045, cash=100, total_debt=50, diluted_shares=100,
        terminal_growth_rate=0.025,
    )
    assert any("margen prudente" in w for w in warnings_text)
    assert BASE_SCENARIO_NAME in results


def test_run_scenarios_capturing_warnings_no_warning_on_healthy_spread():
    """El fixture HISTORY tiene una tendencia de margen real (I16, ver
    tests/test_scenarios.py) que dispara su propio aviso, sin relación
    con el spread WACC-g -- este test verifica específicamente que NO
    hay aviso de spread estrecho con un WACC saludable, no que no haya
    avisos de ningún tipo."""
    results, warnings_text = run_scenarios_capturing_warnings(
        HISTORY, wacc=0.09, cash=100, total_debt=50, diluted_shares=100,
        terminal_growth_rate=0.025,
    )
    assert not any("margen prudente" in w for w in warnings_text)


# --- build_memo_input --------------------------------------------------------

def _sample_scenario_results():
    from engine.valuation import DCFResult
    return {
        BASE_SCENARIO_NAME: DCFResult(
            unlevered_fcf=[10], pv_unlevered_fcf=[9], discount_periods=[1],
            gordon_terminal_value=100, exit_multiple_terminal_value=None,
            terminal_value=100, pv_terminal_value=90, enterprise_value=99,
            equity_value=100, implied_share_price=50.0,
        ),
        "Alcista (continúa la tendencia reciente)": DCFResult(
            unlevered_fcf=[12], pv_unlevered_fcf=[11], discount_periods=[1],
            gordon_terminal_value=120, exit_multiple_terminal_value=None,
            terminal_value=120, pv_terminal_value=110, enterprise_value=121,
            equity_value=122, implied_share_price=70.0,
        ),
    }


def test_build_memo_input_computes_deviation_from_conservative_scenario():
    memo_input = build_memo_input(
        ticker="TEST", wacc=0.08, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={"ebit_margin_start": 0.2},
        market_price=60.0, analyst_target_price=55.0,
    )
    assert memo_input.deviation_vs_market == pytest.approx(50.0 / 60.0 - 1)
    assert memo_input.deviation_vs_consensus == pytest.approx(50.0 / 55.0 - 1)
    assert len(memo_input.scenarios) == 2


def test_build_memo_input_handles_missing_market_data():
    memo_input = build_memo_input(
        ticker="TEST", wacc=0.08, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={},
    )
    assert memo_input.deviation_vs_market is None
    assert memo_input.deviation_vs_consensus is None
    assert memo_input.ratios is None


def test_build_memo_input_includes_ratios_when_provided():
    from engine.ratios import RatioSnapshot

    snapshot = RatioSnapshot(
        fiscal_year=2025, net_margin=0.1, asset_turnover=0.5, equity_multiplier=4.0,
        roe=0.20, roic=0.15, creates_value=True, debt_to_ebitda=1.5, net_debt_to_ebitda=1.0,
        interest_coverage=7.5, current_ratio=1.5,
    )
    memo_input = build_memo_input(
        ticker="TEST", wacc=0.08, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={}, ratios=snapshot,
    )
    assert memo_input.ratios == {
        "fiscal_year": 2025, "roe": 0.20, "roic": 0.15,
        "crea_valor_roic_mayor_que_wacc": True, "debt_to_ebitda": 1.5,
        "net_debt_to_ebitda": 1.0, "interest_coverage": 7.5, "current_ratio": 1.5,
    }
    assert type(memo_input.ratios["crea_valor_roic_mayor_que_wacc"]) is bool


def test_build_memo_input_converts_numpy_bool_to_native_bool():
    """Regresión: engine.ratios.creates_value() puede devolver np.bool_
    (de una comparación numpy), que json.dumps() no serializa sin la
    salvaguarda default=str -- y con ella lo convertiría en la CADENA
    "True" en vez del booleano JSON true. Debe quedar como bool nativo."""
    from engine.ratios import RatioSnapshot

    snapshot = RatioSnapshot(
        fiscal_year=2025, net_margin=0.1, asset_turnover=0.5, equity_multiplier=4.0,
        roe=0.20, roic=0.15, creates_value=np.True_, debt_to_ebitda=1.5, net_debt_to_ebitda=1.0,
        interest_coverage=7.5, current_ratio=1.5,
    )
    memo_input = build_memo_input(
        ticker="TEST", wacc=0.08, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={}, ratios=snapshot,
    )
    assert memo_input.ratios["crea_valor_roic_mayor_que_wacc"] is True


def test_build_memo_input_represents_infinite_interest_coverage_as_text():
    """Regresión: interest_coverage=inf (empresa sin deuda) rota JSON
    estricto si se serializa tal cual (json.dumps produce el token no
    estándar 'Infinity', inválido según RFC 8259)."""
    from engine.ratios import RatioSnapshot

    snapshot = RatioSnapshot(
        fiscal_year=2025, net_margin=0.1, asset_turnover=0.5, equity_multiplier=4.0,
        roe=0.20, roic=0.15, creates_value=True, debt_to_ebitda=0.0, net_debt_to_ebitda=-0.5,
        interest_coverage=float("inf"), current_ratio=1.5,
    )
    memo_input = build_memo_input(
        ticker="TEST", wacc=0.08, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={}, ratios=snapshot,
    )
    assert memo_input.ratios["interest_coverage"] == "sin deuda (cobertura infinita)"

    _, user_prompt = build_prompt(memo_input)
    json_start = user_prompt.index("{")
    raw_json = user_prompt[json_start:]
    payload = json.loads(raw_json)  # Python acepta "Infinity" al leer (no es prueba suficiente)
    assert payload["ratios_financieros"]["interest_coverage"] == "sin deuda (cobertura infinita)"
    assert "Infinity" not in raw_json  # la prueba real: el token no estándar no debe aparecer


def test_build_memo_input_includes_implied_expectations_when_provided():
    from engine.reverse_dcf import ImpliedExpectations

    expectations = [
        ImpliedExpectations(
            target_label="Mercado", target_price=258.51,
            assumed_revenue_growth=0.117, assumed_terminal_growth=0.025,
            implied_revenue_growth=0.318, implied_terminal_growth=0.0725,
            terminal_growth_fragile=True,
        ),
        ImpliedExpectations(
            target_label="Precio absurdo", target_price=1_000_000.0,
            assumed_revenue_growth=0.117, assumed_terminal_growth=0.025,
            implied_revenue_growth=None, implied_terminal_growth=None,
        ),
    ]
    memo_input = build_memo_input(
        ticker="AMZN", wacc=0.09, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={},
        implied_expectations=expectations,
    )
    assert memo_input.implied_expectations == [
        {
            "precio_objetivo": "Mercado", "precio": 258.51,
            "crecimiento_ingresos_asumido": 0.117, "crecimiento_ingresos_implicito": 0.318,
            "gap_crecimiento_ingresos": pytest.approx(0.318 - 0.117),
            "tasa_crecimiento_terminal_asumida": 0.025, "tasa_crecimiento_terminal_implicita": 0.0725,
            "crecimiento_terminal_implicito_en_zona_fragil": True,
        },
        {
            "precio_objetivo": "Precio absurdo", "precio": 1_000_000.0,
            "crecimiento_ingresos_asumido": 0.117, "crecimiento_ingresos_implicito": None,
            "gap_crecimiento_ingresos": None,
            "tasa_crecimiento_terminal_asumida": 0.025, "tasa_crecimiento_terminal_implicita": None,
            "crecimiento_terminal_implicito_en_zona_fragil": False,
        },
    ]

    _, user_prompt = build_prompt(memo_input)
    json_start = user_prompt.index("{")
    payload = json.loads(user_prompt[json_start:])
    expectativas = payload["expectativas_implicitas_del_mercado"]
    assert expectativas[0]["crecimiento_ingresos_implicito"] == pytest.approx(0.318)
    assert expectativas[1]["crecimiento_ingresos_implicito"] is None


def test_build_memo_input_omits_implied_expectations_when_not_provided():
    memo_input = build_memo_input(
        ticker="TEST", wacc=0.08, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={},
    )
    assert memo_input.implied_expectations is None
    _, user_prompt = build_prompt(memo_input)
    json_start = user_prompt.index("{")
    payload = json.loads(user_prompt[json_start:])
    assert payload["expectativas_implicitas_del_mercado"] is None


def test_build_memo_input_includes_sensitivities_when_provided():
    from engine.sensitivity import DriverSensitivity

    sensitivities = [
        DriverSensitivity(driver="capex_pct_revenue", label="CapEx % ventas", base_value=0.184,
                           bump=0.01, base_price=104.41, price_at_bump=95.0),
        DriverSensitivity(driver="wacc", label="WACC", base_value=0.0827,
                           bump=0.01, base_price=104.41, price_at_bump=98.0),
    ]
    memo_input = build_memo_input(
        ticker="AMZN", wacc=0.0827, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={},
        sensitivities=sensitivities,
    )
    assert memo_input.sensitivities == [
        {"supuesto": "CapEx % ventas", "cambio_en_precio_por_1pp": pytest.approx((95.0 - 104.41) / 104.41)},
        {"supuesto": "WACC", "cambio_en_precio_por_1pp": pytest.approx((98.0 - 104.41) / 104.41)},
    ]

    _, user_prompt = build_prompt(memo_input)
    json_start = user_prompt.index("{")
    payload = json.loads(user_prompt[json_start:])
    assert payload["sensibilidad_del_precio_por_supuesto"][0]["supuesto"] == "CapEx % ventas"


def test_build_memo_input_omits_sensitivities_when_not_provided():
    memo_input = build_memo_input(
        ticker="TEST", wacc=0.08, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={},
    )
    assert memo_input.sensitivities is None
    _, user_prompt = build_prompt(memo_input)
    json_start = user_prompt.index("{")
    payload = json.loads(user_prompt[json_start:])
    assert payload["sensibilidad_del_precio_por_supuesto"] is None


def test_build_memo_input_includes_analyst_override_when_provided():
    """Sesión 18: analyst_overrides/analyst_rationale se reciben explícitos
    (no inferidos de scenario_results, que solo trae DCFResult sin el
    objeto Scenario -- hallazgo del agente de planificación)."""
    from engine.scenarios import ANALYST_SCENARIO_NAME

    memo_input = build_memo_input(
        ticker="AMZN", wacc=0.0827, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={},
        analyst_overrides={"ebit_margin": 0.16},
        analyst_rationale="Guidance del propio Excel de referencia (15% año 5).",
    )
    assert memo_input.analyst_override == {
        "nombre_escenario": ANALYST_SCENARIO_NAME,
        "drivers_anulados": {"ebit_margin": 0.16},
        "justificacion": "Guidance del propio Excel de referencia (15% año 5).",
    }
    _, user_prompt = build_prompt(memo_input)
    json_start = user_prompt.index("{")
    payload = json.loads(user_prompt[json_start:])
    assert payload["supuesto_manual_del_analista"]["nombre_escenario"] == ANALYST_SCENARIO_NAME


def test_build_memo_input_omits_analyst_override_when_not_provided():
    memo_input = build_memo_input(
        ticker="TEST", wacc=0.08, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={},
    )
    assert memo_input.analyst_override is None
    _, user_prompt = build_prompt(memo_input)
    json_start = user_prompt.index("{")
    payload = json.loads(user_prompt[json_start:])
    assert payload["supuesto_manual_del_analista"] is None


# --- build_prompt -------------------------------------------------------------

def _sample_memo_input() -> MemoInput:
    return MemoInput(
        ticker="AMZN", company_name="Amazon.com Inc",
        wacc=0.0827, terminal_growth_rate=0.025,
        scenarios=[ScenarioSummary(BASE_SCENARIO_NAME, 84.82),
                   ScenarioSummary("Alcista (continúa la tendencia reciente)", 107.09)],
        key_assumptions={"ebit_margin_start": 0.139, "ebit_margin_end": 0.105},
        market_price=258.51, analyst_target_price=328.17,
        deviation_vs_market=-0.672, deviation_vs_consensus=-0.742,
        warnings_raised=["WACC-g = 2.00% está por debajo del margen prudente..."],
    )


def test_build_prompt_system_prompt_forbids_inventing_numbers():
    system_prompt, _ = build_prompt(_sample_memo_input())
    assert "ÚNICAMENTE" in system_prompt or "únicamente" in system_prompt.lower()
    assert "Executive Summary" in system_prompt


def test_build_prompt_user_prompt_contains_all_figures_as_valid_json():
    _, user_prompt = build_prompt(_sample_memo_input())
    json_start = user_prompt.index("{")
    payload = json.loads(user_prompt[json_start:])
    assert payload["ticker"] == "AMZN"
    assert payload["precio_de_mercado"] == 258.51
    assert payload["precio_objetivo_consenso_analistas"] == 328.17
    assert len(payload["escenarios_dcf"]) == 2
    assert payload["avisos_tecnicos_del_modelo"][0].startswith("WACC-g")


def test_build_prompt_omits_nothing_silently_missing_fields_stay_null():
    memo_input = build_memo_input(
        ticker="TEST", wacc=0.08, terminal_growth_rate=0.025,
        scenario_results=_sample_scenario_results(), key_assumptions={},
    )
    _, user_prompt = build_prompt(memo_input)
    json_start = user_prompt.index("{")
    payload = json.loads(user_prompt[json_start:])
    assert payload["precio_de_mercado"] is None
    assert payload["precio_objetivo_consenso_analistas"] is None


# --- generate_memo (cliente falso, sin red) ----------------------------------

def test_generate_memo_calls_client_with_system_and_user_prompt():
    client = FakeClient()
    text = generate_memo(_sample_memo_input(), client=client)
    assert text == "Memo redactado de prueba."
    assert client.messages.last_call["system"] is not None
    assert "AMZN" in client.messages.last_call["messages"][0]["content"]


def test_generate_memo_passes_through_model_and_max_tokens():
    client = FakeClient()
    generate_memo(_sample_memo_input(), client=client, model="claude-test-model", max_tokens=500)
    assert client.messages.last_call["model"] == "claude-test-model"
    assert client.messages.last_call["max_tokens"] == 500
