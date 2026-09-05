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

from engine.scenarios import run_scenarios
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


CONSERVATIVE_SCENARIO_NAME = "Conservador (reversión a la media)"


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
                      warnings_raised: Optional[list[str]] = None) -> MemoInput:
    """Ensambla el MemoInput. La desviación vs. mercado/consenso se mide
    sobre el escenario conservador (el valor por defecto del motor)."""
    base_result = scenario_results.get(CONSERVATIVE_SCENARIO_NAME)
    base_price = base_result.implied_share_price if base_result else None

    dev_market = (base_price / market_price - 1) if (base_price and market_price) else None
    dev_consensus = (base_price / analyst_target_price - 1) if (base_price and analyst_target_price) else None

    scenarios = [
        ScenarioSummary(name=name, implied_price=result.implied_share_price)
        for name, result in scenario_results.items()
    ]

    return MemoInput(
        ticker=ticker, company_name=company_name, wacc=wacc,
        terminal_growth_rate=terminal_growth_rate, scenarios=scenarios,
        key_assumptions=key_assumptions, market_price=market_price,
        analyst_target_price=analyst_target_price, deviation_vs_market=dev_market,
        deviation_vs_consensus=dev_consensus, warnings_raised=warnings_raised or [],
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
