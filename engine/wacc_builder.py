"""Orquesta la construcción completa del WACC a partir de un set de
comparables, replicando la hoja WACC del Excel de referencia de punta a
punta: beta desapalancada por comparable -> beta media de industria ->
beta reapalancada para el ticker objetivo -> CAPM -> WACC.

No reimplementa la matemática: reutiliza tal cual las funciones de
engine.valuation ya validadas contra el Excel (unlever_beta,
relever_beta, cost_of_equity, cost_of_debt, wacc). Este módulo es pura
orquestación de datos, para no tener dos fuentes de verdad del mismo
cálculo.
"""

from dataclasses import dataclass
from typing import Sequence

from engine.valuation import (
    cost_of_debt,
    cost_of_equity,
    relever_beta,
    unlever_beta,
    wacc,
)


@dataclass
class PeerInput:
    """Datos de un comparable necesarios para desapalancar su beta.
    net_debt = deuda total - caja (puede ser negativo si hay caja neta)."""
    symbol: str
    levered_beta: float
    tax_rate: float
    net_debt: float
    market_cap: float


@dataclass
class WaccBuildResult:
    peer_unlevered_betas: dict[str, float]
    industry_unlevered_beta: float
    relevered_beta: float
    cost_of_equity: float
    cost_of_debt: float
    wacc: float


def industry_unlevered_beta(peers: Sequence[PeerInput]) -> dict[str, float]:
    """Beta desapalancada de cada comparable. Devuelve un dict
    símbolo -> beta desapalancada (no solo la media) para que el
    resultado sea auditable línea a línea, como en WACC!I28:I30."""
    if not peers:
        raise ValueError("Se necesita al menos un comparable")
    return {
        p.symbol: unlever_beta(p.levered_beta, p.tax_rate, p.net_debt, p.market_cap)
        for p in peers
    }


def build_wacc(peers: Sequence[PeerInput], target_tax_rate: float,
                target_net_debt: float, target_market_cap: float,
                risk_free_rate: float, market_risk_premium: float,
                target_interest_expense: float, target_total_debt: float) -> WaccBuildResult:
    """Réplica completa de la hoja WACC del Excel para un ticker objetivo
    arbitrario, dado un set de comparables de su sector.

    target_net_debt: deuda total - caja del propio ticker (usado para
    reapalancar la beta de industria, WACC!E33).
    target_total_debt / target_interest_expense: usados para el coste de
    deuda y las ponderaciones E/D del WACC (WACC!F15:F22) — deuda BRUTA,
    no neta.
    """
    peer_betas = industry_unlevered_beta(peers)
    avg_unlevered_beta = sum(peer_betas.values()) / len(peer_betas)

    relevered = relever_beta(avg_unlevered_beta, target_tax_rate, target_net_debt, target_market_cap)
    re = cost_of_equity(risk_free_rate, relevered, market_risk_premium)
    rd = cost_of_debt(target_interest_expense, target_total_debt)
    w = wacc(target_market_cap, target_total_debt, re, rd, target_tax_rate)

    return WaccBuildResult(
        peer_unlevered_betas=peer_betas,
        industry_unlevered_beta=avg_unlevered_beta,
        relevered_beta=relevered,
        cost_of_equity=re,
        cost_of_debt=rd,
        wacc=w,
    )
