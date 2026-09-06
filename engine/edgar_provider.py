"""SEC EDGAR (data.sec.gov) como VALIDADOR CRUZADO, no como proveedor
alternativo -- sesión 17, retomando la investigación pausada en
docs/METHODOLOGY.md sección 26. El motivo de esta sesión: 3 de los
bugs de código reales encontrados evaluando la herramienta de punta a
punta (M8, M9, I13) eran huecos/inconsistencias del proveedor de datos
(yfinance/Alpha Vantage), no errores del motor de valoración -- se
descubrieron auditando empresa por empresa a mano. Este módulo consulta
SEC EDGAR (gratis, sin API key, sin cuota diaria) para los mismos
campos y avisa cuando difieren de lo que ya reporta el proveedor activo
más de un umbral -- detectar ese PATRÓN de bug automáticamente, en vez
de encontrarlo ticker a ticker.

Deliberadamente EXCLUYE dos campos de la comparación (ver sección 26):
- `total_debt`: SEC EDGAR reporta solo deuda financiera pura; Alpha
  Vantage/yfinance incluyen además obligaciones de leasing (post ASC
  842, ver hallazgo I8) -- una diferencia de METODOLOGÍA ya investigada
  y entendida, no un error. Compararlos generaría falsos positivos
  sistemáticos.
- `d_and_a`: la reconstrucción desde tags XBRL resultó no fiable en la
  investigación previa (hasta 18% de diferencia para MSFT, varias
  combinaciones de tags probadas) -- no hay una "verdad EDGAR" en la
  que confiar todavía para este campo.

La taxonomía US-GAAP migra de tag por empresa y por año -- confirmado
de nuevo esta sesión con datos reales: MSFT reporta `InterestExpense`
hasta FY2024 y pasa a `InterestExpenseNonoperating` desde FY2025, sin
solaparse. `_extract_annual_series()` FUSIONA todos los tags de
fallback de un concepto (no solo el primero con datos), o se perderían
los años más recientes de empresas que migraron de tag.
"""

import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

TICKER_CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "sec_edgar"

# Verificado con datos reales de Big Tech (sección 26 y esta sesión):
# orden de preferencia, no "cualquiera vale" -- el primer tag de cada
# lista es el más estándar/más ampliamente usado.
CONCEPT_TAGS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "ebit": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "total_assets": ["Assets"],
    "total_equity": ["StockholdersEquity",
                      "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "interest_expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
                          "InterestExpenseDebtExcludingAmortization"],
}

# True: hecho que cubre un periodo de ~1 año (cuenta de resultados).
# False: hecho instantáneo a fecha de cierre (balance).
CONCEPT_IS_DURATION: dict[str, bool] = {
    "revenue": True, "ebit": True, "net_income": True, "interest_expense": True,
    "total_assets": False, "total_equity": False, "current_assets": False,
    "current_liabilities": False, "cash": False,
}

DEFAULT_MISMATCH_TOLERANCE = 0.15
MIN_DURATION_DAYS = 330
MAX_DURATION_DAYS = 400


class EdgarError(RuntimeError):
    """SEC EDGAR respondió pero sin los datos esperados (ticker sin CIK
    en el mapeo, sin companyfacts, etc.) -- distinto de un fallo de red."""


