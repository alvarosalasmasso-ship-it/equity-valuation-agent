"""Tests de engine/edgar_provider.py con fixtures sintéticas del formato
real de `companyfacts` (verificado a mano contra data.sec.gov con MSFT
durante el desarrollo, ver docs/METHODOLOGY.md sección 33). Sin llamadas
de red: las funciones puras reciben el JSON ya construido, igual que el
resto de la suite con Alpha Vantage/yfinance.
"""

from datetime import date

import pandas as pd
import pytest

from engine.edgar_provider import (
    CONCEPT_TAGS,
    ConceptComparison,
    cross_validate_latest_year,
    get_concept_value,
)


def _fact(start, end, val, form="10-K", fp="FY"):
    entry = {"end": end, "val": val, "form": form, "fp": fp, "fy": int(end[:4]), "filed": end}
    if start is not None:
        entry["start"] = start
    return entry


def _company_facts(us_gaap: dict) -> dict:
    return {"entityName": "TEST CORP", "facts": {"us-gaap": us_gaap}}


def test_get_concept_value_finds_duration_fact_by_exact_period_end():
    facts = _company_facts({
        "OperatingIncomeLoss": {"units": {"USD": [
            _fact("2024-07-01", "2025-06-30", 100_000_000),
            _fact("2025-07-01", "2026-06-30", 120_000_000),
        ]}}
    })
    assert get_concept_value(facts, "ebit", date(2026, 6, 30)) == 120_000_000
    assert get_concept_value(facts, "ebit", date(2025, 6, 30)) == 100_000_000


def test_get_concept_value_finds_instant_fact():
    facts = _company_facts({
        "Assets": {"units": {"USD": [_fact(None, "2026-06-30", 758_376_000_000)]}}
    })
    assert get_concept_value(facts, "total_assets", date(2026, 6, 30)) == 758_376_000_000


def test_get_concept_value_ignores_non_10k_forms():
    """Un hecho de un 10-Q (trimestral) no debe usarse como si fuera el
    dato anual auditado -- solo 10-K/10-K-A."""
    facts = _company_facts({
        "OperatingIncomeLoss": {"units": {"USD": [
            _fact("2026-01-01", "2026-03-31", 30_000_000, form="10-Q", fp="Q1"),
        ]}}
    })
    assert get_concept_value(facts, "ebit", date(2026, 3, 31)) is None


def test_get_concept_value_ignores_duration_shorter_than_a_year():
    """Filtra hechos de duración distinta a ~1 año (evita colar un
    periodo trimestral etiquetado por error como FY)."""
    facts = _company_facts({
        "NetIncomeLoss": {"units": {"USD": [
            _fact("2026-04-01", "2026-06-30", 10_000_000, fp="FY"),  # ~90 días, no un año
        ]}}
    })
    assert get_concept_value(facts, "net_income", date(2026, 6, 30)) is None


def test_get_concept_value_within_tolerance_days():
    facts = _company_facts({
        "Assets": {"units": {"USD": [_fact(None, "2026-06-28", 500_000_000)]}}
    })
    assert get_concept_value(facts, "total_assets", date(2026, 6, 30), tolerance_days=10) == 500_000_000
    assert get_concept_value(facts, "total_assets", date(2026, 6, 30), tolerance_days=1) is None


def test_get_concept_value_returns_none_when_concept_missing_from_edgar():
    facts = _company_facts({})
    assert get_concept_value(facts, "ebit", date(2026, 6, 30)) is None


def test_get_concept_value_raises_on_unknown_concept():
    with pytest.raises(ValueError, match="Concepto desconocido"):
        get_concept_value(_company_facts({}), "total_debt", date(2026, 6, 30))


def test_get_concept_value_merges_fallback_tags_across_years():
    """Regresión (sesión 17): MSFT reporta interest_expense bajo
    "InterestExpense" hasta FY2024 y bajo "InterestExpenseNonoperating"
    desde FY2025, sin solaparse -- quedarse con el primer tag no vacío
    perdería los años más recientes. Debe fusionar ambos."""
    facts = _company_facts({
        "InterestExpense": {"units": {"USD": [
            _fact("2023-07-01", "2024-06-30", 2_935_000_000),
        ]}},
        "InterestExpenseNonoperating": {"units": {"USD": [
            _fact("2024-07-01", "2025-06-30", 2_385_000_000),
            _fact("2025-07-01", "2026-06-30", 3_051_000_000),
        ]}},
    })
    assert get_concept_value(facts, "interest_expense", date(2024, 6, 30)) == 2_935_000_000
    assert get_concept_value(facts, "interest_expense", date(2025, 6, 30)) == 2_385_000_000
    assert get_concept_value(facts, "interest_expense", date(2026, 6, 30)) == 3_051_000_000


def test_get_concept_value_merges_capex_tag_migration():
    """Regresión (sesión 17, repaso de punta a punta con AMZN): capex
    migra de "PaymentsToAcquirePropertyPlantAndEquipment" (hasta ~2016)
    a "PaymentsToAcquireProductiveAssets" (2023+, coincide exacto con el
    CapEx real que ya usa el pipeline)."""
    facts = _company_facts({
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            _fact("2015-01-01", "2015-12-31", 4_589_000_000),
        ]}},
        "PaymentsToAcquireProductiveAssets": {"units": {"USD": [
            _fact("2025-01-01", "2025-12-31", 131_819_000_000),
        ]}},
    })
    assert get_concept_value(facts, "capex", date(2015, 12, 31)) == 4_589_000_000
    assert get_concept_value(facts, "capex", date(2025, 12, 31)) == 131_819_000_000


