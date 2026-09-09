"""Tests de app/streamlit_app.py vía streamlit.testing.v1.AppTest --
sin navegador, sin red. Cierra el hallazgo N2 de docs/AUDIT.md (antes
"sin tests automatizados, aceptado como práctica estándar") ahora que
Streamlit trae un framework de test headless para exactamente esto.

Mismo principio que el resto de la suite: nunca llamar a la API real
(yfinance). Detalle importante y no obvio de cómo funciona `AppTest`:
re-ejecuta el script COMPLETO desde cero en cada `.run()` (imita de
verdad el modelo de rerun de Streamlit), así que parchear
`app.streamlit_app.historical_financials` (una referencia ya
importada, potencialmente obsoleta) NO intercepta nada -- hay que
parchear en el módulo de ORIGEN (`engine.yfinance_provider`), de donde
el script vuelve a importar en cada ejecución. Confirmado
experimentalmente antes de escribir esta suite (parchear a nivel de
`app.streamlit_app` deja pasar silenciosamente llamadas reales a la
API con el caché en disco de sesiones anteriores).

Estos tests fijan como regresión automática lo que hasta ahora solo se
había verificado a mano con capturas de pantalla de Playwright en cada
sesión: I3 (ticker inválido), M5 (divisa no USD), y el manejo de
errores en modo "universo cacheado" (sesión 16, continuación) --
exactamente el tipo de bug que una verificación manual puede dejar
pasar en un cambio futuro sin que nadie se entere.
"""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

