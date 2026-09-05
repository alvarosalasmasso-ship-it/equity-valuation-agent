# Estado del proyecto — Agente de valoración DCF

> Documento vivo. Actualízalo al final de cada sesión de trabajo con lo
> avanzado, las decisiones tomadas y el siguiente paso concreto. Es la
> primera lectura al retomar el proyecto.

**Última actualización:** 2026-09-05 (sesión 4)

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
  no un beta de mercado tomado directamente. Replicado exacto, y ahora
  orquestado de punta a punta para cualquier ticker en `wacc_builder.py`.
- **Proyección de márgenes/CapEx: reversión a la media, no continuación
  de tendencia.** Cada driver (margen EBIT, D&A, CapEx, ΔNWC) hace fade
  lineal desde el dato real del último año hasta el promedio histórico
  de varios años. Es una postura de modelado deliberada (conservadora,
  no extrapola el momentum actual a perpetuidad) y explícitamente
  sobreescribible — ver sección 3 para la justificación completa y sus
  implicaciones al validar contra el mercado.
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
| 1 — Motor de datos (Alpha Vantage + yfinance) | 🟡 En marcha | Falta: yfinance, y descargar histórico de los otros 4 tickers piloto |
| 2 — Motor de valoración (`engine/valuation.py`) | ✅ Hecho | Ver detalle abajo |
| 3 — Motor de proyección, ratios, comps y WACC | ✅ Hecho | `engine/projections.py` (con fade en todos los drivers), `engine/ratios.py`, `engine/comps.py`, `engine/wacc_builder.py`, matriz de sensibilidad en `valuation.py`. Ver detalle abajo |
| 4 — Tests unitarios | ✅ Hecho (Fases 1-3) | 53 tests, todos en verde (`./.venv/Scripts/python.exe -m pytest tests/ -v`) |
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

Todos los tests pasan contra valores reales extraídos del Excel (no
inventados). Matriz de sensibilidad 2D (WACC × g) añadida en Fase 3
(`sensitivity_matrix`).

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

### Detalle Fase 3 — `engine/projections.py`, `engine/ratios.py`, `engine/comps.py`, `engine/wacc_builder.py`, `sensitivity_matrix`

**Motor de proyección** (`projections.py`, reescrito esta sesión):
mecanismo único de **fade lineal** (`FadeAssumption(start, end)`)
aplicado a TODOS los drivers (crecimiento de ingresos, margen EBIT, D&A,
CapEx, ΔNWC), no solo al crecimiento como en la versión anterior. Por
defecto: año 1 = dato real del último ejercicio fiscal, año N = promedio
histórico de `lookback_years` años (crecimiento de ingresos es la
excepción: año 1 = CAGR reciente, año N = tasa terminal dada por el
usuario). Sigue siendo 100% sobreescribible.

**WACC vía comparables** (`wacc_builder.py`, nuevo): orquesta
`unlever_beta` -> media de industria -> `relever_beta` -> `cost_of_equity`
-> `cost_of_debt` -> `wacc` para un ticker y un set de peers arbitrarios,
reutilizando (no duplicando) las funciones ya validadas de
`valuation.py`. Validado exacto contra el mismo caso AMZN/Excel que
`test_valuation.py`.

**Matriz de sensibilidad** (`sensitivity_matrix` en `valuation.py`,
nuevo): réplica de la Data Table WACC×g del Excel. Corre `run_dcf` en un
grid vía `dataclasses.replace`, sin duplicar matemática. Pendiente
desde la Fase 2, cerrado esta sesión.

**Comps** (`comps.py`): `build_comps_table()` agrega snapshots de varios
tickers en una tabla indexada por símbolo; `peer_average_multiple()` da
la media/mediana de un múltiplo entre peers (excluyendo opcionalmente el
ticker objetivo).

**Ratios** (`ratios.py`): ROE por Dupont, ROIC vs. WACC (creación de
valor), Debt/EBITDA, cobertura de intereses, current ratio.

**Validación de punta a punta con AMZN, repetida con el motor mejorado**
(fade en todos los drivers + WACC vía comparables en vez de beta crudo):
precio implícito subió de ~$49 (sesión 3, bug de ventana + sin fade) a
~$85 (lookback=3, con el bug corregido y el fade activo), frente a
~$258 de mercado y ~$328 de consenso. **Diagnóstico ya no es "hay bugs y
supuestos naive"**: el motor y los datos están correctos y validados; la
brecha restante es una diferencia de **filosofía de modelado** — nuestro
motor por defecto asume reversión a la media (el margen converge a su
promedio histórico), mientras que el mercado paga por continuación de
tendencia (apuesta a que Amazon sigue expandiendo margen más allá de su
nivel actual). Ninguna postura es "la correcta" de forma absoluta; por
eso `ProjectionAssumptions` deja `end` de cada driver completamente
editable — un analista con tesis alcista lo fija a mano. Diagnóstico
completo y honesto en `docs/METHODOLOGY.md` sección 5. **No se ha
tocado el valor por defecto para acercarlo al precio de mercado** —
sería sobreajustar a un caso conocido.

`historical_financials` se amplió con los campos que estas piezas
necesitan (ebitda, interest_expense, total_assets, total_equity,
total_debt, cash, current_assets, current_liabilities) y `market_snapshot`
con ev_to_revenue, pe_ratio, price_to_sales, price_to_book.

**Nota sobre el WACC del pipeline de validación:** los comparables
(AAPL/MSFT/GOOGL) usados en la prueba de punta a punta son las
constantes congeladas del Excel (una fecha pasada), mientras que AMZN
usa datos frescos de hoy — desalineación temporal real, señalada a
propósito en vez de mezclarse en silencio. Para producción, `build_wacc`
debería recibir peers con datos de la misma fecha que el ticker
objetivo (pendiente: pull en vivo de balances de peers vía Alpha Vantage,
cuesta cuota de API).

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

El pipeline funciona de punta a punta (datos -> proyección -> WACC ->
valoración) para un ticker arbitrario, con cada pieza validada por
separado contra el Excel. La brecha restante frente al mercado en AMZN
es una diferencia de filosofía de modelado ya documentada (sección 3),
no una lista de bugs pendientes. Opciones razonables para la próxima
sesión, de más a menos prioritaria:

1. **Fase 7 (validación sistemática):** correr el pipeline completo
   sobre los 5 tickers piloto y comparar contra consenso de analistas
   (`AnalystTargetPrice`, ya disponible en `market_snapshot`) — da una
   cifra real de desviación media para el CV, y probablemente muestre
   que el motor se comporta mejor en compañías maduras/estables que en
   historias de crecimiento como AMZN (hipótesis a confirmar con datos,
   no a asumir).
2. Completar Fase 1: descargar histórico de los otros 4 tickers piloto
   (MSFT, GOOGL, META, AAPL) — con cuidado de la cuota diaria (quedan
   ~15-20 peticiones hoy) — y usarlos también como comparables reales en
   `wacc_builder.py` en vez de las constantes congeladas del Excel
   (resuelve la desalineación temporal señalada en la sección 3).
3. Opcional: exponer una tesis "alcista" vs. "conservadora" en la futura
   interfaz (dos `ProjectionAssumptions` predefinidos, no solo el
   derivado por defecto) — comunica mejor que el número no es una
   verdad única, es una función de supuestos explícitos.

**No recomendado todavía:** Streamlit ni la capa generativa (Fases 5-6).
El blueprint es explícito en esto — construir la interfaz antes de medir
la Fase 7 enseñaría números sin saber todavía si son buenos o malos en
la práctica.