def test_get_concept_value_d_and_a_missing_for_companies_without_combined_tag():
    """MSFT/GOOGL reportan D&A en 3+ tags separados que no suman al
    mismo total que el proveedor (sección 26) -- sin tag combinado, debe
    devolver None (se omite la comparación), no un valor parcial/erróneo
    reconstruido a partir de componentes."""
    facts = _company_facts({
        "Depreciation": {"units": {"USD": [_fact("2025-07-01", "2026-06-30", 34_300_000_000)]}},
        "AmortizationOfIntangibleAssets": {"units": {"USD": [_fact("2025-07-01", "2026-06-30", 4_700_000_000)]}},
    })
    assert get_concept_value(facts, "d_and_a", date(2026, 6, 30)) is None


def test_get_concept_value_prefers_higher_priority_tag_on_conflict():
    """Si dos tags de fallback reportan el MISMO periodo, gana el de
    mayor prioridad (el primero en CONCEPT_TAGS), no el último leído."""
    facts = _company_facts({
        "Revenues": {"units": {"USD": [_fact("2025-01-01", "2025-12-31", 100)]}},
        "SalesRevenueNet": {"units": {"USD": [_fact("2025-01-01", "2025-12-31", 999)]}},
    })
    assert get_concept_value(facts, "revenue", date(2025, 12, 31)) == 100


def _history_row(**overrides) -> pd.Series:
    base = {
        "fiscal_year": 2026, "fiscal_year_end_month": 6, "fiscal_year_end_day": 30,
        "revenue": 331_839_000_000.0, "ebit": 155_237_000_000.0, "net_income": 133_749_000_000.0,
        "total_assets": 758_376_000_000.0, "total_equity": 442_387_000_000.0,
        "current_assets": 207_710_000_000.0, "current_liabilities": 168_825_000_000.0,
        "cash": 20_935_000_000.0, "interest_expense": 3_051_000_000.0, "capex": 115_948_000_000.0,
    }
    base.update(overrides)
    return pd.Series(base)


def _msft_like_facts() -> dict:
    end = "2026-06-30"
    start = "2025-07-01"
    return _company_facts({
        "Revenues": {"units": {"USD": [_fact(start, end, 331_839_000_000)]}},
        "OperatingIncomeLoss": {"units": {"USD": [_fact(start, end, 155_237_000_000)]}},
        "NetIncomeLoss": {"units": {"USD": [_fact(start, end, 133_749_000_000)]}},
        "Assets": {"units": {"USD": [_fact(None, end, 758_376_000_000)]}},
        "StockholdersEquity": {"units": {"USD": [_fact(None, end, 442_387_000_000)]}},
        "AssetsCurrent": {"units": {"USD": [_fact(None, end, 207_710_000_000)]}},
        "LiabilitiesCurrent": {"units": {"USD": [_fact(None, end, 168_825_000_000)]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_fact(None, end, 20_935_000_000)]}},
        "InterestExpenseNonoperating": {"units": {"USD": [_fact(start, end, 3_051_000_000)]}},
        "PaymentsToAcquireProductiveAssets": {"units": {"USD": [_fact(start, end, 115_948_000_000)]}},
    })


def test_cross_validate_latest_year_all_match():
    comparisons = cross_validate_latest_year(_msft_like_facts(), _history_row())
    assert len(comparisons) == 10
    assert all(c.relative_diff == pytest.approx(0.0, abs=1e-9) for c in comparisons)
    assert all(not c.is_mismatch for c in comparisons)


def test_cross_validate_latest_year_computes_relative_diff_below_default_tolerance():
    """Reproduce el hallazgo real C2 tal cual se encontró: "ebit" de
    yfinance/Alpha Vantage pre-fix (pretax+interest) daba +8.86% frente
    a Operating Income para MSFT -- por debajo del umbral de aviso por
    defecto (15%), así que NO se habría detectado como "mismatch"
    automático; se encontró inspeccionando el número a mano, no por el
    aviso. El umbral generoso evita falsos positivos en el resto de
    conceptos (que en la práctica coinciden exactos) sin pretender que
    detecta cualquier discrepancia por pequeña que sea."""
    row = _history_row(ebit=168_985_000_000.0)  # el "EBIT" incorrecto pre-fix
    comparisons = cross_validate_latest_year(_msft_like_facts(), row)
    ebit_comparison = next(c for c in comparisons if c.concept == "ebit")
    assert ebit_comparison.relative_diff == pytest.approx((168_985 - 155_237) / 155_237, rel=1e-4)
    assert not ebit_comparison.is_mismatch


def test_cross_validate_latest_year_flags_large_mismatch():
    """Con una discrepancia grande (magnitud real observada en AMZN/JNJ,
    +24-31%), sí debe marcarse como mismatch."""
    row = _history_row(ebit=155_237_000_000.0 * 1.30)
    comparisons = cross_validate_latest_year(_msft_like_facts(), row)
    ebit_comparison = next(c for c in comparisons if c.concept == "ebit")
    assert ebit_comparison.is_mismatch


def test_cross_validate_latest_year_skips_missing_provider_value():
    row = _history_row(cash=None)
    comparisons = cross_validate_latest_year(_msft_like_facts(), row)
    assert "cash" not in [c.concept for c in comparisons]


def test_cross_validate_latest_year_skips_concept_edgar_does_not_cover():
    facts = _company_facts({})  # SEC EDGAR sin ningún dato
    comparisons = cross_validate_latest_year(facts, _history_row())
    assert comparisons == []


def test_cross_validate_latest_year_never_compares_total_debt():
    """Sección 26: total_debt (EDGAR excluye leasing, diferencia de
    metodología ya conocida) queda deliberadamente fuera de CONCEPT_TAGS
    -- compararlo generaría falsos positivos sistemáticos."""
    assert "total_debt" not in CONCEPT_TAGS
