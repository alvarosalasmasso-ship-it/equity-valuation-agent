"""Motor de datos: extrae estados financieros de Alpha Vantage y los
normaliza en un DataFrame limpio, cacheado localmente en disco.

Alpha Vantage free tier limita a 25 peticiones/día -> cachear en
`data/cache/alpha_vantage/` es obligatorio, no una optimización.

Este módulo NO calcula nada de valoración (eso es engine/valuation.py).
Su única responsabilidad es: llamar a la API, cachear la respuesta cruda,
y normalizar a las series que el motor de valoración necesita como
input (revenue, ebit, tax_rate, d_and_a, capex, change_in_nwc).
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "alpha_vantage"

# Funciones de Alpha Vantage usadas (blueprint sección 1)
_FUNCTIONS = {
    "income_statement": "INCOME_STATEMENT",
    "balance_sheet": "BALANCE_SHEET",
    "cash_flow": "CASH_FLOW",
    "earnings": "EARNINGS",
    "company_overview": "OVERVIEW",
}


class AlphaVantageError(RuntimeError):
    """La API respondió pero con una nota de error/rate-limit, no datos."""


class AlphaVantageClient:
    def __init__(self, api_key: Optional[str] = None, cache_dir: Path = CACHE_DIR,
                 cache_ttl_seconds: int = 24 * 3600):
        self.api_key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "Falta ALPHA_VANTAGE_API_KEY. Defínela en un archivo .env "
                "en la raíz del proyecto."
            )
        self.cache_dir = cache_dir
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request_time = 0.0
        self._min_seconds_between_requests = 15.0

    def _cache_path(self, symbol: str, function: str) -> Path:
        return self.cache_dir / f"{symbol.upper()}_{function}.json"

    def _read_cache(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.cache_ttl_seconds:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_cache(self, path: Path, data: dict) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _fetch(self, symbol: str, endpoint_key: str, use_cache: bool = True) -> dict:
        function = _FUNCTIONS[endpoint_key]
        cache_path = self._cache_path(symbol, function)

        if use_cache:
            cached = self._read_cache(cache_path)
            if cached is not None:
                return cached

        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_seconds_between_requests:
            time.sleep(self._min_seconds_between_requests - elapsed)

        response = requests.get(
            ALPHA_VANTAGE_BASE_URL,
            params={"function": function, "symbol": symbol, "apikey": self.api_key},
            timeout=30,
        )
        self._last_request_time = time.time()
        response.raise_for_status()
        data = response.json()
        self._validate_response(data)

        self._write_cache(cache_path, data)
        return data

    @staticmethod
    def _validate_response(data: dict) -> None:
        if "Note" in data or "Information" in data:
            raise AlphaVantageError(
                data.get("Note") or data.get("Information")
            )
        if "Error Message" in data:
            raise AlphaVantageError(data["Error Message"])

    def _fetch_economic_indicator(self, function: str, extra_params: dict,
                                   cache_key: str, use_cache: bool = True) -> dict:
        """Igual que `_fetch`, pero para las funciones económicas de Alpha
        Vantage (TREASURY_YIELD, CPI, ...), que no cuelgan de un `symbol`
        sino de sus propios parámetros -- no se puede reutilizar `_fetch`
        tal cual porque este construye la cache key y los params de la
        petición a partir de un símbolo."""
        cache_path = self.cache_dir / f"{cache_key}.json"

        if use_cache:
            cached = self._read_cache(cache_path)
            if cached is not None:
                return cached

        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_seconds_between_requests:
            time.sleep(self._min_seconds_between_requests - elapsed)

        response = requests.get(
            ALPHA_VANTAGE_BASE_URL,
            params={"function": function, **extra_params, "apikey": self.api_key},
            timeout=30,
        )
        self._last_request_time = time.time()
        response.raise_for_status()
        data = response.json()
        self._validate_response(data)

        self._write_cache(cache_path, data)
        return data

    def treasury_yield(self, maturity: str = "10year", use_cache: bool = True) -> float:
        """Rendimiento del Treasury de EE.UU. a `maturity` (10 años por
        defecto -- la convención estándar de risk-free rate en un CAPM),
        vía la función económica TREASURY_YIELD. Antes de esta corrección
        (auditoría sesión 15, hallazgo I1) el risk-free rate usado en toda
        la app era una constante congelada de cuando se construyó el
        Excel de referencia (~noviembre 2024). Devuelve la tasa como
        fracción (0.0415 para 4.15%), no en puntos porcentuales -- el
        payload de Alpha Vantage sí viene en puntos porcentuales."""
        data = self._fetch_economic_indicator(
            "TREASURY_YIELD", {"interval": "daily", "maturity": maturity},
            f"ECON_TREASURY_YIELD_{maturity}", use_cache,
        )
        points = [p for p in data.get("data", []) if p.get("value") not in (None, ".", "")]
        if not points:
            raise AlphaVantageError("TREASURY_YIELD no devolvió ningún dato válido.")
        latest = max(points, key=lambda p: p["date"])
        return float(latest["value"]) / 100.0

    def income_statement(self, symbol: str, use_cache: bool = True) -> dict:
        return self._fetch(symbol, "income_statement", use_cache)

    def balance_sheet(self, symbol: str, use_cache: bool = True) -> dict:
        return self._fetch(symbol, "balance_sheet", use_cache)

    def cash_flow(self, symbol: str, use_cache: bool = True) -> dict:
        return self._fetch(symbol, "cash_flow", use_cache)

    def earnings(self, symbol: str, use_cache: bool = True) -> dict:
        return self._fetch(symbol, "earnings", use_cache)

    def company_overview(self, symbol: str, use_cache: bool = True) -> dict:
        return self._fetch(symbol, "company_overview", use_cache)


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

def _to_float(value) -> Optional[float]:
    if value is None or value == "None":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_interest_expense(interest_expense: Optional[float], total_debt: Optional[float]) -> Optional[float]:
    """Alpha Vantage a veces reporta interestExpense=0 en el año más
    reciente pese a que la compañía mantiene deuda real (verificado con
    AAPL: FY2023 = $3.933bn real, FY2024 = 0 reportado con total_debt =
    $119bn, FY2025 = None). Un interest_expense de 0 con deuda material
    es casi con certeza un hueco de datos, no un coste de deuda real de
    cero. Sin este filtro, cost_of_debt() calcularía silenciosamente un
    0% de coste de deuda (bug real encontrado y corregido en sesión,
    afectaba al WACC de AAPL). Se trata como dato faltante (None) para
    que .dropna().iloc[-1] caiga al último año con un valor genuino."""
    if interest_expense == 0 and (total_debt or 0) > 0:
        return None
    return interest_expense


def _annual_reports_by_year(payload: dict) -> dict:
    return {r["fiscalDateEnding"][:4]: r for r in payload.get("annualReports", [])}


# Mismo esquema que engine.yfinance_provider.HISTORICAL_FINANCIALS_COLUMNS
# (por diseño, ambos proveedores son intercambiables) -- si se añade una
# columna aquí, añadir también allí.
HISTORICAL_FINANCIALS_COLUMNS = [
    "fiscal_year", "fiscal_year_end_month", "fiscal_year_end_day", "revenue", "ebit",
    "ebitda", "tax_rate", "d_and_a", "capex", "change_in_nwc", "net_income",
    "interest_expense", "total_assets", "total_equity", "total_debt", "cash",
    "current_assets", "current_liabilities",
]


def historical_financials(client: AlphaVantageClient, symbol: str,
                           use_cache: bool = True) -> pd.DataFrame:
    """Combina INCOME_STATEMENT + BALANCE_SHEET + CASH_FLOW en un
    DataFrame anual limpio, con las columnas que necesita
    engine.valuation.unlevered_fcf: revenue, ebit, tax_rate, d_and_a,
    capex, change_in_nwc. Ordenado de año más antiguo a más reciente.

    Net Working Capital = (Current Assets - Cash) - (Current Liabilities - Deuda a corto)
    Delta NWC = NWC(t) - NWC(t-1)   (positivo = consumo de caja)
    """
    income = _annual_reports_by_year(client.income_statement(symbol, use_cache))
    balance = _annual_reports_by_year(client.balance_sheet(symbol, use_cache))
    cash_flow = _annual_reports_by_year(client.cash_flow(symbol, use_cache))

    years = sorted(set(income) & set(balance) & set(cash_flow))
    if not years:
        # Sin ningún año en común entre los tres estados -- p.ej. un símbolo
        # válido pero con historial no solapado. pd.DataFrame([]).sort_values(...)
        # lanzaría KeyError('fiscal_year') en vez de un DataFrame vacío
        # predecible (auditoría sesión 15, hallazgo I3 -- mismo bug real
        # encontrado también en yfinance_provider.py para tickers inválidos).
        return pd.DataFrame(columns=HISTORICAL_FINANCIALS_COLUMNS)
    rows = []
    nwc_by_year = {}

    for year in years:
        bs = balance[year]
        current_assets = _to_float(bs.get("totalCurrentAssets"))
        cash = _to_float(bs.get("cashAndShortTermInvestments"))
        current_liabilities = _to_float(bs.get("totalCurrentLiabilities"))
        short_term_debt = _to_float(bs.get("shortTermDebt")) or 0.0
        if current_assets is None or current_liabilities is None:
            nwc_by_year[year] = None
        else:
            nwc_by_year[year] = (current_assets - (cash or 0.0)) - (current_liabilities - short_term_debt)

    for i, year in enumerate(years):
        inc = income[year]
        cf = cash_flow[year]
        bs = balance[year]

        revenue = _to_float(inc.get("totalRevenue"))
        ebit = _to_float(inc.get("ebit")) or _to_float(inc.get("operatingIncome"))
        pretax_income = _to_float(inc.get("incomeBeforeTax"))
        tax_expense = _to_float(inc.get("incomeTaxExpense"))
        tax_rate = (tax_expense / pretax_income) if (tax_expense is not None
                    and pretax_income not in (None, 0)) else None

        d_and_a = (_to_float(inc.get("depreciationAndAmortization"))
                   or _to_float(cf.get("depreciationDepletionAndAmortization")))
        capex = _to_float(cf.get("capitalExpenditures"))

        prev_nwc = nwc_by_year[years[i - 1]] if i > 0 else None
        curr_nwc = nwc_by_year[year]
        change_in_nwc = (curr_nwc - prev_nwc) if (curr_nwc is not None and prev_nwc is not None) else None

        # "YYYY-MM-DD" -> mes/día de cierre de ejercicio, para poder calcular
        # el stub period real (engine.valuation.compute_stub_fraction).
        fiscal_date_ending = inc.get("fiscalDateEnding", "")
        date_parts = fiscal_date_ending.split("-") if fiscal_date_ending else []
        fiscal_year_end_month = int(date_parts[1]) if len(date_parts) == 3 else None
        fiscal_year_end_day = int(date_parts[2]) if len(date_parts) == 3 else None

        rows.append({
            "fiscal_year": int(year),
            "fiscal_year_end_month": fiscal_year_end_month,
            "fiscal_year_end_day": fiscal_year_end_day,
            "revenue": revenue,
            "ebit": ebit,
            "ebitda": _to_float(inc.get("ebitda")),
            "tax_rate": tax_rate,
            "d_and_a": d_and_a,
            "capex": capex,
            "change_in_nwc": change_in_nwc,
            "net_income": _to_float(inc.get("netIncome")),
            "interest_expense": _clean_interest_expense(
                _to_float(inc.get("interestExpense")), _to_float(bs.get("shortLongTermDebtTotal"))
            ),
            "total_assets": _to_float(bs.get("totalAssets")),
            "total_equity": _to_float(bs.get("totalShareholderEquity")),
            "total_debt": _to_float(bs.get("shortLongTermDebtTotal")),
            "cash": _to_float(bs.get("cashAndShortTermInvestments")),
            "current_assets": _to_float(bs.get("totalCurrentAssets")),
            "current_liabilities": _to_float(bs.get("totalCurrentLiabilities")),
        })

    df = pd.DataFrame(rows).sort_values("fiscal_year").reset_index(drop=True)
    return df


def market_snapshot(client: AlphaVantageClient, symbol: str, use_cache: bool = True) -> dict:
    """Datos de mercado puntuales desde COMPANY_OVERVIEW: precio implícito
    vía market cap / shares, beta, deuda y caja más recientes, múltiplo
    EV/EBITDA de mercado (útil como múltiplo de salida, ver
    engine.valuation.exit_multiple_terminal_value).
    """
    overview = client.company_overview(symbol, use_cache)
    balance = _annual_reports_by_year(client.balance_sheet(symbol, use_cache))
    latest_year = max(balance) if balance else None
    latest_bs = balance.get(latest_year, {})

    shares_outstanding = _to_float(overview.get("SharesOutstanding"))
    market_cap = _to_float(overview.get("MarketCapitalization"))

    return {
        "symbol": symbol.upper(),
        "sector": overview.get("Sector"),
        "industry": overview.get("Industry"),
        "market_cap": market_cap,
        "shares_outstanding": shares_outstanding,
        "price": (market_cap / shares_outstanding) if (market_cap and shares_outstanding) else None,
        "beta": _to_float(overview.get("Beta")),
        "ev_to_ebitda": _to_float(overview.get("EVToEBITDA")),
        "ev_to_revenue": _to_float(overview.get("EVToRevenue")),
        "pe_ratio": _to_float(overview.get("PERatio")),
        "price_to_sales": _to_float(overview.get("PriceToSalesRatioTTM")),
        "price_to_book": _to_float(overview.get("PriceToBookRatio")),
        "analyst_target_price": _to_float(overview.get("AnalystTargetPrice")),
        "cash": _to_float(latest_bs.get("cashAndShortTermInvestments")),
        "total_debt": _to_float(latest_bs.get("shortLongTermDebtTotal")),
    }
