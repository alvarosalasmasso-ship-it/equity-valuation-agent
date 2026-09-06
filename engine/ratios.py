"""Ratios financieros (Fase 3 del blueprint, sección 2 del blueprint:
rentabilidad, apalancamiento, liquidez, creación de valor).

Cada función corresponde a una fila de la tabla de "Ratios y comparables"
del blueprint. Reciben números sueltos (no DataFrames) para que sean
triviales de testear y de reutilizar fuera de un pipeline de pandas —
excepto `latest_ratio_snapshot()`, que sí opera sobre el DataFrame de
`engine.data_provider.historical_financials` por conveniencia de los
consumidores (`ai/memo_generator.py`, `app/streamlit_app.py`).
"""

import warnings
from dataclasses import dataclass

import pandas as pd


# ---------------------------------------------------------------------------
# Rentabilidad
# ---------------------------------------------------------------------------

@dataclass
class DupontROE:
    net_margin: float
    asset_turnover: float
    equity_multiplier: float
    roe: float


def roe_dupont(net_income: float, revenue: float, total_assets: float,
                total_equity: float) -> DupontROE:
    """ROE = (Beneficio neto/Ventas) x (Ventas/Activos) x (Activos/Equity)"""
    net_margin = net_income / revenue
    asset_turnover = revenue / total_assets
    equity_multiplier = total_assets / total_equity
    return DupontROE(
        net_margin=net_margin,
        asset_turnover=asset_turnover,
        equity_multiplier=equity_multiplier,
        roe=net_margin * asset_turnover * equity_multiplier,
    )


# ---------------------------------------------------------------------------
# Creación de valor
# ---------------------------------------------------------------------------

def invested_capital(total_debt: float, total_equity: float, cash: float) -> float:
    return total_debt + total_equity - cash


def roic(ebit: float, tax_rate: float, invested_capital_: float) -> float:
    """ROIC = NOPAT / Capital invertido.

    Emite un warning (no bloquea el cálculo) si el capital invertido es
    <= 0 -- ocurre en compañías con equity contable negativo por
    recompras de acciones muy agresivas (no observado en los 8 tickers
    piloto, pero es un caso real conocido, p.ej. algunas consumer
    staples muy apalancadas en recompras). Con capital invertido <= 0 el
    ROIC resultante no es interpretable de la forma habitual (puede
    salir negativo o desproporcionado sin reflejar mal desempeño real)."""
    if invested_capital_ <= 0:
        warnings.warn(
            f"Capital invertido <= 0 ({invested_capital_:,.0f}) -- el ROIC resultante "
            "no es comparable al de una empresa con capital invertido positivo "
            "(equity contable negativo, típico de recompras de acciones agresivas). "
            "Trátalo con cautela.",
            stacklevel=2,
        )
    nopat = ebit * (1 - tax_rate)
    return nopat / invested_capital_


def creates_value(roic_: float, wacc: float) -> bool:
    """Crea valor si ROIC > WACC."""
    return roic_ > wacc


# ---------------------------------------------------------------------------
# Apalancamiento
# ---------------------------------------------------------------------------

def debt_to_ebitda(total_debt: float, ebitda: float) -> float:
    """Deuda BRUTA / EBITDA (M7, `docs/AUDIT.md`: el nombre y la
    etiqueta antes no aclaraban que es deuda bruta, no neta -- Net
    Debt/EBITDA es al menos igual de común en la práctica bancaria real,
    ver `net_debt_to_ebitda()`).

    Auditoría (sesión 17): con EBITDA<=0 (un año de break-even o
    pérdida operativa antes de D&A) esta función hacía `crashear` con
    ZeroDivisionError, o devolvía un ratio negativo sin sentido (un
    Debt/EBITDA "negativo" no significa "menos apalancado" -- significa
    que el ratio no es interpretable). A diferencia de cost_of_debt()
    (donde 0.0 es un valor seguro porque el peso de la deuda también es
    0), aquí no hay un valor trivial seguro: el ratio se muestra
    directamente al usuario, así que se falla explícito -- mismo
    contrato que espera latest_ratio_snapshot() (ya captura ValueError,
    no ZeroDivisionError, que NO es una subclase de ValueError en
    Python)."""
    if ebitda <= 0:
        raise ValueError(
            f"EBITDA no positivo ({ebitda:,.0f}) -- Debt/EBITDA no es un ratio interpretable "
            "de la forma habitual (un año de break-even o pérdida operativa antes de D&A)."
        )
    return total_debt / ebitda


