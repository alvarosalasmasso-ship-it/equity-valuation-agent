"""Proveedor de datos alternativo vía yfinance.

No tiene el límite de 25 peticiones/día de Alpha Vantage
(engine.data_provider) — a cambio de bastante menos profundidad
histórica (yfinance solo expone ~4 años de estados financieros anuales
por ticker, frente a los 15-20 años de Alpha Vantage).

Normaliza a EXACTAMENTE el mismo esquema de columnas/claves que
engine.data_provider.historical_financials / market_snapshot, así que
es intercambiable con él en engine.projections, engine.wacc_builder y
engine.validation sin ningún cambio en esos módulos — mismo principio
de "una sola fuente de verdad para la metodología, con proveedores de
datos intercambiables" que ya usa wacc_builder.py con valuation.py.

Diseño con inyección de dependencia (se recibe un objeto `Ticker` ya
construido, no el símbolo) para poder testear la normalización sin red,
igual que engine.data_provider recibe un AlphaVantageClient.
"""

from typing import Optional

import pandas as pd


def get_ticker(symbol: str):
    """Construye el objeto yfinance.Ticker real. Separado del resto de
    funciones para poder inyectar un doble de prueba sin red."""
    import yfinance as yf
    return yf.Ticker(symbol)


def treasury_yield_10y(ticker) -> float:
    """Rendimiento del Treasury de EE.UU. a 10 años vía el índice ^TNX de
    Yahoo Finance (cotiza en puntos porcentuales -- un Close de 4.25
    significa 4.25%, no 425%). Mismo propósito que
    engine.data_provider.AlphaVantageClient.treasury_yield(): risk-free
    rate en vivo para el CAPM, en vez de la constante congelada que usaba
    toda la app antes de esta corrección (auditoría sesión 15, hallazgo
    I1). `ticker`: objeto con método `.history()` (yfinance.Ticker("^TNX"),
    o un doble de prueba con la misma forma) -- misma inyección de
    dependencia que el resto de este módulo, para poder testear sin red."""
    hist = ticker.history(period="5d")
    if hist.empty:
        raise ValueError("Yahoo Finance no devolvió cotización para ^TNX.")
    return float(hist["Close"].iloc[-1]) / 100.0


def _clean(value) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return float(value)


def _clean_interest_expense(interest_expense: Optional[float], total_debt: Optional[float]) -> Optional[float]:
    """Mismo filtro que engine.data_provider._clean_interest_expense:
    un interest_expense de 0 con deuda material reportada es casi con
    certeza un hueco de datos del proveedor, no un coste de deuda real
    de cero (bug real encontrado con Alpha Vantage/AAPL en sesión — sin
    este filtro, cost_of_debt() calcularía silenciosamente un 0%). Se
    trata como dato faltante para que el resto del pipeline caiga al
    último año con un valor genuino."""
    if interest_expense == 0 and (total_debt or 0) > 0:
        return None
    return interest_expense


# Mismo esquema que engine.data_provider.HISTORICAL_FINANCIALS_COLUMNS
# (por diseño, ambos proveedores son intercambiables) -- si se añade una
# columna aquí, añadir también allí.
HISTORICAL_FINANCIALS_COLUMNS = [
    "fiscal_year", "fiscal_year_end_month", "fiscal_year_end_day", "revenue", "ebit",
    "ebitda", "tax_rate", "d_and_a", "capex", "change_in_nwc", "net_income",
    "interest_expense", "total_assets", "total_equity", "total_debt", "cash",
    "current_assets", "current_liabilities",
]


