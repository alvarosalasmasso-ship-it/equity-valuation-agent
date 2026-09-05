# Estado del proyecto — Agente de valoración DCF

> Documento vivo. Actualízalo al final de cada sesión de trabajo con lo
> avanzado, las decisiones tomadas y el siguiente paso concreto. Es la
> primera lectura al retomar el proyecto.

**Última actualización:** 2026-09-05 (sesión 2)

---

## 1. Qué es esto

Dos insumos de partida:
1. `blueprint_agente_valoracion_dcf.md` — diseño del proyecto de portfolio
   (agente de valoración con IA generativa desacoplada del cálculo).
2. `Advanced DCF.xlsx` — modelo DCF profesional real (Amazon), elaborado
   por un ex-banquero de M&A/ECM de JP Morgan. **Es la fuente de verdad
   metodológica**: el motor Python traduce sus fórmulas exactas, no
   reinventa un DCF genérico.

Objetivo final: agente que, dado un ticker, extrae sus estados
financieros, corre un DCF determinista y genera un Investment Memo con
IA (que solo redacta, nunca calcula).

## 2. Decisiones de arquitectura tomadas

- **El LLM nunca calcula, solo redacta.** El motor de valoración
  (`engine/`) es Python puro, auditable, sin llamadas a IA.
- **El motor NO segmenta por línea de negocio.** El Excel real hace un
  DCF por suma de partes (North America / International / AWS, cada uno
  con su propio DCF). El agente generalizado no tiene ese desglose para
  un ticker arbitrario, así que aplica la misma lógica de fórmulas a
  nivel de empresa consolidada. Verificado que esto no pierde precisión
  cuando el peso Gordon/múltiplo y el múltiplo EV/EBITDA son uniformes
  entre segmentos (ver `docs/METHODOLOGY.md` sección 2 — reproduce el
  precio real del modelo, $216.41, de forma exacta).
- **Convención de descuento:** stub period + mid-year convention (no
  periodos enteros). Replicada exacta en `discount_periods()`.
- **Valor terminal:** Gordon Growth puro por defecto (`gordon_weight=1.0`),
  con modo opcional de blend Gordon + múltiplo de salida EV/EBITDA
  (requiere comparables, pendiente de Fase 3).
- **WACC:** CAPM con beta reconstruida desde comparables (unlever/relever),
  no un beta de mercado tomado directamente. Replicado exacto.
- **Acciones diluidas:** Treasury Stock Method completo (tramos de
  opciones in-the-money + convertibles), no solo shares outstanding.
- **Universo piloto elegido: Big Tech / Cloud — AMZN, MSFT, GOOGL, META,
  AAPL.** Decisión delegada a Claude. Motivo: el propio Excel de
  referencia ya trae betas/market caps de AAPL, MSFT y GOOGL (permite
  contraste directo), son tickers muy líquidos con cobertura de consenso
  fiable para la Fase 7, y son comparables entre sí para la tabla de comps.
- **`Advanced DCF.xlsx` se versiona en el repo.** El autor lo comparte de
  libre uso en su canal de YouTube. Se usa como estándar profesional de
  referencia (cómo debe construirse un DCF riguroso), no como plantilla a
  copiar literal — el motor propio generaliza la lógica a cualquier ticker.

## 3. Avance por fase (según plan del blueprint, sección 3)

| Fase | Estado | Notas |
|---|---|---|
| 0 — Alcance | ✅ Hecho | Idea #1 elegida (agente de valoración), sentiment como extensión Fase 2 |
| 1 — Motor de datos (Alpha Vantage + yfinance) | 🟡 En marcha | Ver detalle abajo. Falta: yfinance, y descargar histórico de los otros 4 tickers piloto |
| 2 — Motor de valoración (`engine/valuation.py`) | ✅ Hecho | Ver detalle abajo |
| 3 — Motor de ratios y comps (`ratios.py`, `comps.py`) | ⬜ No empezado | Necesario para: (a) ratios de rentabilidad/apalancamiento, (b) tabla de comparables, (c) múltiplo EV/EBITDA para el modo blended del valor terminal |
| 4 — Tests unitarios | ✅ Hecho (Fase 1 y 2) | `tests/test_valuation.py` (11) + `tests/test_data_provider.py` (6), 17 tests, todos en verde |
| 5 — Capa generativa (Investment Memo) | ⬜ No empezado | |
| 6 — Interfaz Streamlit | ⬜ No empezado | |
| 7 — Validación vs consenso de analistas | ⬜ No empezado | |
| 8 — Despliegue | ⬜ No empezado | `Advanced DCF.xlsx` SÍ se versiona: el autor lo comparte de libre uso en su canal de YouTube. Se usa como estándar profesional de referencia, no como plantilla a copiar literal — el motor propio generaliza su lógica a cualquier ticker (ver sección 2). |
| 9 (extensión) — Sentiment earnings calls | ⬜ No empezado | |

### Detalle Fase 2 — `engine/valuation.py`

Funciones implementadas y testeadas (mapeo completo en `docs/METHODOLOGY.md`):

- `unlever_beta` / `relever_beta` — reconstrucción de beta desde comparables
- `cost_of_equity` (CAPM), `cost_of_debt`, `wacc`
- `treasury_stock_method`, `diluted_shares_outstanding` — TSM completo
- `unlevered_fcf` — FCFF = EBIT×(1-t) + D&A - CapEx - ΔNWC
- `discount_periods`, `pv_of_cash_flows` — stub + mid-year convention
- `gordon_growth_terminal_value`, `exit_multiple_terminal_value`, `blended_terminal_value`
- `run_dcf(DCFInputs) -> DCFResult` — pipeline completo end-to-end

