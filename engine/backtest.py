"""Backtesting walk-forward (sesión 17, Lote C, ítem D): ¿el modelo
tiene señal predictiva real, o solo se ajusta transversalmente al
precio de hoy? Congela los inputs "como si" se valorara en una fecha
pasada (usando solo datos que existían entonces, con un margen de
retraso de reporting conservador) y deja comparar la dirección de la
desviación contra el retorno real posterior -- la pregunta que la
validación transversal (Fase 7, docs/METHODOLOGY.md secciones 7-16)
nunca puede responder por sí sola: esa validación mide "¿coincide con
el precio de HOY?", nunca "¿tenía razón sobre hacia dónde iba el
precio?".

Viabilidad confirmada con datos reales antes de construir (revisando
la investigación de SEC EDGAR, sección 26 -- no es el mismo hallazgo
pesimista): no hace falta una fuente de datos "point-in-time"
especializada. Truncar el histórico ya disponible a fechas anteriores
al punto de backtest, con un margen de retraso de reporting
conservador, evita el componente más grave de look-ahead bias (usar
estados financieros que aún no existían). Riesgo residual menor y
aceptado: restatements retroactivos de años ya cerrados no se
modelan (no hay forma gratuita de saber qué decía el 10-K exacto de
la época sin volver a parsear el filing original).

Este módulo es cálculo puro (`known_history_as_of`, `BacktestResult`) --
la obtención de datos reales (precio histórico en la fecha de backtest,
risk-free rate histórico) vive en `scripts/run_backtest.py`, mismo
patrón que `engine.validation`/`scripts.validate_universe`.

Simplificaciones deliberadas, documentadas (no descuidos):
- Beta y coste de deuda: los de HOY, no los de la fecha de backtest --
  no hay fuente gratuita de beta histórico point-in-time.
- Acciones diluidas: recuento de HOY, mismo motivo.
- Risk-free rate y precio de mercado en la fecha de backtest: SÍ son
  reales de esa fecha (^TNX y precio de cierre histórico vía yfinance,
  ambos gratis) -- no hay motivo para aproximarlos si el dato real
  existe sin coste.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd

DEFAULT_REPORTING_LAG_DAYS = 90


def known_history_as_of(history: pd.DataFrame, as_of_date: date,
                         reporting_lag_days: int = DEFAULT_REPORTING_LAG_DAYS) -> pd.DataFrame:
    """Filtra `history` a solo los años fiscales cuyo 10-K ya habría
    sido publicado en `as_of_date` -- estimado como cierre de ejercicio
    + `reporting_lag_days` (90 días por defecto, un margen conservador
    frente al plazo real de la SEC para un "large accelerated filer",
    60 días -- prefiere excluir un año de más antes que arriesgar
    look-ahead bias). Filas sin `fiscal_year_end_month`/`_day` (no
    debería ocurrir viniendo de `historical_financials()` real, pero
    posible en históricos sintéticos) se excluyen, nunca se asumen."""
    clean = history.dropna(subset=["fiscal_year_end_month", "fiscal_year_end_day"])
    if clean.empty:
        return clean

    def _filed_by(row) -> bool:
        fy_end = date(int(row["fiscal_year"]), int(row["fiscal_year_end_month"]), int(row["fiscal_year_end_day"]))
        return fy_end + timedelta(days=reporting_lag_days) <= as_of_date

    mask = clean.apply(_filed_by, axis=1)
    return clean[mask].reset_index(drop=True)


@dataclass
class BacktestResult:
    """`deviation_at_backtest`: (precio implícito - precio de mercado) /
    precio de mercado, EN LA FECHA DE BACKTEST -- negativo significa que
    el modelo ya marcaba la empresa como infravalorada entonces.
    `actual_return`: retorno real del precio de mercado desde la fecha
    de backtest hasta hoy. Comparar el SIGNO/MAGNITUD de ambos across
    varios tickers es la pregunta real del backtest: ¿una desviación
    negativa grande predijo un retorno posterior más bajo (el mercado
    tenía razón en pagar la prima) o más alto (el modelo conservador
    tenía razón, y el mercado corrigió después)? Este resultado no
    contesta la pregunta por sí solo -- solo la deja medible."""
    ticker: str
    backtest_date: date
    price_at_backtest: float
    price_today: float
    implied_price_at_backtest: float
    wacc_at_backtest: float
    years_of_history_used: int

    @property
    def deviation_at_backtest(self) -> float:
        return self.implied_price_at_backtest / self.price_at_backtest - 1

    @property
    def actual_return(self) -> float:
        return self.price_today / self.price_at_backtest - 1


def build_backtest_result(ticker: str, backtest_date: date, price_at_backtest: float,
                           price_today: float, implied_price_at_backtest: float,
                           wacc_at_backtest: float, years_of_history_used: int) -> BacktestResult:
    """Ensambla el resultado a partir de números ya calculados --
    separado de `run_dcf()` porque el DCF "como si fuera entonces" se
    construye con el pipeline normal (`default_assumptions_from_history`
    + `project_financials` + `DCFInputs`/`run_dcf`) sobre el histórico
    ya truncado por `known_history_as_of()`; esta función solo empaqueta
    el resultado final, no repite ningún cálculo financiero."""
    if price_at_backtest <= 0:
        raise ValueError(f"price_at_backtest debe ser positivo (recibido {price_at_backtest})")
    if price_today <= 0:
        raise ValueError(f"price_today debe ser positivo (recibido {price_today})")
    return BacktestResult(
        ticker=ticker, backtest_date=backtest_date, price_at_backtest=price_at_backtest,
        price_today=price_today, implied_price_at_backtest=implied_price_at_backtest,
        wacc_at_backtest=wacc_at_backtest, years_of_history_used=years_of_history_used,
    )