class EdgarClient:
    def __init__(self, user_agent: Optional[str] = None, cache_dir: Path = CACHE_DIR,
                 cache_ttl_seconds: int = 24 * 3600):
        self.user_agent = user_agent or os.environ.get("SEC_EDGAR_USER_AGENT")
        if not self.user_agent:
            raise RuntimeError(
                "Falta SEC_EDGAR_USER_AGENT. SEC EDGAR exige un User-Agent con un "
                "contacto real (p.ej. 'NombreApp contacto@email.com') para acceso "
                "automatizado a data.sec.gov -- defínela en un archivo .env en la "
                "raíz del proyecto."
            )
        self.cache_dir = cache_dir
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request_time = 0.0
        # SEC pide un máximo de ~10 peticiones/segundo -- 0.15s de
        # margen es sobradamente conservador, no un límite real de cuota
        # diaria como Alpha Vantage.
        self._min_seconds_between_requests = 0.15

    def _headers(self) -> dict:
        return {"User-Agent": self.user_agent}

    def _cache_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.json"

    def _read_cache(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl_seconds:
            return None
        import json
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_cache(self, path: Path, data: dict) -> None:
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _get(self, url: str, cache_name: str, use_cache: bool = True) -> dict:
        cache_path = self._cache_path(cache_name)
        if use_cache:
            cached = self._read_cache(cache_path)
            if cached is not None:
                return cached

        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_seconds_between_requests:
            time.sleep(self._min_seconds_between_requests - elapsed)

        response = requests.get(url, headers=self._headers(), timeout=30)
        self._last_request_time = time.time()
        response.raise_for_status()
        data = response.json()
        self._write_cache(cache_path, data)
        return data

    def ticker_to_cik(self, ticker: str, use_cache: bool = True) -> str:
        """Devuelve el CIK de 10 dígitos (con ceros a la izquierda) del
        ticker, vía el mapeo estático que publica la propia SEC."""
        mapping = self._get(TICKER_CIK_MAP_URL, "company_tickers", use_cache=use_cache)
        ticker_upper = ticker.upper()
        for row in mapping.values():
            if row.get("ticker") == ticker_upper:
                return str(row["cik_str"]).zfill(10)
        raise EdgarError(f"'{ticker}' no aparece en el mapeo ticker->CIK de SEC EDGAR.")

    def company_facts(self, ticker: str, use_cache: bool = True) -> dict:
        """Devuelve el JSON completo de `companyfacts` (todos los hechos
        XBRL reportados por la compañía) para el ticker dado."""
        cik = self.ticker_to_cik(ticker, use_cache=use_cache)
        url = COMPANY_FACTS_URL_TEMPLATE.format(cik=cik)
        return self._get(url, f"companyfacts_{ticker.upper()}", use_cache=use_cache)


def _extract_annual_series(company_facts: dict, tags: list[str], is_duration: bool) -> dict[date, float]:
    """Fusiona TODOS los tags de fallback de un concepto en un único
    dict {fecha_fin_periodo: valor}, restringido a formularios 10-K (los
    únicos auditados) -- ver docstring del módulo sobre por qué fusionar
    en vez de usar solo el primer tag con datos (migración de tag entre
    años, caso real: MSFT `InterestExpense` -> `InterestExpenseNonoperating`).
    Los tags se recorren en orden INVERSO de preferencia para que, ante
    un mismo periodo reportado por dos tags, el de mayor prioridad
    (primero en la lista) sea el que quede al final."""
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    result: dict[date, float] = {}
    for tag in reversed(tags):
        tag_data = us_gaap.get(tag)
        if not tag_data:
            continue
        for entry in tag_data.get("units", {}).get("USD", []):
            if entry.get("form") not in ("10-K", "10-K/A"):
                continue
            end_raw = entry.get("end")
            if not end_raw:
                continue
            if is_duration:
                if entry.get("fp") != "FY":
                    continue
                start_raw = entry.get("start")
                if not start_raw:
                    continue
                start_d, end_d = date.fromisoformat(start_raw), date.fromisoformat(end_raw)
                duration_days = (end_d - start_d).days
                if not (MIN_DURATION_DAYS <= duration_days <= MAX_DURATION_DAYS):
                    continue
                result[end_d] = entry["val"]
            else:
                result[date.fromisoformat(end_raw)] = entry["val"]
    return result


def get_concept_value(company_facts: dict, concept: str, period_end: date,
                       tolerance_days: int = 10) -> Optional[float]:
    """Valor de `concept` (según `CONCEPT_TAGS`) cuyo periodo termina en
    `period_end`, o dentro de `tolerance_days` de esa fecha (colchón
    frente a pequeñas diferencias de fin de ejercicio fiscal entre
    fuentes). `None` si SEC EDGAR no reporta ese concepto para ese año."""
    if concept not in CONCEPT_TAGS:
        raise ValueError(f"Concepto desconocido: '{concept}' -- no está en CONCEPT_TAGS")
    series = _extract_annual_series(company_facts, CONCEPT_TAGS[concept], CONCEPT_IS_DURATION[concept])
    if not series:
        return None
    if period_end in series:
        return series[period_end]
    closest = min(series.keys(), key=lambda d: abs((d - period_end).days))
    if abs((closest - period_end).days) <= tolerance_days:
        return series[closest]
    return None


@dataclass
class ConceptComparison:
    concept: str
    provider_value: float
    edgar_value: float
    relative_diff: float  # (provider - edgar) / |edgar|

    @property
    def is_mismatch(self) -> bool:
        return abs(self.relative_diff) > DEFAULT_MISMATCH_TOLERANCE


def cross_validate_latest_year(company_facts: dict, history_row, tolerance: float = DEFAULT_MISMATCH_TOLERANCE,
                                ) -> list[ConceptComparison]:
    """Compara el último año del histórico YA usado por el pipeline
    (`history_row`, una fila de `engine.*_provider.historical_financials()`
    -- necesita `fiscal_year`, `fiscal_year_end_month`, `fiscal_year_end_day`
    y las columnas de cada concepto) contra SEC EDGAR, para los conceptos
    donde EDGAR reconstruye con fiabilidad verificada (ver docstring del
    módulo). Devuelve solo las comparaciones donde SEC EDGAR SÍ tiene
    dato para ese año -- si EDGAR no reporta el concepto (taxonomía
    distinta, empresa fuera de su cobertura), se omite en vez de
    tratarlo como un mismatch."""
    period_end = date(int(history_row["fiscal_year"]), int(history_row["fiscal_year_end_month"]),
                       int(history_row["fiscal_year_end_day"]))
    comparisons = []
    for concept in CONCEPT_TAGS:
        provider_value = history_row.get(concept)
        if provider_value is None or (isinstance(provider_value, float) and provider_value != provider_value):
            continue
        edgar_value = get_concept_value(company_facts, concept, period_end)
        if edgar_value is None or edgar_value == 0:
            continue
        relative_diff = (provider_value - edgar_value) / abs(edgar_value)
        comparisons.append(ConceptComparison(
            concept=concept, provider_value=float(provider_value),
            edgar_value=float(edgar_value), relative_diff=float(relative_diff),
        ))
    return comparisons