def historical_financials(ticker) -> pd.DataFrame:
    """ticker: objeto con atributos .financials, .balance_sheet, .cashflow
    (yfinance.Ticker, o un doble de prueba con la misma forma)."""
    income = ticker.financials
    balance = ticker.balance_sheet
    cash_flow = ticker.cashflow

    common_dates = sorted(set(income.columns) & set(balance.columns) & set(cash_flow.columns))
    if not common_dates:
        # Sin ninguna fecha en común entre los tres estados -- ocurre de
        # verdad con un ticker inválido: yfinance devuelve DataFrames
        # vacíos y pd.DataFrame([]).sort_values(...) lanzaría
        # KeyError('fiscal_year') en vez de un DataFrame vacío predecible
        # (auditoría sesión 15, hallazgo I3 -- confirmado reproducible
        # con un símbolo inexistente real).
        return pd.DataFrame(columns=HISTORICAL_FINANCIALS_COLUMNS)

    nwc_by_date = {}
    for date in common_dates:
        bs = balance[date]
        current_assets = _clean(bs.get("Current Assets"))
        cash = _clean(bs.get("Cash And Cash Equivalents"))
        current_liabilities = _clean(bs.get("Current Liabilities"))
        current_debt = _clean(bs.get("Current Debt")) or 0.0
        if current_assets is None or current_liabilities is None:
            nwc_by_date[date] = None
        else:
            nwc_by_date[date] = (current_assets - (cash or 0.0)) - (current_liabilities - current_debt)

    rows = []
    for i, date in enumerate(common_dates):
        inc = income[date]
        cf = cash_flow[date]
        bs = balance[date]

        revenue = _clean(inc.get("Total Revenue"))
        ebit = _clean(inc.get("EBIT"))
        pretax_income = _clean(inc.get("Pretax Income"))
        tax_provision = _clean(inc.get("Tax Provision"))
        tax_rate = (tax_provision / pretax_income) if (tax_provision is not None
                    and pretax_income not in (None, 0)) else None

        d_and_a = _clean(cf.get("Depreciation And Amortization"))
        capex = _clean(cf.get("Capital Expenditure"))
        if capex is not None:
            capex = abs(capex)  # yfinance reporta CapEx como salida de caja (negativo)

        prev_nwc = nwc_by_date[common_dates[i - 1]] if i > 0 else None
        curr_nwc = nwc_by_date[date]
        change_in_nwc = (curr_nwc - prev_nwc) if (curr_nwc is not None and prev_nwc is not None) else None

        rows.append({
            "fiscal_year": date.year,
            "fiscal_year_end_month": int(date.month),
            "fiscal_year_end_day": int(date.day),
            "revenue": revenue,
            "ebit": ebit,
            "ebitda": _clean(inc.get("EBITDA")),
            "tax_rate": tax_rate,
            "d_and_a": d_and_a,
            "capex": capex,
            "change_in_nwc": change_in_nwc,
            "net_income": _clean(inc.get("Net Income")),
            "interest_expense": _clean_interest_expense(
                _clean(inc.get("Interest Expense")), _clean(bs.get("Total Debt"))
            ),
            "total_assets": _clean(bs.get("Total Assets")),
            "total_equity": _clean(bs.get("Stockholders Equity")),
            "total_debt": _clean(bs.get("Total Debt")),
            "cash": _clean(bs.get("Cash And Cash Equivalents")),
            "current_assets": _clean(bs.get("Current Assets")),
            "current_liabilities": _clean(bs.get("Current Liabilities")),
        })

    return pd.DataFrame(rows).sort_values("fiscal_year").reset_index(drop=True)


def market_snapshot(ticker) -> dict:
    """ticker: objeto con atributo .info (dict), .ticker o .symbol para
    el propio símbolo."""
    info = ticker.info
    symbol = getattr(ticker, "ticker", None) or info.get("symbol") or ""

    market_cap = _clean(info.get("marketCap"))
    shares_outstanding = _clean(info.get("sharesOutstanding"))
    price = _clean(info.get("currentPrice")) or _clean(info.get("regularMarketPrice"))
    if price is None and market_cap and shares_outstanding:
        price = market_cap / shares_outstanding

    return {
        "symbol": symbol.upper(),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": market_cap,
        "shares_outstanding": shares_outstanding,
        "price": price,
        "beta": _clean(info.get("beta")),
        "ev_to_ebitda": _clean(info.get("enterpriseToEbitda")),
        "ev_to_revenue": _clean(info.get("enterpriseToRevenue")),
        "pe_ratio": _clean(info.get("trailingPE")),
        "price_to_sales": _clean(info.get("priceToSalesTrailing12Months")),
        "price_to_book": _clean(info.get("priceToBook")),
        "analyst_target_price": _clean(info.get("targetMeanPrice")),
        "cash": _clean(info.get("totalCash")),
        "total_debt": _clean(info.get("totalDebt")),
        # Sesión 17: rango de 52 semanas, para el football field bancario
        # (docs/METHODOLOGY.md sección 23) -- ya expuesto en .info, sin
        # coste de petición adicional.
        "week_52_high": _clean(info.get("fiftyTwoWeekHigh")),
        "week_52_low": _clean(info.get("fiftyTwoWeekLow")),
        # Auditoría sesión 15/16, hallazgo M5: divisa de reporte de los
        # estados financieros -- ver el mismo campo en data_provider.py
        # para la justificación completa. yfinance separa "currency"
        # (divisa de cotización del precio) de "financialCurrency"
        # (divisa de los propios estados financieros); esta última es la
        # relevante aquí porque es la que hay que comparar contra el USD
        # de risk_free_rate/market_risk_premium, no la de cotización (un
        # ADR puede cotizar en USD con estados financieros en otra
        # divisa). Cae a "currency" si "financialCurrency" no viene.
        "currency": info.get("financialCurrency") or info.get("currency"),
    }