Todos los tests pasan (`python -m pytest tests/ -v` → 11 passed) contra
valores reales extraídos del Excel (no inventados).

**No implementado todavía dentro del motor:** matriz de sensibilidad 2D
(WACC × g) — el Excel la genera con Data Tables; en Python será una
función que corre `run_dcf` en un grid de combinaciones. Fácil de añadir
en Fase 3/6, no bloquea nada.

### Detalle Fase 1 — `engine/data_provider.py`

- `AlphaVantageClient`: llama a `INCOME_STATEMENT`, `BALANCE_SHEET`,
  `CASH_FLOW`, `EARNINGS`, `OVERVIEW`. Cachea cada respuesta cruda en
  `data/cache/alpha_vantage/{TICKER}_{FUNCTION}.json` (TTL 24h) y espacia
  las peticiones en vivo 15s entre sí (el free tier además de 25/día
  tiene un límite de ráfaga ~1 req/seg — lo pisamos una vez en pruebas,
  ya corregido).
- `historical_financials(client, symbol) -> DataFrame`: combina income
  statement + balance sheet + cash flow anuales en una tabla con
  `revenue, ebit, tax_rate, d_and_a, capex, change_in_nwc, net_income`
  — exactamente los inputs que pide `DCFInputs` en `valuation.py`.
  ΔNWC se calcula como `(Activo corriente - Caja) - (Pasivo corriente -
  Deuda corto)`, diferencia interanual.
- `market_snapshot(client, symbol) -> dict`: precio implícito, beta,
  market cap, EV/EBITDA de mercado (múltiplo de salida listo para el
  modo blended del valor terminal), caja y deuda del balance más reciente,
  precio objetivo de consenso de analistas (`AnalystTargetPrice`, clave
  para la Fase 7 de validación).
- **Validado con una llamada real a la API** (no solo con fixtures):
  histórico de AMZN 2018-2025 descargado y contrastado contra el propio
  Excel — 2021 revenue $469.822B y EBIT $24.879B coinciden exactamente
  con `Consolidated!C12`/`C15` (en millones). Confirma que Alpha Vantage
  y el modelo de referencia parten de los mismos estados financieros.
- Tests (`tests/test_data_provider.py`, 6 tests) usan fixtures simuladas
  (mismo formato real, verificado a mano) — no gastan cuota de API ni
  dependen de red.
- **Cuota de Alpha Vantage usada hoy:** ~4-5 de las 25 peticiones/día
  gratuitas, todas en AMZN (ya cacheado 24h). No se ha llamado todavía a
  AAPL/MSFT/GOOGL/META — pendiente para no agotar la cuota en una sola
  sesión.
- **Pendiente dentro de Fase 1:** integrar yfinance como fuente
  alternativa de precio/beta/market cap (blueprint la prefiere para datos
  de mercado de alta frecuencia, ya que no tiene el límite de 25/día de
  Alpha Vantage) — de momento `market_snapshot` cubre lo mismo vía
  `COMPANY_OVERVIEW`, así que no bloquea nada, es una mejora de fiabilidad
  futura, no un requisito.

## 4. Estado técnico del entorno

- Python 3.12.10 disponible vía `python` (⚠️ no `python3`) en el sistema.
- **Entorno virtual del proyecto creado en `.venv/`** (gitignored).
  Todas las dependencias (`openpyxl`, `pandas`, `numpy`, `requests`,
  `python-dotenv`, `pytest`, `yfinance`) instaladas ahí, no en el Python
  global. Para ejecutar cualquier cosa del proyecto:
  `./.venv/Scripts/python.exe -m pytest tests/ -v` (o activar el venv
  primero).
- `.env` creado en la raíz con `ALPHA_VANTAGE_API_KEY` (gitignored,
  confirmado con `git check-ignore`).
- Repositorio git local inicializado, primer commit hecho (2026-09-05).
  Identidad configurada solo local a este repo (Álvaro Salas Massó /
  alvarosalasmasso@gmail.com), no en la config global de git. Sin remoto
  todavía — el push a GitHub es la Fase 8 del plan.
- Estructura de carpetas: `engine/` (`valuation.py`, `data_provider.py`),
  `ai/prompts/`, `app/`, `tests/`, `docs/`, `data/cache/` (gitignored).

## 5. Próximo paso inmediato

**Seguir Fase 1 / arrancar Fase 3.** Dos caminos razonables, a decidir
en la próxima sesión:

1. Completar Fase 1: descargar histórico de los otros 4 tickers piloto
   (MSFT, GOOGL, META, AAPL) — con cuidado de la cuota diaria — y
   confirmar que `historical_financials` funciona igual de bien con ellos
   (distintos formatos de balance, p.ej. bancos o empresas con deuda
   compleja podrían romper supuestos del cálculo de NWC).
2. Empezar Fase 3 (`engine/comps.py`): tabla de comparables (múltiplos
   EV/EBITDA, EV/Sales, P/E) entre los 5 tickers piloto — esto es lo que
   falta para poder activar el modo "blended terminal value" del motor
   con datos reales en vez del caso Amazon del Excel.

Nota: todavía no existe una capa de **proyección** (llevar los
históricos de Alpha Vantage a los 5-10 años futuros que pide
`DCFInputs`). El Excel lo resuelve con el "Operating Model" (supuestos
de % crecimiento / % sobre ventas por segmento) — no está mapeado en
`docs/METHODOLOGY.md` todavía porque decidimos priorizar primero validar
el motor de valoración y el motor de datos por separado. Es el siguiente
bloque que falta para cerrar el pipeline de punta a punta con un ticker
que no sea Amazon.
