"""Capa generativa: redacta el Investment Memo a partir de números YA
calculados por engine/. Principio de arquitectura del proyecto (ver
docs/METHODOLOGY.md): el LLM nunca calcula nada, solo redacta texto
natural a partir de un paquete de datos cerrado y determinista
(MemoInput). Si un número no está en ese paquete, el prompt prohíbe
explícitamente que el LLM lo mencione o lo invente.

Diseño con inyección de dependencia (`client` en generate_memo) para que
la construcción del paquete de datos y del prompt se puedan testear sin
red ni API key — solo generate_memo() necesita una key real de Anthropic,
y solo si se llama sin pasar un client de prueba.
"""

import json
import warnings as warnings_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from engine.ratios import RatioSnapshot
from engine.reverse_dcf import ImpliedExpectations
from engine.scenarios import BASE_SCENARIO_NAME, run_scenarios
from engine.sensitivity import DriverSensitivity
from engine.valuation import DCFResult

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_MODEL = "claude-sonnet-5"


def _load_system_prompt() -> str:
    return (PROMPTS_DIR / "investment_memo_system.md").read_text(encoding="utf-8")


@dataclass
class ScenarioSummary:
    name: str
    implied_price: float


@dataclass
class MemoInput:
    """Paquete cerrado de datos numéricos: todo lo que el LLM puede ver.
    Si un dato no está aquí, el prompt le prohíbe mencionarlo."""
    ticker: str
    wacc: float
    terminal_growth_rate: float
    scenarios: list[ScenarioSummary]
    key_assumptions: dict
    company_name: Optional[str] = None
    market_price: Optional[float] = None
    analyst_target_price: Optional[float] = None
    deviation_vs_market: Optional[float] = None
    deviation_vs_consensus: Optional[float] = None
    warnings_raised: list[str] = field(default_factory=list)
    ratios: Optional[dict] = None
    implied_expectations: Optional[list[dict]] = None
    sensitivities: Optional[list[dict]] = None


def run_scenarios_capturing_warnings(history, **kwargs) -> tuple[dict[str, DCFResult], list[str]]:
    """Envuelve engine.scenarios.run_scenarios capturando cualquier
    warnings.warn emitido durante el cálculo (p.ej. spread WACC-g
    estrecho, ver engine.valuation.MIN_PRUDENT_WACC_GROWTH_SPREAD) como
    texto, para poder incluirlo en el memo como una señal real de riesgo
    del propio modelo."""
    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        results = run_scenarios(history, **kwargs)
    return results, [str(w.message) for w in caught]