def net_debt_to_ebitda(total_debt: float, cash: float, ebitda: float) -> float:
    """Deuda NETA (deuda total - caja) / EBITDA (M7, sesión 17). Puede
    salir negativo de forma legítima (caja neta positiva, p.ej. AAPL en
    ciertos ejercicios) -- a diferencia de un Debt/EBITDA bruto negativo
    (que señala EBITDA<=0, no interpretable), aquí un valor negativo SÍ
    es interpretable: significa que la caja supera a la deuda total.
    Mismo guard que debt_to_ebitda() para EBITDA<=0 -- ninguna versión
    del ratio es interpretable con una base nula o negativa."""
    if ebitda <= 0:
        raise ValueError(
            f"EBITDA no positivo ({ebitda:,.0f}) -- Net Debt/EBITDA no es un ratio interpretable "
            "de la forma habitual (un año de break-even o pérdida operativa antes de D&A)."
        )
    return (total_debt - cash) / ebitda


def interest_coverage(ebit: float, interest_expense: float) -> float:
    """EBIT / Gastos financieros"""
    if interest_expense == 0:
        return float("inf")
    return ebit / interest_expense


# ---------------------------------------------------------------------------
# Liquidez
# ---------------------------------------------------------------------------

def current_ratio(current_assets: float, current_liabilities: float) -> float:
    return current_assets / current_liabilities


# ---------------------------------------------------------------------------
# Snapshot completo (conveniencia para ai/memo_generator.py y app/streamlit_app.py)
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS_FOR_RATIOS = [
    "fiscal_year", "net_income", "revenue", "total_assets", "total_equity",
    "total_debt", "cash", "ebit", "tax_rate", "ebitda", "interest_expense",
    "current_assets", "current_liabilities",
]


@dataclass
class RatioSnapshot:
    fiscal_year: int
    net_margin: float
    asset_turnover: float
    equity_multiplier: float
    roe: float
    roic: float
    creates_value: bool
    debt_to_ebitda: float
    net_debt_to_ebitda: float
    interest_coverage: float
    current_ratio: float


def compute_ratio_snapshot(row, wacc: float) -> RatioSnapshot:
    """row: cualquier objeto indexable por nombre de columna (una fila de
    pandas, un dict...) con las columnas de REQUIRED_COLUMNS_FOR_RATIOS.
    wacc: para comparar contra ROIC (¿crea valor esta compañía?)."""
    dupont = roe_dupont(row["net_income"], row["revenue"], row["total_assets"], row["total_equity"])
    ic = invested_capital(row["total_debt"], row["total_equity"], row["cash"])
    r = roic(row["ebit"], row["tax_rate"], ic)
    return RatioSnapshot(
        fiscal_year=int(row["fiscal_year"]),
        net_margin=dupont.net_margin,
        asset_turnover=dupont.asset_turnover,
        equity_multiplier=dupont.equity_multiplier,
        roe=dupont.roe,
        roic=r,
        creates_value=creates_value(r, wacc),
        debt_to_ebitda=debt_to_ebitda(row["total_debt"], row["ebitda"]),
        net_debt_to_ebitda=net_debt_to_ebitda(row["total_debt"], row["cash"], row["ebitda"]),
        interest_coverage=interest_coverage(row["ebit"], row["interest_expense"]),
        current_ratio=current_ratio(row["current_assets"], row["current_liabilities"]),
    )


def latest_ratio_snapshot(history: pd.DataFrame, wacc: float) -> RatioSnapshot:
    """Selecciona el último año con todos los campos necesarios
    disponibles (dropna) y calcula el snapshot de ratios sobre él."""
    clean = history.dropna(subset=REQUIRED_COLUMNS_FOR_RATIOS)
    if clean.empty:
        raise ValueError("No hay ningún año con todos los datos necesarios para calcular los ratios")
    return compute_ratio_snapshot(clean.iloc[-1], wacc)
