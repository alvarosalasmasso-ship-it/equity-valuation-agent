"""Ratios financieros (Fase 3 del blueprint, sección 2 del blueprint:
rentabilidad, apalancamiento, liquidez, creación de valor).

Cada función corresponde a una fila de la tabla de "Ratios y comparables"
del blueprint. Reciben números sueltos (no DataFrames) para que sean
triviales de testear y de reutilizar fuera de un pipeline de pandas.
"""

from dataclasses import dataclass


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
    """ROIC = NOPAT / Capital invertido"""
    nopat = ebit * (1 - tax_rate)
    return nopat / invested_capital_


def creates_value(roic_: float, wacc: float) -> bool:
    """Crea valor si ROIC > WACC."""
    return roic_ > wacc


# ---------------------------------------------------------------------------
# Apalancamiento
# ---------------------------------------------------------------------------

def debt_to_ebitda(total_debt: float, ebitda: float) -> float:
    return total_debt / ebitda


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
