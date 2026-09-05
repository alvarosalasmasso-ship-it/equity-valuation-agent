# Estado del proyecto — Agente de valoración DCF

> Documento vivo. Actualízalo al final de cada sesión de trabajo con lo
> avanzado, las decisiones tomadas y el siguiente paso concreto. Es la
> primera lectura al retomar el proyecto.

**Última actualización:** 2026-09-05

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

## 3. Avance por fase (según plan del blueprint, sección 3)

| Fase | Estado | Notas |
|---|---|---|
| 0 — Alcance | ✅ Hecho | Idea #1 elegida (agente de valoración), sentiment como extensión Fase 2 |
| 1 — Motor de datos (Alpha Vantage + yfinance) | ⬜ No empezado | Alpha Vantage está disponible como MCP en esta sesión de Claude Code para explorar/probar, pero el agente desplegado necesita su propia integración por API key (`requests` + `.env`) |
| 2 — Motor de valoración (`engine/valuation.py`) | ✅ Hecho | Ver detalle abajo |
| 3 — Motor de ratios y comps (`ratios.py`, `comps.py`) | ⬜ No empezado | Necesario para: (a) ratios de rentabilidad/apalancamiento, (b) tabla de comparables, (c) múltiplo EV/EBITDA para el modo blended del valor terminal |
| 4 — Tests unitarios | ✅ Hecho (para Fase 2) | `tests/test_valuation.py`, 11 tests, validados contra valores reales del Excel |
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

## 4. Estado técnico del entorno

- Python 3.12.10 disponible vía `python` (⚠️ no `python3`) en el sistema.
- No había entorno virtual — se instalaron `openpyxl`, `pandas`, `pytest`
  con `pip install` a nivel de usuario/sistema. **Pendiente:** crear
  `venv` propio del proyecto antes de seguir añadiendo dependencias
  (yfinance, anthropic, streamlit) para no ensuciar el Python global.
- Repositorio git local inicializado, primer commit hecho (2026-09-05).
  Sin remoto todavía — el push a GitHub es la Fase 8 del plan.
- Estructura de carpetas creada: `engine/`, `ai/prompts/`, `app/`,
  `tests/`, `docs/`, `data/`.

## 5. Próximo paso inmediato

**Fase 1 — Motor de datos.** Construir el script que llama a Alpha
Vantage (`INCOME_STATEMENT`, `BALANCE_SHEET`, `CASH_FLOW`, `EARNINGS`,
`COMPANY_OVERVIEW`) + yfinance (precio, beta, market cap) y normaliza
todo en un DataFrame limpio, con cache local para no agotar el rate
limit gratuito de Alpha Vantage (25 peticiones/día).

Con un único ticker piloto primero (el blueprint sugiere no tocar
Streamlit ni el LLM hasta validar el motor de datos + valoración de
punta a punta con datos reales, no solo con el caso Amazon del Excel).

**Requiere de ti antes de continuar:**
- **API key de Alpha Vantage para el script standalone.** Tienes Alpha
  Vantage conectado como *connector* de Claude (MCP) — eso permite que
  YO llame a sus datos dentro de esta sesión de Claude Code, pero es una
  integración distinta y no expone una API key reutilizable en tu propio
  código Python. El agente desplegado (Streamlit, fuera de Claude Code)
  necesita su propia key gratuita: sacarla en
  https://www.alphavantage.co/support/#api-key (formulario de 1 campo,
  key al instante) y guardarla en un `.env` local (no versionado). Puedo
  usar el connector mientras tanto para explorar/probar datos durante el
  desarrollo.
- Decidir el universo piloto de 3-5 tickers del mismo sector (Fase 0 del
  plan pide esto para poder comparar comps).