@pytest.fixture(autouse=True)
def _clear_streamlit_cache():
    """@st.cache_data está diseñado para sobrevivir reruns de Streamlit
    dentro del mismo proceso -- sin esto, un test anterior que cargó con
    éxito el mismo universo de tickers deja el resultado en caché, y los
    tests de fallo de API de más abajo nunca vuelven a invocar la
    función parcheada (síntoma real encontrado escribiendo esta suite:
    los tests de error pasaban a devolver 0 errores en cuanto se movían
    después de un test exitoso con los mismos tickers)."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()

# AppTest.from_file resuelve rutas relativas contra el archivo que llama
# (este test), no contra el directorio de trabajo -- se usa una ruta
# absoluta para que funcione sin importar desde dónde se invoque pytest.
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")

# Revenue determinista por símbolo (nunca aleatorio/hash real -- tiene
# que ser reproducible entre ejecuciones), para que el WACC vía
# comparables tenga variación real entre peers, no un universo idéntico
# de forma artificial.
_REVENUE_BY_SYMBOL = {"AMZN": 100.0, "MSFT": 120.0, "GOOGL": 140.0, "META": 160.0, "AAPL": 180.0,
                       "KO": 90.0, "PG": 110.0, "JNJ": 130.0}


def _fake_history(revenue_last: float = 100.0) -> pd.DataFrame:
    revenue = [revenue_last / 1.1, revenue_last]
    return pd.DataFrame({
        "fiscal_year": [2022, 2023],
        "revenue": revenue,
        "ebit": [r * 0.20 for r in revenue],
        "d_and_a": [r * 0.05 for r in revenue],
        "capex": [r * 0.08 for r in revenue],
        "change_in_nwc": [None, revenue[1] * 0.02],
        "tax_rate": [0.25, 0.25],
        "interest_expense": [r * 0.01 for r in revenue],
        "total_assets": [r * 2.0 for r in revenue],
        "total_equity": [r * 1.2 for r in revenue],
        "total_debt": [r * 0.4 for r in revenue],
        "cash": [r * 0.3 for r in revenue],
        "current_assets": [r * 0.5 for r in revenue],
        "current_liabilities": [r * 0.25 for r in revenue],
        "ebitda": [r * 0.25 for r in revenue],
        "net_income": [r * 0.15 for r in revenue],
    })


def _fake_snapshot(symbol: str, revenue_last: float = 100.0, currency: str = "USD") -> dict:
    return {
        "symbol": symbol, "sector": "TECHNOLOGY", "industry": "INTERNET RETAIL",
        "market_cap": revenue_last * 10, "shares_outstanding": 10.0,
        "price": revenue_last, "beta": 1.2, "ev_to_ebitda": 15.0, "ev_to_revenue": 4.0,
        "pe_ratio": 20.0, "price_to_sales": 3.0, "price_to_book": 5.0,
        "analyst_target_price": revenue_last * 1.2,
        "cash": revenue_last * 0.3, "total_debt": revenue_last * 0.4,
        "currency": currency,
        "week_52_high": revenue_last * 1.3, "week_52_low": revenue_last * 0.7,
    }


def _fake_yf_historical_financials(ticker):
    symbol = getattr(ticker, "symbol", "TEST")
    return _fake_history(_REVENUE_BY_SYMBOL.get(symbol, 100.0))


def _fake_yf_market_snapshot(ticker):
    symbol = getattr(ticker, "symbol", "TEST")
    return _fake_snapshot(symbol, _REVENUE_BY_SYMBOL.get(symbol, 100.0))


def _fake_get_ticker(symbol):
    class _FakeTicker:
        pass
    t = _FakeTicker()
    t.symbol = symbol
    return t


# Parcheos de base para el camino "universo cacheado" -- se usan en la
# gran mayoría de tests, siempre a nivel del módulo de ORIGEN (nunca
# app.streamlit_app, ver docstring del archivo).
def _base_patches():
    return [
        patch("engine.yfinance_provider.get_ticker", side_effect=_fake_get_ticker),
        patch("engine.yfinance_provider.historical_financials", side_effect=_fake_yf_historical_financials),
        patch("engine.yfinance_provider.market_snapshot", side_effect=_fake_yf_market_snapshot),
        patch("engine.yfinance_provider.treasury_yield_10y", return_value=0.045),
    ]


def _run_app(extra_patches=None, interact=None):
    """extra_patches: parcheos adicionales sobre los de _base_patches().
    interact: callback(at) opcional para simular clics/inputs del
    usuario ANTES de la ejecución final -- se llama dentro del mismo
    `with ExitStack()` que los parcheos, porque cada `.set_value().run()`
    dispara una re-ejecución completa del script (ver docstring del
    archivo): si los parcheos no siguen activos durante esas llamadas
    intermedias, se cuela una llamada real a la API sin querer."""
    with ExitStack() as stack:
        for p in _base_patches() + (extra_patches or []):
            stack.enter_context(p)
        at = AppTest.from_file(APP_PATH, default_timeout=30)
        at.run()
        if interact:
            interact(at)
        return at


# --- Camino feliz por defecto (universo cacheado, yfinance) ----------------

def test_default_state_loads_without_exception():
    at = _run_app()
    assert not at.exception
    assert not at.error
    assert len(at.tabs) == 4
    tab_labels = [t.label for t in at.tabs]
    assert tab_labels == ["Valoración", "Supuestos y expectativas", "Fundamentales", "Memo"]


def test_default_state_shows_key_metrics():
    at = _run_app()
    metric_labels = {m.label for m in at.main.metric}
    assert {"Precio de mercado", "Consenso analistas", "WACC", "Sector"}.issubset(metric_labels)
    price_metric = next(m for m in at.main.metric if m.label == "Precio de mercado")
    assert price_metric.value == "$100.00"  # AMZN en _REVENUE_BY_SYMBOL


def test_analyst_coverage_shown_as_consensus_metric_tooltip():
    """Regresión (sesión 17): la recomendación consenso de analistas se
    muestra como tooltip del metric 'Consenso analistas', no como un
    elemento nuevo que desordene el layout ya establecido."""
    at = _run_app(extra_patches=[
        patch("engine.yfinance_provider.market_snapshot",
              side_effect=lambda ticker: {**_fake_snapshot(getattr(ticker, "symbol", "TEST")),
                                           "analyst_recommendation_key": "buy", "analyst_num_opinions": 12}),
    ])
    consensus_metric = next(m for m in at.main.metric if m.label == "Consenso analistas")
    assert "buy" in consensus_metric.help
    assert "12 analistas" in consensus_metric.help


def test_sensitivity_section_renders_with_five_plotly_charts():
    """Regresión (sesión 17): 'Valoración' tiene 3 gráficos Plotly
    (football field + escenarios + heatmap WACC×g) y 'Supuestos y
    expectativas' 2 más (tendencia histórica + tornado chart de
    sensibilidad) -- 5 en total sobre AMZN con universo cacheado
    (comps_valuation disponible, así que el football field incluye la
    barra de comparables). El histograma de Monte Carlo (Lote C) NO
    cuenta aquí: `_fake_history()` solo trae 2 años, y
    `historical_revenue_growth_stats()` exige al menos 3 -- degrada con
    gracia a un caption ("No se pudo completar la simulación"), no un
    6º gráfico ni una excepción. Ver
    test_monte_carlo_histogram_renders_with_enough_history para el
    caso con datos suficientes."""
    at = _run_app()
    assert not at.exception
    headers = [h.value for h in at.subheader]
    assert "Sensibilidad del precio a cada supuesto" in headers
    assert "Football field: triangulación de métodos" in headers
    assert "Valor terminal: Gordon Growth vs. múltiplo de salida" in headers
    assert "Tendencia histórica" in headers
    assert "Simulación Monte Carlo: distribución de precios" in headers
    assert len(at.get("plotly_chart")) == 5


def test_monte_carlo_histogram_renders_with_enough_history():
    """Con al menos 4 años de histórico (mínimo real para
    historical_revenue_growth_stats con lookback_years=3), la
    simulación de Monte Carlo sí debe completarse y añadir un 6º
    gráfico Plotly (el histograma), con P10 <= P50 <= P90 mostrados.

    Sesión 18/19: el grupo por defecto ("Big Tech / Cloud") va por
    yfinance (ver CACHED_GROUPS en app/streamlit_app.py), única fuente
    de datos del proyecto -- el parcheo va contra
    engine.yfinance_provider, la ruta que el camino feliz por defecto
    ejecuta."""
    def longer_history(symbol):
        revenue_last = _REVENUE_BY_SYMBOL.get(symbol, 100.0)
        revenue = [revenue_last / 1.1**3, revenue_last / 1.1**2, revenue_last / 1.1, revenue_last]
        return pd.DataFrame({
            "fiscal_year": [2020, 2021, 2022, 2023],
            "revenue": revenue,
            "ebit": [r * 0.20 for r in revenue],
            "d_and_a": [r * 0.05 for r in revenue],
            "capex": [r * 0.08 for r in revenue],
            "change_in_nwc": [None] + [r * 0.02 for r in revenue[1:]],
            "tax_rate": [0.25] * 4,
            "interest_expense": [r * 0.01 for r in revenue],
            "total_assets": [r * 2.0 for r in revenue],
            "total_equity": [r * 1.2 for r in revenue],
            "total_debt": [r * 0.4 for r in revenue],
            "cash": [r * 0.3 for r in revenue],
            "current_assets": [r * 0.5 for r in revenue],
            "current_liabilities": [r * 0.25 for r in revenue],
            "ebitda": [r * 0.25 for r in revenue],
            "net_income": [r * 0.15 for r in revenue],
        })

    at = _run_app(
        extra_patches=[patch("engine.yfinance_provider.historical_financials",
                              side_effect=lambda ticker: longer_history(getattr(ticker, "symbol", "TEST")))],
    )
    assert not at.exception
    assert len(at.get("plotly_chart")) == 6
    metric_labels = {m.label for m in at.main.metric}
    assert {"P10", "P50 (mediana)", "P90"}.issubset(metric_labels)


def test_roic_delta_color_reflects_creates_value():
    """Regresión: el delta 'Crea valor'/'No crea valor' del ROIC vs. WACC
    debe pintarse en verde cuando SÍ crea valor (fix de esta sesión --
    antes st.metric no podía inferir el signo de un texto libre sin
    '-' y lo pintaba siempre en verde, incluso para 'No crea valor').
    `.proto.color` es el entero crudo del enum; el repr en texto del
    proto completo sí incluye el nombre simbólico ("GREEN"/"GRAY"), que
    es lo que se comprueba aquí en vez de comparar contra el entero
    (frágil si Streamlit reordena el enum en una versión futura)."""
    at = _run_app()
    roic_metrics = [m for m in at.main.metric if m.label == "ROIC vs. WACC"]
    assert len(roic_metrics) == 1
    assert roic_metrics[0].delta == "Crea valor"
    assert "color: GREEN" in str(roic_metrics[0].proto)


# --- Regresión: manejo de errores en modo "universo cacheado" (sesión 16) --

# Sesión 18: se abandonó Alpha Vantage como fuente automática. Sesión 19:
# se elimina del todo del proyecto (engine/data_provider.py, su test
# dedicado, y el `except AlphaVantageError` correspondiente) -- ambos
# grupos cacheados van por yfinance, sin límite de cuota.


def test_cached_group_mode_with_missing_interest_expense_shows_actionable_error():
    """Regresión (sesión 17, evaluación del grupo Utilities/biotech):
    FIZZ (National Beverage, sin deuda) tiene `interest_expense` vacío
    en TODO su histórico -- antes de este fix, `build_peer_wacc()`
    (modo "universo cacheado", el que usa cualquier visitante por
    defecto) no comprobaba esto y dejaba escapar un IndexError crudo de
    pandas hacia el except genérico -- el usuario veía el texto interno
    de pandas ("single positional indexer is out-of-bounds") en vez de
    un mensaje accionable. El modo "cualquier ticker" ya tenía este
    guard (ver test_arbitrary_ticker_mode más abajo); ahora ambos
    caminos lo comparten. El guard vive en build_peer_wacc(), que no
    depende del proveedor -- sesión 18: parcheo movido a
    engine.yfinance_provider (ruta real del grupo por defecto ahora)."""
    def history_without_interest_expense(ticker):
        symbol = getattr(ticker, "symbol", "TEST")
        df = _fake_yf_historical_financials(ticker)
        if symbol == "AMZN":
            df = df.assign(interest_expense=[None, None])
        return df

    at = _run_app(extra_patches=[
        patch("engine.yfinance_provider.historical_financials", side_effect=history_without_interest_expense),
    ])
    assert not at.exception
    assert len(at.error) >= 1
    assert "gasto financiero" in at.error[0].value
    assert "AMZN" in at.error[0].value


def test_generic_loader_failure_shows_actionable_message_not_a_traceback():
    """Sesión 18: parcheo movido a engine.yfinance_provider (ruta real
    del grupo por defecto, ver nota más arriba)."""
    def raise_network_error(*args, **kwargs):
        raise ConnectionError("network down")

    at = _run_app(extra_patches=[
        patch("engine.yfinance_provider.historical_financials", side_effect=raise_network_error),
    ])
    assert not at.exception
    assert len(at.error) >= 1
    assert "network down" in at.error[0].value


# --- Regresión: modo "cualquier ticker" (auditoría sesión 15, hallazgo I3) -

def _switch_to_arbitrary_ticker(at, symbol: str):
    at.sidebar.radio[0].set_value("Cualquier empresa (símbolo suelto)").run()
    at.sidebar.text_input[0].set_value(symbol).run()


def test_arbitrary_ticker_mode_with_empty_history_shows_actionable_error():
    """Regresión I3: yfinance devolviendo un histórico vacío para un
    ticker inválido no debe crashear la app."""
    at = _run_app(
        extra_patches=[patch("engine.yfinance_provider.historical_financials", return_value=pd.DataFrame())],
        interact=lambda at: _switch_to_arbitrary_ticker(at, "ZZZZINVALID"),
    )
    assert not at.exception
    assert len(at.error) >= 1
    assert "ZZZZINVALID" in at.error[0].value


def test_arbitrary_ticker_mode_warns_proactively_for_incompatible_sector():
    """Regresión sesión 17 (hallazgo I11): bancos/REITs/aseguradoras
    reciben un aviso explícito ANTES de que la valoración probablemente
    falle (I9) o no sea significativa -- no solo un mensaje de error
    reactivo, sino contexto de por qué."""
    def bank_snapshot(ticker):
        symbol = getattr(ticker, "symbol", "TEST")
        return {**_fake_snapshot(symbol), "sector": "Financial Services"}

    at = _run_app(
        extra_patches=[patch("engine.yfinance_provider.market_snapshot", side_effect=bank_snapshot)],
        interact=lambda at: _switch_to_arbitrary_ticker(at, "FAKEBANK"),
    )
    assert not at.exception
    warnings_shown = [w.value for w in at.warning]
    assert any("Financial Services" in w and "REITs" in w for w in warnings_shown)


def test_arbitrary_ticker_mode_with_missing_capex_shows_actionable_error():
    """Regresión sesión 17: prueba de estrés con tickers reales (JPM,
    banco sin CapEx/EBIT tradicionales; PLD, REIT sin CapEx; XOM, sin
    D&A en yfinance) encontró un IndexError sin capturar en la sección
    de cómputo compartida por ambos modos -- fuera de cualquier
    try/except existente. Reproduce con CapEx=None en todos los años."""
    def history_without_capex(ticker):
        symbol = getattr(ticker, "symbol", "TEST")
        df = _fake_history(_REVENUE_BY_SYMBOL.get(symbol, 100.0))
        df["capex"] = None
        return df

    at = _run_app(
        extra_patches=[patch("engine.yfinance_provider.historical_financials", side_effect=history_without_capex)],
        interact=lambda at: _switch_to_arbitrary_ticker(at, "JPM"),
    )
    assert not at.exception
    assert len(at.error) >= 1
    assert "CapEx" in at.error[0].value


def test_arbitrary_ticker_mode_with_missing_beta_shows_actionable_error():
    """Regresión I3: sin beta (frecuente en small caps/IPOs) no debe
    lanzar un TypeError sin capturar."""
    def snapshot_without_beta(ticker):
        symbol = getattr(ticker, "symbol", "TEST")
        return {**_fake_snapshot(symbol), "beta": None}

    at = _run_app(
        extra_patches=[patch("engine.yfinance_provider.market_snapshot", side_effect=snapshot_without_beta)],
        interact=lambda at: _switch_to_arbitrary_ticker(at, "NEWCO"),
    )
    assert not at.exception
    assert len(at.error) >= 1
    assert "beta" in at.error[0].value.lower()


# --- Regresión: verificación de divisa (auditoría sesión 15/16, hallazgo M5) -

def test_non_usd_currency_blocks_valuation_with_clear_message():
    """Regresión M5: un ticker que reporta en una divisa distinta de USD
    debe bloquear con un mensaje claro, nunca calcular un WACC/precio
    mezclando divisas en silencio."""
    def jpy_snapshot(ticker):
        symbol = getattr(ticker, "symbol", "TEST")
        return _fake_snapshot(symbol, currency="JPY")

    at = _run_app(
        extra_patches=[patch("engine.yfinance_provider.market_snapshot", side_effect=jpy_snapshot)],
        interact=lambda at: _switch_to_arbitrary_ticker(at, "TM"),
    )
    assert not at.exception
    assert len(at.error) >= 1
    assert "JPY" in at.error[0].value


def test_usd_currency_does_not_block_valuation():
    """Contraprueba: una divisa explícitamente USD no debe bloquear nada
    -- confirma que el guard de M5 no es una falsa alarma para el caso
    normal."""
    at = _run_app(interact=lambda at: _switch_to_arbitrary_ticker(at, "GOODCO"))
    assert not at.exception
    assert not at.error
