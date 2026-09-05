"""Fase 7: valida el motor de valoración corriendo el pipeline completo
(WACC vía comparables -> proyección -> DCF) sobre un universo de tickers
y comparando el precio implícito frente al precio de mercado y al
consenso de analistas.

Diseño explícito para no depender de red en los tests: recibe los
históricos y snapshots YA DESCARGADOS (dict símbolo -> DataFrame / dict),
no los descarga internamente. Quien orquesta la descarga real
(engine.data_provider) es responsabilidad de quien llama a este módulo.

Cada ticker del universo se valora usando el resto del universo como su
propio set de comparables para el WACC — evita mezclar datos de mercado
de fechas distintas (ver docs/METHODOLOGY.md sección 5).
"""

from dataclasses import asdict, dataclass
from typing import Optional

import pandas as pd

from engine.projections import default_assumptions_from_history, project_financials
from engine.valuation import DCFInputs, run_dcf
from engine.wacc_builder import PeerInput, build_wacc


def build_peer_set(target: str, universe_hist: dict[str, pd.DataFrame],
                    universe_snap: dict[str, dict]) -> list[PeerInput]:
    """Todos los tickers del universo salvo `target`, como comparables."""
    peers = []
    for symbol, hist in universe_hist.items():
        if symbol == target:
            continue
        snap = universe_snap[symbol]
        tax_rate = hist["tax_rate"].dropna().iloc[-1]
        net_debt = (snap.get("total_debt") or 0) - (snap.get("cash") or 0)
        peers.append(PeerInput(
            symbol=symbol, levered_beta=snap["beta"], tax_rate=float(tax_rate),
            net_debt=net_debt, market_cap=snap["market_cap"],
        ))
    return peers


@dataclass
class ValuationCheck:
    ticker: str
    wacc: float
    implied_price: float
    market_price: Optional[float]
    analyst_target_price: Optional[float]
    deviation_vs_market: Optional[float]
    deviation_vs_consensus: Optional[float]


def value_ticker(target: str, universe_hist: dict[str, pd.DataFrame],
                  universe_snap: dict[str, dict], risk_free_rate: float,
                  market_risk_premium: float, n_years: int = 5,
                  terminal_growth_rate: float = 0.025, lookback_years: int = 3,
                  gordon_weight: float = 0.8) -> ValuationCheck:
    """Corre el pipeline completo para un ticker del universo y lo
    compara contra su propio precio de mercado y consenso de analistas.

    Requiere al menos 2 tickers en el universo (target + >=1 peer).
    """
    if target not in universe_hist or target not in universe_snap:
        raise ValueError(f"'{target}' no está en el universo proporcionado")
    if len(universe_hist) < 2:
        raise ValueError("Se necesitan al menos 2 tickers en el universo (target + comparables)")

    hist = universe_hist[target]
    snap = universe_snap[target]
    peers = build_peer_set(target, universe_hist, universe_snap)

    target_tax_rate = float(hist["tax_rate"].dropna().iloc[-1])
    target_net_debt = (snap.get("total_debt") or 0) - (snap.get("cash") or 0)
    target_interest_expense = float(hist["interest_expense"].dropna().iloc[-1])

    wacc_result = build_wacc(
        peers=peers, target_tax_rate=target_tax_rate, target_net_debt=target_net_debt,
        target_market_cap=snap["market_cap"], risk_free_rate=risk_free_rate,
        market_risk_premium=market_risk_premium,
        target_interest_expense=target_interest_expense, target_total_debt=snap["total_debt"],
    )

    assumptions = default_assumptions_from_history(
        hist, n_years=n_years, lookback_years=lookback_years,
    )
    projection = project_financials(hist["revenue"].iloc[-1], assumptions)

    inputs = DCFInputs(
        ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
        capex=projection.capex, change_in_nwc=projection.change_in_nwc,
        wacc=wacc_result.wacc, terminal_growth_rate=terminal_growth_rate,
        cash=snap["cash"], total_debt=snap["total_debt"], diluted_shares=snap["shares_outstanding"],
        terminal_ev_ebitda_multiple=snap.get("ev_to_ebitda"), gordon_weight=gordon_weight,
    )
    result = run_dcf(inputs)

    market_price = snap.get("price")
    consensus = snap.get("analyst_target_price")
    dev_market = (result.implied_share_price / market_price - 1) if market_price else None
    dev_consensus = (result.implied_share_price / consensus - 1) if consensus else None

    return ValuationCheck(
        ticker=target, wacc=wacc_result.wacc, implied_price=result.implied_share_price,
        market_price=market_price, analyst_target_price=consensus,
        deviation_vs_market=dev_market, deviation_vs_consensus=dev_consensus,
    )


def validate_universe(universe_hist: dict[str, pd.DataFrame], universe_snap: dict[str, dict],
                       risk_free_rate: float, market_risk_premium: float,
                       **kwargs) -> pd.DataFrame:
    """Corre value_ticker para cada ticker del universo. kwargs se pasan
    tal cual a value_ticker (n_years, terminal_growth_rate, lookback_years,
    gordon_weight)."""
    checks = [
        value_ticker(t, universe_hist, universe_snap, risk_free_rate, market_risk_premium, **kwargs)
        for t in universe_hist
    ]
    return pd.DataFrame([asdict(c) for c in checks]).set_index("ticker")


def summarize_deviation(results: pd.DataFrame) -> dict:
    """Métricas agregadas de la Fase 7: desviación media/mediana absoluta
    del precio implícito frente a mercado y consenso, sobre el universo."""
    return {
        "n": len(results),
        "mean_abs_deviation_vs_market": results["deviation_vs_market"].abs().mean(),
        "median_abs_deviation_vs_market": results["deviation_vs_market"].abs().median(),
        "mean_abs_deviation_vs_consensus": results["deviation_vs_consensus"].abs().mean(),
        "median_abs_deviation_vs_consensus": results["deviation_vs_consensus"].abs().median(),
    }
