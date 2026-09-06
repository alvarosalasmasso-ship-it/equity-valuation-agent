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
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from engine.comps import build_comps_table, peer_average_multiple
from engine.projections import default_assumptions_from_history, project_financials, stub_fraction_from_history
from engine.reverse_dcf import ImpliedExpectations, compute_implied_expectations
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
    peer_ev_ebitda_multiple: float
    implied_expectations: list[ImpliedExpectations]


def value_ticker(target: str, universe_hist: dict[str, pd.DataFrame],
                  universe_snap: dict[str, dict], risk_free_rate: float,
                  market_risk_premium: float, n_years: int = 5,
                  terminal_growth_rate: float = 0.025, lookback_years: int = 3,
                  gordon_weight: float = 0.8, valuation_date: Optional[date] = None) -> ValuationCheck:
    """Corre el pipeline completo para un ticker del universo y lo
    compara contra su propio precio de mercado y consenso de analistas.

    El múltiplo EV/EBITDA de salida del valor terminal es la MEDIANA de
    los comparables del universo (excluyendo `target`), nunca el propio
    múltiplo de la empresa objetivo — igual que el WACC, para no usar
    una referencia que puede estar ya sobre/infra valorada.

    stub_fraction (auditoría sesión 15, hallazgo C1) se calcula a partir
    del cierre de ejercicio fiscal real del último año de `hist` y de
    `valuation_date` (por defecto, hoy) — no se asume ya "1 de enero del
    primer año proyectado" de forma implícita.

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

    # Múltiplo de salida = mediana de los COMPARABLES (nunca el propio
    # múltiplo de la empresa objetivo, que puede estar ya sobre/infra
    # valorado y no aportaría ninguna referencia externa real) — ver
    # docs/METHODOLOGY.md sección 16.
    comps_table = build_comps_table(list(universe_snap.values()))
    peer_ev_ebitda = peer_average_multiple(comps_table, "ev_to_ebitda", exclude_symbol=target, method="median")

    stub_fraction = stub_fraction_from_history(hist, valuation_date=valuation_date)

    inputs = DCFInputs(
        ebit=projection.ebit, tax_rate=projection.tax_rate, d_and_a=projection.d_and_a,
        capex=projection.capex, change_in_nwc=projection.change_in_nwc,
        wacc=wacc_result.wacc, terminal_growth_rate=terminal_growth_rate, stub_fraction=stub_fraction,
        cash=snap["cash"], total_debt=snap["total_debt"], diluted_shares=snap["shares_outstanding"],
        terminal_ev_ebitda_multiple=peer_ev_ebitda, gordon_weight=gordon_weight,
    )
    result = run_dcf(inputs)

    market_price = snap.get("price")
    consensus = snap.get("analyst_target_price")
    dev_market = (result.implied_share_price / market_price - 1) if market_price else None
    dev_consensus = (result.implied_share_price / consensus - 1) if consensus else None

    # Reverse DCF (ver engine/reverse_dcf.py, docs/METHODOLOGY.md sección
    # 20): qué crecimiento de ingresos / tasa de crecimiento terminal
    # justificarían el precio de mercado o el consenso, dado el resto de
    # supuestos ya fijados arriba. `inputs` ya tiene todo lo que necesita
    # como `base_inputs`; no se reconstruye nada.
    reverse_dcf_kwargs = dict(
        wacc=wacc_result.wacc, terminal_growth_rate=terminal_growth_rate, stub_fraction=stub_fraction,
        cash=snap["cash"], total_debt=snap["total_debt"], diluted_shares=snap["shares_outstanding"],
        terminal_ev_ebitda_multiple=peer_ev_ebitda, gordon_weight=gordon_weight,
    )
    implied_expectations = compute_implied_expectations(
        hist["revenue"].iloc[-1], assumptions, inputs, reverse_dcf_kwargs,
        targets=[("Mercado", market_price), ("Consenso analistas", consensus)],
    )

    return ValuationCheck(
        ticker=target, wacc=wacc_result.wacc, implied_price=result.implied_share_price,
        market_price=market_price, analyst_target_price=consensus,
        deviation_vs_market=dev_market, deviation_vs_consensus=dev_consensus,
        peer_ev_ebitda_multiple=peer_ev_ebitda, implied_expectations=implied_expectations,
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


DEFAULT_BOOTSTRAP_ITERATIONS = 10_000

# 80% (percentiles 10/90) para que la lectura sea consistente con las
# bandas P10/P50/P90 ya usadas en engine.monte_carlo, no un 95%
# introducido sin motivo aparte de "es lo habitual".
DEFAULT_BOOTSTRAP_CONFIDENCE = 0.80


def bootstrap_deviation_ci(results: pd.DataFrame, column: str = "deviation_vs_market",
                            confidence: float = DEFAULT_BOOTSTRAP_CONFIDENCE,
                            n_bootstrap: int = DEFAULT_BOOTSTRAP_ITERATIONS,
                            seed: Optional[int] = None) -> dict:
    """Intervalo de confianza bootstrap (percentil) sobre la desviación
    absoluta media agregada (sesión 17, item E del lote de rigor
    matemático propuesto en la sección 13 de `estado.md`).

    Motivo: `summarize_deviation()` reporta un único número puntual
    (p.ej. "34.21%" sobre el universo piloto, n=8) sin comunicar cuánta
    incertidumbre de muestreo hay detrás de una muestra tan pequeña --
    dos universos de 8 tickers distintos podrían dar cifras bastante
    distintas por puro azar de qué compañías caen dentro. Remuestrea
    con reemplazo `n_bootstrap` veces sobre las desviaciones absolutas
    individuales y reporta el rango percentil de la media de cada
    remuestra -- el método estándar (Efron, 1979) para estimar
    incertidumbre sin asumir una distribución paramétrica conocida.

    RNG inyectable (`numpy.random.default_rng`) para tests
    deterministas, mismo patrón que `engine.monte_carlo.run_monte_carlo`.
    """
    values = results[column].abs().to_numpy(dtype=float)
    n = len(values)
    if n < 2:
        raise ValueError(
            "Se necesitan al menos 2 tickers para un intervalo de confianza bootstrap "
            f"(recibidos: {n})"
        )
    rng = np.random.default_rng(seed)
    resamples = rng.choice(values, size=(n_bootstrap, n), replace=True)
    resample_means = resamples.mean(axis=1)
    alpha = 1.0 - confidence
    lower = float(np.percentile(resample_means, 100 * alpha / 2))
    upper = float(np.percentile(resample_means, 100 * (1 - alpha / 2)))
    return {
        "point_estimate": float(values.mean()),
        "confidence": confidence,
        "n": n,
        "n_bootstrap": n_bootstrap,
        "ci_lower": lower,
        "ci_upper": upper,
    }
