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


def live_price(ticker) -> Optional[float]:
    """Cotización actual real, vía `.info` (mismo campo que ya usa
    `market_snapshot()` de este módulo) -- pensada para usarse como
    fuente de precio fiable incluso cuando los datos FUNDAMENTALES
    vienen de Alpha Vantage.

    Auditoría (sesión 17): investigando el backtest walk-forward se
    encontró que `engine.data_provider.market_snapshot()` deriva
    "price" como `MarketCapitalization / SharesOutstanding` de Alpha
    Vantage, y ese cociente resultó estar mal para 2 de 5 tickers reales
    de Big Tech -- GOOGL (2.08x inflado: `SharesOutstanding` de AV solo
    cuenta una de las dos clases de acciones de Alphabet, mientras
    `MarketCapitalization` sí refleja la compañía completa) y META
    (1.155x inflado, con `SharesOutstanding`/`SharesFloat` consistentes
    entre sí -- aquí `MarketCapitalization` parece desincronizado en el
    tiempo respecto al resto del snapshot, no un problema de clases de
    acciones). Ninguno de los dos patrones es detectable de forma
    genérica y fiable solo con los campos de Alpha Vantage. yfinance ya
    es una dependencia transversal de la app (el risk-free rate en vivo,
    `treasury_yield_10y()`, se usa sin importar el modo) -- extenderla
    a la cotización en vivo no añade una categoría nueva de fragilidad."""
    info = ticker.info
    price = _clean(info.get("currentPrice")) or _clean(info.get("regularMarketPrice"))
    if price is None:
        market_cap = _clean(info.get("marketCap"))
        shares = _clean(info.get("sharesOutstanding"))
        if market_cap and shares:
            price = market_cap / shares
    return price


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
        # Auditoría sesión 17 (validación cruzada con SEC EDGAR, hallazgo
        # crítico, mismo mecanismo que engine.data_provider): la fila
        # "EBIT" de yfinance NO es Operating Income -- es Pretax Income +
        # Interest Expense, que INCLUYE partidas no operativas (ganancias
        # de inversión, resultado por método de participación). Verificado
        # con datos reales: MSFT "EBIT"=$168.985bn vs "Operating
        # Income"=$155.237bn (+8.86%, coincide exacto con SEC EDGAR); en
        # AMZN/GOOGL/JNJ la brecha llega a +24-31%, solo AAPL sin
        # diferencia. El Excel de referencia usa Operating Income por
        # segmento, nunca "pretax + interest" (ver docs/AUDIT.md).
        ebit = _clean(inc.get("Operating Income"))
        if ebit is None:
            ebit = _clean(inc.get("EBIT"))
        pretax_income = _clean(inc.get("Pretax Income"))
        tax_provision = _clean(inc.get("Tax Provision"))
        tax_rate = (tax_provision / pretax_income) if (tax_provision is not None
                    and pretax_income not in (None, 0)) else None

        d_and_a = _clean(cf.get("Depreciation And Amortization"))
        if d_and_a is None:
            # Utilities/energía con contabilidad de "depletion" (p.ej. D
            # -Dominion Energy-, auditoría sesión 17) reportan esta partida
            # como "Depreciation Amortization Depletion" en vez de la
            # etiqueta estándar -- sin este fallback, default_assumptions_
            # from_history() lanza ValueError con un mensaje que apunta a
            # "estados financieros no estándar (bancos/REITs)", diagnóstico
            # engañoso para una utility con datos perfectamente estándar.
            d_and_a = _clean(cf.get("Depreciation Amortization Depletion"))
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


def _latest_balance_sheet_column(ticker) -> Optional[pd.Series]:
    """Auditoría sesión 17: `ticker.info["totalDebt"]`/`["totalCash"]`
    resultaron NO ser fiables -- verificado con datos reales de AMZN,
    `info["totalDebt"]` daba $251.6bn frente a los $153.0bn de
    `ticker.balance_sheet.loc["Total Debt"]` (que sí coincide exacto con
    Alpha Vantage: deuda financiera + obligaciones de leasing). La
    diferencia es grande en empresas con mucho leasing (AMZN) y pequeña
    en las que no (KO/PG/JNJ, verificado -2.7%/0.0%/+2.3%), pero
    `historical_financials()` de este mismo módulo YA usaba la fuente
    fiable (`balance_sheet`) -- esto solo iguala `market_snapshot()` al
    mismo estándar, en vez de tener dos fuentes distintas del mismo dato
    dentro del propio código. Devuelve la columna (fecha) más reciente
    del balance, o None si no hay ninguna disponible."""
    balance = ticker.balance_sheet
    if balance is None or balance.empty:
        return None
    return balance[sorted(balance.columns)[-1]]


def market_snapshot(ticker) -> dict:
    """ticker: objeto con atributo .info (dict), .ticker o .symbol para
    el propio símbolo, y .balance_sheet (DataFrame) para deuda/caja
    fiables -- ver _latest_balance_sheet_column()."""
    info = ticker.info
    symbol = getattr(ticker, "ticker", None) or info.get("symbol") or ""
    bs_latest = _latest_balance_sheet_column(ticker)

    market_cap = _clean(info.get("marketCap"))
    shares_outstanding = _clean(info.get("sharesOutstanding"))
    price = _clean(info.get("currentPrice")) or _clean(info.get("regularMarketPrice"))
    if price is None and market_cap and shares_outstanding:
        price = market_cap / shares_outstanding

    cash = _clean(bs_latest.get("Cash And Cash Equivalents")) if bs_latest is not None else None
    total_debt = _clean(bs_latest.get("Total Debt")) if bs_latest is not None else None
    if cash is None:
        cash = _clean(info.get("totalCash"))  # mejor esfuerzo si no hay balance sheet disponible
    if total_debt is None:
        total_debt = _clean(info.get("totalDebt"))

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
        "cash": cash,
        "total_debt": total_debt,
        # Sesión 17: rango de 52 semanas, para el football field bancario
        # (docs/METHODOLOGY.md sección 23) -- ya expuesto en .info, sin
        # coste de petición adicional.
        "week_52_high": _clean(info.get("fiftyTwoWeekHigh")),
        "week_52_low": _clean(info.get("fiftyTwoWeekLow")),
        # Sesión 17: yfinance no da el desglose por tramo de rating que sí
        # da Alpha Vantage (ver el mismo campo en data_provider.py) -- lo
        # más parecido gratis en .info es la recomendación consenso
        # (texto "buy"/"hold"/...), su media en escala 1-5 y el número de
        # analistas, más el rango alto/bajo del precio objetivo (no solo
        # la media, ya expuesta como analyst_target_price).
        "analyst_recommendation_key": info.get("recommendationKey"),
        "analyst_recommendation_mean": _clean(info.get("recommendationMean")),
        "analyst_num_opinions": _clean(info.get("numberOfAnalystOpinions")),
        "analyst_target_price_low": _clean(info.get("targetLowPrice")),
        "analyst_target_price_high": _clean(info.get("targetHighPrice")),
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