def build_memo_input(ticker: str, wacc: float, terminal_growth_rate: float,
                      scenario_results: dict[str, DCFResult], key_assumptions: dict,
                      company_name: Optional[str] = None, market_price: Optional[float] = None,
                      analyst_target_price: Optional[float] = None,
                      warnings_raised: Optional[list[str]] = None,
                      ratios: Optional[RatioSnapshot] = None,
                      implied_expectations: Optional[list[ImpliedExpectations]] = None,
                      sensitivities: Optional[list[DriverSensitivity]] = None) -> MemoInput:
    """Ensambla el MemoInput. La desviación vs. mercado/consenso se mide
    sobre el escenario conservador (el valor por defecto del motor).

    ratios: snapshot de engine.ratios.latest_ratio_snapshot() (ROE,
    ROIC vs. WACC, Debt/EBITDA, cobertura de intereses, current ratio) —
    opcional, da al memo contexto de rentabilidad/apalancamiento además
    del precio objetivo. Si se omite, el prompt no lo menciona.

    implied_expectations: resultado de
    engine.reverse_dcf.compute_implied_expectations() -- qué crecimiento
    de ingresos y qué tasa de crecimiento terminal tendrían que cumplirse
    para justificar el precio de mercado/consenso, comparado contra lo
    que asume el propio escenario base. Es lo que le permite al
    memo explicar el mecanismo detrás de una desviación grande en vez de
    solo reportar el porcentaje (ver la regla 6 del prompt de sistema).

    sensitivities: resultado de engine.sensitivity.driver_sensitivities()
    (sesión 17) -- cuánto se mueve el precio implícito por cada supuesto
    (WACC, g terminal, margen, D&A, CapEx, crecimiento), de mayor a menor
    impacto. Le da al memo la palanca que domina la valoración de esta
    empresa en concreto, en vez de una lista genérica de supuestos."""
    base_result = scenario_results.get(BASE_SCENARIO_NAME)
    base_price = base_result.implied_share_price if base_result else None

    dev_market = (base_price / market_price - 1) if (base_price and market_price) else None
    dev_consensus = (base_price / analyst_target_price - 1) if (base_price and analyst_target_price) else None

    scenarios = [
        ScenarioSummary(name=name, implied_price=result.implied_share_price)
        for name, result in scenario_results.items()
    ]

    ratios_dict = None
    if ratios is not None:
        # interest_coverage puede ser float("inf") (empresa sin deuda) --
        # json.dumps() lo serializaría como el token "Infinity", inválido
        # en JSON estricto (RFC 8259). Se representa como texto explícito.
        interest_coverage = ("sin deuda (cobertura infinita)"
                              if ratios.interest_coverage == float("inf")
                              else ratios.interest_coverage)
        ratios_dict = {
            "fiscal_year": ratios.fiscal_year,
            "roe": ratios.roe,
            "roic": ratios.roic,
            "crea_valor_roic_mayor_que_wacc": bool(ratios.creates_value),
            "debt_to_ebitda": ratios.debt_to_ebitda,
            "net_debt_to_ebitda": ratios.net_debt_to_ebitda,
            "interest_coverage": interest_coverage,
            "current_ratio": ratios.current_ratio,
        }

    implied_expectations_list = None
    if implied_expectations:
        implied_expectations_list = [
            {
                "precio_objetivo": exp.target_label,
                "precio": exp.target_price,
                "crecimiento_ingresos_asumido": exp.assumed_revenue_growth,
                "crecimiento_ingresos_implicito": exp.implied_revenue_growth,
                "gap_crecimiento_ingresos": exp.revenue_growth_gap,
                "tasa_crecimiento_terminal_asumida": exp.assumed_terminal_growth,
                "tasa_crecimiento_terminal_implicita": exp.implied_terminal_growth,
                "crecimiento_terminal_implicito_en_zona_fragil": exp.terminal_growth_fragile,
            }
            for exp in implied_expectations
        ]

    sensitivities_list = None
    if sensitivities:
        sensitivities_list = [
            {
                "supuesto": s.label,
                "cambio_en_precio_por_1pp": s.price_change_pct,
            }
            for s in sensitivities
        ]

    return MemoInput(
        ticker=ticker, company_name=company_name, wacc=wacc,
        terminal_growth_rate=terminal_growth_rate, scenarios=scenarios,
        key_assumptions=key_assumptions, market_price=market_price,
        analyst_target_price=analyst_target_price, deviation_vs_market=dev_market,
        deviation_vs_consensus=dev_consensus, warnings_raised=warnings_raised or [],
        ratios=ratios_dict, implied_expectations=implied_expectations_list,
        sensitivities=sensitivities_list,
    )


def build_prompt(memo_input: MemoInput) -> tuple[str, str]:
    """Devuelve (system_prompt, user_prompt). El user_prompt serializa
    MemoInput a JSON tal cual — nada de texto libre que el LLM pueda
    confundir con una instrucción."""
    payload = {
        "ticker": memo_input.ticker,
        "empresa": memo_input.company_name,
        "precio_de_mercado": memo_input.market_price,
        "precio_objetivo_consenso_analistas": memo_input.analyst_target_price,
        "wacc": memo_input.wacc,
        "tasa_de_crecimiento_terminal": memo_input.terminal_growth_rate,
        "escenarios_dcf": [
            {"nombre": s.name, "precio_implicito": s.implied_price}
            for s in memo_input.scenarios
        ],
        "desviacion_vs_mercado_escenario_conservador": memo_input.deviation_vs_market,
        "desviacion_vs_consenso_escenario_conservador": memo_input.deviation_vs_consensus,
        "avisos_tecnicos_del_modelo": memo_input.warnings_raised,
        "supuestos_clave_de_proyeccion": memo_input.key_assumptions,
        "ratios_financieros": memo_input.ratios,
        "expectativas_implicitas_del_mercado": memo_input.implied_expectations,
        "sensibilidad_del_precio_por_supuesto": memo_input.sensitivities,
    }
    user_prompt = (
        "Redacta el Investment Memo para el siguiente paquete de datos. "
        "Recuerda: solo puedes usar las cifras que aparecen aquí.\n\n"
        + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    )
    return _load_system_prompt(), user_prompt


def generate_memo(memo_input: MemoInput, client=None, model: str = DEFAULT_MODEL,
                   max_tokens: int = 1500) -> str:
    """Llama a la API de Anthropic para redactar el memo. `client` es
    inyectable (cualquier objeto con `.messages.create(...)` -> objeto
    con `.content[0].text`) para poder testear sin red ni API key; si se
    omite, se crea un anthropic.Anthropic() real (lee ANTHROPIC_API_KEY
    del entorno)."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    system_prompt, user_prompt = build_prompt(memo_input)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text
