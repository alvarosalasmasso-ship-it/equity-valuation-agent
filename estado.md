# Estado del proyecto — Agente de valoración DCF

> Documento vivo. Actualízalo al final de cada sesión de trabajo con lo
> avanzado, las decisiones tomadas y el siguiente paso concreto. Es la
> primera lectura al retomar el proyecto.

**Última actualización:** 2026-09-06 (sesión 16)

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
| 1 — Motor de datos (Alpha Vantage + yfinance) | 🟡 Suficiente para seguir | Los 5 tickers piloto descargados y cacheados. Falta solo yfinance (no bloquea nada, ver detalle) |
| 2 — Motor de valoración (`engine/valuation.py`) | ✅ Hecho | Ver detalle abajo |
| 3 — Motor de proyección, ratios, comps y WACC | ✅ Hecho | `engine/projections.py` (con fade en todos los drivers), `engine/ratios.py`, `engine/comps.py`, `engine/wacc_builder.py`, matriz de sensibilidad en `valuation.py`. Ver detalle abajo |
| 4 — Tests unitarios | ✅ Hecho (Fases 1-3 y 7) | 60 tests, todos en verde (`./.venv/Scripts/python.exe -m pytest tests/ -v`) |
| 5 — Capa generativa (Investment Memo) | ⬜ No empezado | |
| 6 — Interfaz Streamlit | ⬜ No empezado | |
| 7 — Validación vs consenso de analistas | ✅ Hecho | `engine/validation.py`. Resultado real sobre los 5 tickers piloto: desviación media absoluta 54.8% vs. mercado, 53.4% vs. consenso — ver detalle abajo, causa raíz identificada (no bugs) |
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
- **Cuota de Alpha Vantage:** los 5 tickers piloto (AMZN, MSFT, GOOGL,
  META, AAPL) están descargados y cacheados en
  `data/cache/alpha_vantage/` (TTL 24h) — ~20 de las 25 peticiones/día
  gratuitas usadas en total entre las dos sesiones. Sin incidentes de
  rate-limit en la segunda tanda (pacing de 15s funcionó bien).
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

`historical_financials` se amplió con los campos que estas piezas
necesitan (ebitda, interest_expense, total_assets, total_equity,
total_debt, cash, current_assets, current_liabilities) y `market_snapshot`
con ev_to_revenue, pe_ratio, price_to_sales, price_to_book.

### Detalle Fase 7 — `engine/validation.py`, resultado real sobre el universo piloto

`value_ticker()` corre el pipeline completo para un ticker usando el
RESTO de un universo como sus comparables de WACC (mismo momento
temporal para todos — resuelve la desalineación de la sesión anterior,
que usaba constantes congeladas del Excel como peers). `validate_universe()`
lo repite para cada ticker; `summarize_deviation()` agrega desviación
media/mediana absoluta vs. mercado y consenso. Tests con universo
sintético de 3 tickers, sin red.

**Resultado real, corrido sobre los 5 tickers piloto** (AMZN, MSFT,
GOOGL, META, AAPL descargados y cacheados esta sesión):

| Ticker | WACC | Implícito | Mercado | Consenso | Desv. mercado | Desv. consenso |
|---|---|---|---|---|---|---|
| AMZN | 8.27% | $84.82 | $258.51 | $328.17 | -67.2% | -74.2% |
| MSFT | 8.78% | $295.80 | $499.70 | $572.92 | -40.8% | -48.4% |
| GOOGL | 8.68% | $270.02 | $705.51 | $428.07 | -61.7% | -36.9% |
| META | 8.47% | $365.03 | $712.53 | $754.77 | -48.8% | -51.6% |
| AAPL | 8.84% | $142.74 | $319.97 | $323.86 | -55.4% | -55.9% |

**Desviación media absoluta: 54.8% vs. mercado, 53.4% vs. consenso.**

**⚠️ Diagnóstico investigado a fondo, no asumido:** que las 5 compañías
(independientes entre sí) salgan infravaloradas en magnitud similar es
una señal de causa compartida, no de 5 historias sueltas. Se hizo un
desglose completo del DCF de MSFT y se verificó contra el JSON crudo de
Alpha Vantage: **CapEx = 34.9% de ventas en el último ejercicio (frente
a D&A = 11.6%)** — CapEx real, reportado, no un artefacto de cálculo.
Es el supercycle de inversión en infraestructura de IA que estas 5
compañías están ejecutando ahora mismo. Con un CapEx de esa magnitud, un
UFCF conservador (que no asume que ese CapEx ya se traduce en EBIT
futuro no verificado) queda estructuralmente deprimido — mismo mecanismo
identificado para AMZN en la sesión anterior, ahora confirmado
sistemático en 5 compañías, no anecdótico en una.

**Esto NO se interpreta como "el modelo está mal".** Es el resultado
correcto de una herramienta rigurosa aplicada a un momento de mercado
donde el consenso paga una prima considerable por crecimiento futuro no
garantizado. El valor para un entrevistador no es "reproduce el precio
de mercado" (trivial, cualquier múltiplo lo hace por construcción) sino
"cuantifica cuánta prima de crecimiento no verificado está pagando el
mercado, y explica el mecanismo concreto" (CapEx >> D&A). Diagnóstico
completo en `docs/METHODOLOGY.md` sección 7.

**Cifra real para el CV** (blueprint sección 4, viñeta 2, ya no un
placeholder): *"...validado frente a 5 empresas del sector Big
Tech/Cloud con una desviación media del 53% frente al consenso de
mercado, explicada por un motor conservador (reversión a la media)
frente al actual supercycle de CapEx en IA no descontado de forma
determinista."*

### Contraprueba con una empresa madura (Coca-Cola) — responde "¿es útil esta herramienta?"

El usuario preguntó directamente si la herramienta sirve para valorar
empresas, dado el 53% de desviación de la Fase 7. Respuesta corta: un
DCF no está pensado para reproducir el precio de mercado (si lo hiciera,
no aportaría información — el valor de un DCF es dar una estimación
*independiente* que tú comparas con el precio). La pregunta real es si
el motor se comporta como debería: fiable en negocios estables, menos
fiable por defecto en hiper-crecimiento con reinversión masiva. Se
comprobó con datos, no solo con argumento:

**Coca-Cola (KO)** — CapEx = 4.4% de ventas frente a D&A = 2.2% (vs.
MSFT: CapEx 34.9% frente a D&A 11.6%), el contraste casi perfecto:

| Ticker | Lookback | Implícito | Mercado | Consenso |
|---|---|---|---|---|
| KO | 3 años | $83.13 | $88.07 (-5.6%) | $94.70 (-12.2%) |
| KO | 5 años | $110.67 | $88.07 (+25.7%) | $94.70 (+16.9%) |

**Desviación de un dígito a ~20%, un orden de magnitud por debajo del
41%-67% de Big Tech.** Confirma con evidencia (no solo con teoría) que
el motor conservador es fiable donde la teoría predice que debe serlo.
Diagnóstico completo en `docs/METHODOLOGY.md` sección 7 ("Contraprueba:
¿el motor funciona mejor en empresas maduras?").

**⚠️ Cuota de Alpha Vantage agotada durante esta prueba.** Se planeaban
3 empresas maduras (KO + Procter & Gamble + Johnson & Johnson) pero la
API devolvió el límite diario de 25 peticiones/día al intentar P&G
(fallo limpio, sin caché corrupta — simplemente no se descargó nada de
P&G/JNJ). Solo hay 1 dato, no 3. Además, el WACC de KO usado aquí es
**simplificado** (beta propio directo vía `cost_of_equity`/`wacc`, no
`wacc_builder.py` con comparables) porque no había peers del mismo
sector (consumo defensivo) descargados — no invalida la conclusión sobre
el motor de proyección, que es lo que esta prueba mide, pero sí significa
que el WACC de KO es menos riguroso que el de los 5 tickers de Big Tech.

**Pendiente para la próxima sesión (cuando la cuota se resetee):**
completar la contraprueba con Procter & Gamble y Johnson & Johnson, y
reconstruir el WACC de KO vía comparables reales del sector para
igualar el rigor con el que se trató a Big Tech.

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
  alvarosalasmasso@gmail.com), no en la config global de git. **Remoto
  configurado (sesión 16, continuación):** `origin` ->
  https://github.com/alvarosalasmasso-ship-it/equity-valuation-agent
  (público). `gh` CLI instalado y autenticado en este entorno
  (`gh auth setup-git` configurado como credential helper de git).
- Estructura de carpetas: `engine/` (`valuation.py`, `data_provider.py`),
  `ai/prompts/`, `app/`, `tests/`, `docs/`, `data/cache/` (gitignored).

### Detalle sesión 7 — `engine/yfinance_provider.py` y contraprueba de empresas maduras completada

El usuario pidió seguir desarrollando sin usar Alpha Vantage (cuota
agotada). Se construyó **`engine/yfinance_provider.py`**: mismo esquema
exacto de columnas/claves que `data_provider.py` (intercambiable en
`projections.py`, `wacc_builder.py`, `validation.py` sin tocarlos).
yfinance no tiene límite de peticiones/día (a cambio de solo ~4 años de
histórico anual, no 15-20). Validado con datos reales: mismo precio y
beta que Alpha Vantage para KO. 7 tests con un doble de prueba (sin red).

Esto permitió **terminar la contraprueba de empresas maduras** que había
quedado a medias (KO + Procter & Gamble + Johnson & Johnson, WACC vía
comparables real entre las tres):

| Ticker | WACC | Implícito | Mercado | Consenso | Desv. mercado |
|---|---|---|---|---|---|
| KO | 4.95% | $90.55 | $88.07 | $94.70 | **+2.8%** |
| PG | 4.80% | $271.16 | $146.44 | $160.61 | **+85.2%** |
| JNJ | 5.08% | $418.26 | $275.23 | $275.64 | **+52.0%** |

**Resultado sorprendente y más matizado que la hipótesis de la sesión
anterior:** KO confirma que el motor puede ser muy preciso, pero PG y
JNJ salen SOBREvaloradas — lo contrario que Big Tech. Investigado a
fondo (desglose completo, no asumido): **no es un problema de márgenes**
(PG tiene márgenes planos, sin anomalía) sino del **valor terminal por
Gordon Growth, matemáticamente inestable cuando el margen WACC-g es
estrecho** (PG: WACC=4.80%, g=2.5% → spread=2.3% → Gordon da $793.6bn
frente a solo $399.9bn del múltiplo de salida, casi el doble, y Gordon
pesa 80% del blend). Es una inestabilidad conocida de cualquier modelo
de Gordon Growth académico, no un fallo de esta implementación. JNJ
además tiene un ítem no recurrente real en su último ejercicio (margen
EBIT 35.6% vs. ~19-25% los 3 años previos — probablemente relacionado
con la escisión de Kenvue) que nuestra metodología (año más reciente
como ancla del fade) hereda directamente.

**Corrección aplicada:** `MIN_PRUDENT_WACC_GROWTH_SPREAD = 0.03` en
`valuation.py` — `gordon_growth_terminal_value()` ahora emite un aviso
(`warnings.warn`, no bloquea ni cambia el cálculo) cuando el margen
WACC-g es demasiado estrecho, señalando la inestabilidad. Deliberadamente
no se bajó el peso de Gordon por defecto ni se tocó la tasa terminal para
"arreglar" PG/JNJ — mismo principio de no sobreajustar a un caso conocido
que se ha mantenido en toda la sesión anterior. Diagnóstico completo en
`docs/METHODOLOGY.md` secciones 8 y 9.

**Conclusión honesta y ya más precisa que la de ayer:** el motor no es
"fiable en maduras, poco fiable en crecimiento" sin matices — es fiable
cuando (a) el spread WACC-g es saludable y (b) el histórico reciente no
tiene ítems no recurrentes, e inestable si cualquiera de las dos falla,
sea cual sea el perfil de la compañía. Dos mecanismos de desviación ya
identificados y verificados con desgloses completos: CapEx>>D&A (Big
Tech) y spread WACC-g estrecho + outliers de un año (staples de bajo
beta). 69 tests, todos en verde.

### Detalle sesión 8 — `engine/scenarios.py`

El usuario pidió seguir desarrollando sin usar Alpha Vantage. Se
construyó `engine/scenarios.py`: en vez de una etiqueta "conservador/
agresivo" (que las sesiones 6-7 mostraron que no describe bien el
comportamiento real del motor), expone 2-3 lecturas explícitas del mismo
histórico, todas derivadas de datos reales, ninguna inventada:

- **Conservador** — el valor por defecto (`default_assumptions_from_history`):
  cada driver revierte a su media histórica.
- **Mantener nivel actual** — margen, D&A, CapEx y ΔNWC se congelan en
  el último ejercicio fiscal real.
- **Alcista** — el margen EBIT extrapola la MISMA magnitud de mejora que
  ya se observó frente a su media (no una cifra arbitraria); el resto de
  drivers se mantienen en su nivel actual.

**Hallazgo no intuitivo, verificado con AMZN** (datos ya cacheados, sin
llamar a ninguna API): "Mantener nivel actual" ($71.04) sale POR DEBAJO
del "Conservador" ($84.82), pese a partir de un margen más alto; el
"Alcista" da $107.09. Investigado antes de aceptarlo: AMZN tiene el
CapEx actual en pico (18.4% de ventas) frente a un promedio histórico de
13.5% (mismo mecanismo de la sesión 6-7). El escenario conservador deja
que el CapEx revierta a la baja igual que el margen; "mantener nivel
actual" lo congela en su pico durante los 5 años — el lastre de CapEx
pesa más que la mejora de margen. El motor revela así qué supuesto
domina realmente la sensibilidad del valor en cada compañía, información
que un único número nunca comunicaría. No se reordenó el resultado para
que pareciera más intuitivo (bear<base<bull) — sería esconder
información real. Diagnóstico completo en `docs/METHODOLOGY.md`
sección 10. 6 tests nuevos (75 en total, todos en verde), matemática
verificable a mano con un histórico sintético de tendencia conocida.

### Detalle sesión 9 — `ai/memo_generator.py` (Fase 5, capa generativa)

Se construyó la capa generativa completa, con la parte determinista
100% testeada sin clave de Anthropic (instalada la SDK `anthropic` en
el venv):

- `ai/prompts/investment_memo_system.md` — prompt de sistema versionado
  (no un string embebido en el código): prohíbe explícitamente inventar
  o calcular cualquier cifra no presente en el paquete de datos, exige
  decir "no disponible" en vez de omitir en silencio, fija la estructura
  del memo (Executive Summary, Tesis de Valoración, Rango de Escenarios,
  Riesgos y Limitaciones del Modelo, Conclusión), prohíbe una
  recomendación de compra/venta (disclaimer educativo), y exige explicar
  el mecanismo detrás de una desviación grande en vez de presentarla
  como un fallo.
- `MemoInput` — paquete cerrado de datos: ticker, precio de mercado,
  consenso, WACC, los 2-3 escenarios de `engine.scenarios`, desviación
  vs. mercado/consenso del escenario conservador, supuestos clave, y
  **los avisos técnicos del propio modelo** (capturados con
  `run_scenarios_capturing_warnings()`, que envuelve `run_scenarios` con
  `warnings.catch_warnings` para recoger el aviso de
  `MIN_PRUDENT_WACC_GROWTH_SPREAD` de la sesión 7 como texto).
- `build_prompt()` — serializa `MemoInput` a JSON en el mensaje de
  usuario; nada de texto libre que el LLM pueda confundir con una
  instrucción.
- `generate_memo(memo_input, client=None, ...)` — `client` inyectable
  (cualquier objeto con `.messages.create(...)`), así que toda la
  construcción del paquete y del prompt tiene tests sin red; solo la
  llamada real necesita `ANTHROPIC_API_KEY` en `.env` (no configurada
  todavía).

**Validado de punta a punta con datos reales de AMZN** (ya cacheados,
sin llamar a ninguna API): el JSON completo que recibiría el LLM se
generó correctamente — precio de mercado $258.51, consenso $328.17, los
3 escenarios ($84.82 / $71.04 / $107.09), desviaciones, supuestos clave,
y una lista de avisos vacía (correcto: el spread WACC-g de AMZN, 8.27%
- 2.5% = 5.77%, es sano, no debía saltar el aviso de la sesión 7).

9 tests nuevos (84 en total, todos en verde) — ninguno llama a la API
real.

### Detalle sesión 10 — `app/streamlit_app.py` (Fase 6, interfaz)

**Decisión de producto en la Fase 5, antes de esto:** en vez de dar de
alta la API de pago de Anthropic todavía, el usuario pidió generar un
memo real conmigo mismo (esta sesión de Claude Code), usando exactamente
el prompt de `ai/prompts/investment_memo_system.md` sobre el paquete de
datos de AMZN ya construido — sin coste. El memo salió con la estructura
esperada (Executive Summary, Tesis, Rango de Escenarios, Riesgos,
Conclusión), sin inventar cifras y explicando el mecanismo de la
desviación en vez de ocultarla — validación cualitativa real del diseño
del prompt antes de gastar nada en la API.

Con eso resuelto, se construyó el esqueleto de Streamlit:

- Dos modos de datos en la sidebar: **universo cacheado** (Big Tech vía
  Alpha Vantage, Consumo defensivo vía yfinance — WACC riguroso vía
  comparables reales del propio grupo, reutilizando
  `engine.validation.build_peer_set` en vez de reimplementar la
  construcción de peers) y **cualquier ticker** vía yfinance sin límite
  de cuota (WACC simplificado con beta propio, etiquetado como tal).
- Sliders para `n_years`, `terminal_growth_rate`, `lookback_years`,
  `gordon_weight` — cualquiera puede reproducir los hallazgos de las
  sesiones 6-9 cambiando un control, no leyendo código.
- Gráfico de los 3 escenarios (bear/hold/bull) con el precio de mercado
  y el consenso como líneas de referencia — un solo hue neutro para las
  barras (son una magnitud ordenada, no categorías de identidad) más
  etiquetas directas, siguiendo la guía de la skill `dataviz`.
- Matriz de sensibilidad WACC×g con gradiente secuencial de un solo hue
  (`cmap="Blues"`, magnitud → un hue, nunca arcoíris).
- Sección de memo: si hay `ANTHROPIC_API_KEY`, botón que llama a
  `generate_memo()`; si no (caso actual), muestra el prompt listo para
  copiar y pegar en Claude.ai — el mismo flujo manual que se usó para el
  memo de AMZN.

**Validación sin navegador disponible en esta sesión:** no se pudo
capturar la interfaz renderizada (sin Playwright/Chromium en el
entorno). Se verificó en su lugar (a) que el servidor arranca sin
errores y responde HTTP 200 en tres arranques distintos, y (b) que la
ruta de cómputo completa — incluida la figura de matplotlib y la tabla
`pandas.Styler` con gradiente, las dos piezas nuevas sin cobertura de
tests previa — corre de punta a punta sin excepciones, tanto en modo
universo cacheado (AMZN) como en modo ticker arbitrario (NVDA, datos
frescos de yfinance). Pendiente: captura visual real en una sesión con
navegador disponible.

Al revisar el propio código se encontró y corrigió una duplicación: la
construcción de la lista de comparables en la app repetía la lógica ya
existente y testeada en `engine.validation.build_peer_set` — se
refactorizó para reutilizarla en vez de mantener dos implementaciones
del mismo cálculo.

`matplotlib` y `streamlit` instalados en el venv y añadidos a
`requirements.txt`.

### Detalle sesión 11 — bug real encontrado: `interest_expense=0` espurio en AAPL

El usuario pidió explícitamente rigor matemático/técnico antes de seguir
con visuales y preguntó qué más había por desarrollar. En vez de asumir
que todo estaba fino, se auditó lo que nunca se había validado contra
datos reales: `engine/ratios.py` y `engine/comps.py`, construidos en la
Fase 3 con solo fixtures sintéticas.

Al correr los 8 ratios reales, `interest_coverage()` dio `inf` para
AAPL — investigado antes de aceptarlo. **Causa raíz:** Alpha Vantage
reporta `interestExpense=0` en FY2024 (y `None` en FY2025) pese a que
AAPL mantiene ~$112-119bn de deuda real (FY2023 sí reportó $3.933bn
correctamente). Esto NO es un caso raro sin consecuencias: ese mismo
campo alimenta `cost_of_debt()` dentro de `wacc_builder.build_wacc()` —
con el dato espurio, `cost_of_debt` de AAPL se calculaba silenciosamente
como **0.0%**.

**Por qué pasó desapercibido en las sesiones 6-9:** el peso de la deuda
en el WACC de AAPL es pequeño, así que el WACC agregado seguía
"pareciendo razonable" (8.77% en vez del 8.84% correcto, solo 7 puntos
básicos de diferencia) — el error se escondía dentro de un output que
superficialmente parecía plausible. Lección metodológica explícita:
revisar solo si el agregado "parece razonable" no basta; hay que auditar
inputs individuales, sobre todo los de bajo peso, que son los que un
vistazo al resultado final no detecta.

**Corregido** en ambos proveedores de datos (`_clean_interest_expense()`
en `data_provider.py` y `yfinance_provider.py`, misma lógica en los dos
para que no reaparezca por otra fuente): un `interest_expense=0`
reportado junto a `total_debt>0` se trata como dato faltante, no como
coste de deuda real de cero — cae al último año con un valor genuino.
Una compañía sin deuda sí puede tener 0 legítimo; ese caso no se filtra
(test dedicado para ambas ramas).

**Impacto verificado:** `cost_of_debt` AAPL 0.0%→3.50%, WACC AAPL
8.77%→8.84%, `interest_coverage` AAPL `inf`→29.06x. Tabla de la Fase 7
(sección 3 de este documento y `docs/METHODOLOGY.md` sección 7) y
resumen agregado actualizados con las cifras corregidas (el cambio en la
desviación media es marginal, 54.7%→54.8%, pero ahora es la cifra
correcta). 3 tests de regresión nuevos (87 en total, todos en verde).

**Ratios validados con los 8 tickers reales, todos con sentido**
(ver `docs/METHODOLOGY.md` sección 13 para el detalle): las 8 compañías
"crean valor" (ROIC > WACC), ROE de AAPL extremo pero correcto (~165%,
consistente con recompras masivas reduciendo su equity contable — no es
un bug, es el comportamiento real y conocido de esa métrica en AAPL),
Debt/EBITDA e interest coverage en rangos plausibles en las demás 7.

**Sigue pendiente, identificado pero no implementado:** ni `ratios.py`
ni `comps.py` están conectados todavía a `app/streamlit_app.py` ni a
`ai/memo_generator.py` — existen, están testeados y ahora también
validados con datos reales, pero el usuario final de la app/memo no los
ve todavía. Candidato claro para la próxima sesión si se sigue en la
línea de "cerrar huecos" antes de visuales.

### Detalle sesión 12 — comparación directa contra el Excel profesional; fix real de metodología

El usuario pidió explícitamente volver a comparar la app contra "el
modelo a seguir" (el Excel de Amazon) en vez de seguir añadiendo piezas
nuevas. Hasta ahora solo se había validado que las fórmulas coinciden
exactamente y que el WACC da un resultado casi idéntico — nunca se
habían puesto lado a lado, año a año, los supuestos de proyección del
analista frente a los que genera nuestro motor automático.

**Comparación año a año (AMZN):**

| | Excel (analista, 2024-29) | App antes del fix |
|---|---|---|
| Crecimiento ingresos | plano ~10-11% los 6 años | decae de 11.7% a 2.5% en 5 años |
| Margen EBIT | sube 9.8%→15.0% | baja 13.9%→10.5% |
| CapEx % ventas | plano ~12-13% (dato de nov-2024, pre-supercycle IA) | baja de 18.4% (real 2026) a 13.5% |

WACC: Excel 8.33% vs. app 8.27% — prácticamente idéntico, buena señal.

**Tres diferencias, tres veredictos distintos:**
1. **Forma del fade de crecimiento — era un bug de diseño, corregido
   hoy.** El analista mantiene el crecimiento plano durante toda la
   previsión explícita y solo cae a la tasa terminal de golpe, en la
   fórmula de Gordon Growth. Nuestro motor decaía el crecimiento DENTRO
   de la propia ventana explícita — una forma de curva distinta a la
   del modelo de referencia, no solo "más conservadora".
2. **Dirección del margen — postura de modelado, no se toca.** Ya
   documentado (sesiones 6-9). Dato nuevo: el margen real de AMZN en
   2025 (13.9%) ya casi alcanzó la previsión del Excel para *2029*
   (15.0%) — la tesis del analista resultó más acertada que la
   reversión a la media.
3. **Nivel de CapEx — aquí la app va por delante del Excel, no por
   detrás.** El Excel es de antes del supercycle de IA; nuestros datos
   de 2026 sí lo capturan.

**Corrección aplicada (punto 1):** `default_assumptions_from_history()`
ya no recibe `terminal_growth_rate` ni lo usa para el crecimiento de
ingresos — ahora proyecta el CAGR reciente PLANO durante todo el
horizonte explícito, igual que el analista. `terminal_growth_rate`
sigue existiendo, pero solo como argumento de `DCFInputs`/`run_dcf`
para el cálculo del valor terminal (nunca para las series explícitas).
Actualizados `engine/scenarios.py`, `engine/validation.py`,
`app/streamlit_app.py` y todos los tests afectados.

**No es un ajuste para acercar el precio al mercado — es alinear la
forma de la curva con la que usa literalmente "el modelo a seguir".**
Que el resultado suba es una consecuencia observada, no el objetivo.

**Impacto real (universo Big Tech):**

| Ticker | Antes | Después | Mercado |
|---|---|---|---|
| AMZN | $84.82 (-67.2%) | $104.41 (-59.6%) | $258.51 |
| MSFT | $295.80 (-40.8%) | $397.36 (-20.5%) | $499.70 |
| GOOGL | $270.02 (-61.7%) | $333.41 (-52.7%) | $705.51 |
| META | $365.03 (-48.8%) | $534.56 (-25.0%) | $712.53 |
| AAPL | $142.74 (-55.4%) | $140.60 (-56.1%) | $319.97 |

**Desviación media: 54.8%→42.8% vs. mercado, 53.4%→41.3% vs. consenso.**
MSFT y META prácticamente reducen su brecha a la mitad. AAPL casi no
cambia (su crecimiento reciente ya era bajo, así que plano vs. decayendo
apenas difiere para esa compañía) — el efecto es proporcional a cuánto
crecimiento por encima de la tasa terminal tenía cada compañía, exactamente
lo que cabría esperar.

**Efecto secundario honesto, no escondido:** el mismo cambio empeora
ligeramente a PG/JNJ (ya identificadas como inestables por spread
WACC-g estrecho) — un crecimiento plano más alto alimenta un UFCF
terminal mayor, que la fórmula de Gordon (ya inestable ahí) amplifica
más. Es la misma causa raíz interactuando con dos cambios distintos, no
una regresión nueva. Diagnóstico completo en `docs/METHODOLOGY.md`
sección 14.

Nuevo test de regresión con CAGR sintético del 21% (lejos de cualquier
tasa terminal, para que no pueda pasar por coincidencia). 88 tests en
total, todos en verde.

### Detalle sesión 13 — comprobación del fade de margen/CapEx + `ratios.py` conectado

Dos piezas, siguiendo el mismo estándar de rigor de la sesión anterior:

**1. Comprobación (no asumida) de si el fade lineal es correcto también
para margen/D&A/CapEx.** Regresión lineal sobre las series reales del
Excel: margen EBIT R²=0.978, D&A R²=0.982 (el fade lineal es una réplica
excelente de cómo el analista los modela — sin bug), CapEx R²=0.625
(ajuste mediocre, pero rango tan estrecho en el Excel de 2024 que el
impacto es bajo, y no comparable con el CapEx real de hoy que refleja el
supercycle de IA). Conclusión: sin cambios, comprobado y descartado, no
pendiente. Detalle en `docs/METHODOLOGY.md` sección 14.

**2. `engine/ratios.py` conectado al memo y a la interfaz** (ya no
huérfano). Nuevo `RatioSnapshot` + `compute_ratio_snapshot()` +
`latest_ratio_snapshot()`. `ai/memo_generator.py` acepta `ratios`
opcional; el prompt de sistema añade una sección "Rentabilidad y
Solvencia" que se omite si no hay ratios. `app/streamlit_app.py` muestra
ROE, ROIC vs. WACC, Debt/EBITDA, cobertura de intereses y current ratio.

**Guarda añadida** (mismo patrón que el spread WACC-g): `roic()` avisa
si el capital invertido es `<=0` (equity negativo por recompras
agresivas) — no observado en los 8 tickers piloto, pero es un caso real.

**Dos bugs de serialización JSON encontrados al conectar esto a un
payload real por primera vez** (nunca se había hecho): `creates_value`
es `np.bool_`, que sin cuidado se serializaba como la CADENA `"True"` en
vez del booleano JSON `true` — corregido con `bool()` explícito.
`interest_coverage=inf` (empresa sin deuda) generaba el token `Infinity`,
inválido en JSON estricto — corregido representándolo como texto. Ninguno
de los dos rompía nada visible; se encontraron verificando de punta a
punta con datos reales de AMZN antes de dar la integración por cerrada.

10 tests nuevos (96 en total, todos en verde). `comps.py` sigue sin
conectar — candidato claro para la próxima sesión.

### Detalle sesión 14 — `comps.py` conectado: múltiplo de salida de peers, no propio

Última pieza huérfana conectada. Hasta ahora, el múltiplo EV/EBITDA del
valor terminal blended era el de la PROPIA empresa objetivo — circular
(si el mercado ya tiene a AMZN sobre/infravalorada, ese sesgo se cuela
directo en nuestra propia valoración). El Excel usa un múltiplo de una
tabla de comparables, no el propio.

**Corregido:** `value_ticker()` ahora usa la mediana de los comparables
del universo (excluyendo el target) como múltiplo de salida, igual que
ya hacía con el WACC. `ValuationCheck` expone `peer_ev_ebitda_multiple`
para que quede trazable. La interfaz hace lo mismo en modo universo
cacheado y muestra la tabla de comparables completa.

**Impacto real, mixto — reportado tal cual, no maquillado:**

| Universo | Antes (múltiplo propio) | Después (múltiplo peers) |
|---|---|---|
| Big Tech (media vs. mercado) | 42.8% | 43.4% (~igual) |
| AMZN | -59.6% | **-54.1%** (mejora) |
| AAPL | -56.1% | -63.0% (empeora bastante) |
| KO | +2.8% | **+0.1%** (casi exacto) |
| PG | +85.2% | +92.1% (empeora) |

A diferencia del fix de la forma del crecimiento (sesión 12, mejora
limpia en las 5 compañías), este cambio es correcto en principio
(evita la circularidad) pero de efecto mixto en este universo concreto:
AAPL cotiza muy por encima de sus propios comparables de "Big Tech"
(28.4x vs. 13-16x del resto), así que excluir su propio múltiplo la
hace ver más barata de lo que el mercado realmente le asigna. Se
mantiene el cambio de todas formas — es la réplica correcta de la
metodología, y revertirlo porque el agregado no mejoró sería
sobreajustar a un resultado, el mismo error que este proyecto ha evitado
siempre. El problema real que revela (solo 3-5 comparables por sector,
no siempre homogéneos) queda anotado como límite conocido, no resuelto.

1 test de regresión con un múltiplo propio imposible de coincidir por
casualidad (999x), para probar sin ambigüedad que se usa el de peers.
97 tests, todos en verde. Diagnóstico completo en `docs/METHODOLOGY.md`
sección 16.

### Detalle sesión 15 — auditoría técnica profesional completa

El usuario pidió una auditoría completa: releer todo el código (no solo
memoria de sesiones anteriores) y comparar sistemáticamente contra el
Excel en todas las dimensiones aún no revisadas. Resultado en
`docs/AUDIT.md` (y publicado como artifact navegable) — **1 hallazgo
crítico, 4 importantes, 5 moderados, 3 informativos**, todos verificados
con evidencia concreta (grep del código real, no solo inspección):

- **C1 (crítico): el stub period nunca se calcula en el pipeline real.**
  `discount_periods()`/`pv_of_cash_flows()` soportan `stub_fraction`
  (validado exacto contra el Excel), pero ningún módulo de orquestación
  calcula jamás la fracción de año real desde la fecha de hoy —
  `DCFInputs.stub_fraction` siempre usa su default de 1.0. Cada
  valoración asume implícitamente que "hoy" es el 1 de enero del primer
  año proyectado, infravalorando sistemáticamente cuanto más avanzado
  esté el año en que se ejecuta la herramienta.
- **I1: risk-free rate (3.909%) y MRP (4.06%) son constantes congeladas**
  del Excel de ~nov-2024, no en vivo, no ajustables desde la interfaz.
  Alpha Vantage ya tiene `TREASURY_YIELD`; yfinance puede leer `^TNX`.
- **I2: el Treasury Stock Method está construido y validado exacto,
  pero nunca se usa** — todo el pipeline real usa `shares_outstanding`
  en bruto. Limitación estructural (sin fuente de datos de opciones
  outstanding gratuita), no un bug de una línea.
- **I3: excepciones no controladas en modo "cualquier ticker"** —
  `IndexError`/`TypeError` sin capturar si `interest_expense` está
  vacío o `beta` es `None` (posible en small caps/IPOs recientes vía
  yfinance). La app crashearía para esos tickers. ✅ **Corregido esta
  sesión (continuación)** — ver detalle abajo.
- **I4: universo de comparables pequeño/heterogéneo** — ya documentado
  en sesión 14, incluido aquí formalmente.
- **M1-M5:** `requirements.txt` sin versiones fijadas; `gordon_weight=0.8`
  heredado del Excel sin justificación propia; `DCFInputs` sin validar
  `wacc>0`/`gordon_weight∈[0,1]`; Gordon Growth se calcula y avisa
  aunque su peso sea 0; sin verificación de divisa de reporte.
- **N1-N3 (informativo, sin acción):** tipo impositivo (R²=0.001, sin
  tendencia, plano es correcto) y ΔNWC (R²=0.425, rango estrecho)
  verificados contra el Excel — cierra la comprobación de forma de fade
  para TODOS los drivers, no solo margen/D&A/CapEx/crecimiento. Sin
  tests de `app/streamlit_app.py` en sí (práctica estándar para
  Streamlit). Degradación silenciosa de la ventana de histórico si hay
  menos años de los pedidos.

El usuario pidió corregir los hallazgos uno por uno, empezando por el
crítico. **C1 (stub period) e I3 (excepciones no controladas) ya están
corregidos y validados con datos reales en esta misma sesión** — ver
detalle de cada uno abajo.

### Detalle — C1 corregido: stub period real, no siempre 1.0

- `engine.valuation.compute_stub_fraction()` — fracción real por días
  de calendario entre la fecha de valoración y el próximo cierre fiscal
  (con fallback para 29 de febrero y para valorar justo en el día de
  cierre, nunca devuelve 0.0).
- `historical_financials()` en ambos proveedores ahora expone
  `fiscal_year_end_month`/`fiscal_year_end_day` (antes solo el año).
- `engine.projections.stub_fraction_from_history()` une ambas piezas;
  degrada a 1.0 si el histórico no trae esas columnas (no rompe tests
  existentes con fixtures sintéticas).
- `run_scenarios()`/`value_ticker()` aceptan `valuation_date` (por
  defecto hoy) y calculan el stub solos. La app añade un `date_input` y
  muestra el stub calculado junto a las métricas.

**Validado con datos reales, no solo con tests:** AMZN (cierre 31-dic),
valorado el 5-sept-2026 → stub=0.3205 → precio implícito **$104.41 →
$108.78 (+4.19%)**. Confirma la dirección exacta que predijo la
auditoría: el bug infravaloraba sistemáticamente. Verificado también con
MSFT (cierre 30-jun) y con el caso límite de "hoy" ya pasado el último
cierre reportado (salta correctamente al ejercicio siguiente).

12 tests de regresión nuevos. 109 tests en total, todos en verde.
Servidor Streamlit re-verificado arrancando limpio tras el cambio.

### Detalle — I3 corregido: excepciones no controladas en modo "cualquier ticker"

Arreglo en dos capas, no solo un parche superficial:

1. **Capa app (`app/streamlit_app.py`):** toda la rama "cualquier ticker"
   ahora vive dentro de un `try/except Exception`, con comprobaciones
   explícitas antes de calcular el WACC simplificado — histórico vacío,
   `beta` ausente, `interest_expense`/`tax_rate` sin datos. Cualquier
   fallo muestra `st.error(...)` con el motivo concreto y sugiere probar
   otro ticker, en vez de un traceback crudo.
2. **Causa raíz más profunda, encontrada al validar con un ticker
   inválido real (`ZZZZINVALID`), no solo con la hipótesis sintética de
   la auditoría:** `historical_financials()` en **ambos** proveedores
   (`data_provider.py` y `yfinance_provider.py`) crasheaba con
   `KeyError: 'fiscal_year'` cuando no hay ningún año/fecha en común
   entre los tres estados financieros — `pd.DataFrame([]).sort_values(...)`
   falla porque un DataFrame de cero filas construido desde una lista
   vacía no tiene columnas. Arreglado en la fuente: ambos proveedores
   ahora devuelven `pd.DataFrame(columns=HISTORICAL_FINANCIALS_COLUMNS)`
   (vacío pero con la forma correcta) en ese caso, en vez de dejar que
   la excepción se propague sin control.

**Validado con datos reales:** probado en la app con el ticker inválido
`ZZZZINVALID` — antes crasheaba con traceback en pantalla, ahora
muestra el mensaje de error esperado y controlado. Confirma que sin
esta segunda capa, la capa de la app por sí sola no habría bastado (el
crash ocurría un nivel más abajo de lo que la auditoría original
señalaba).

2 tests de regresión nuevos (uno por proveedor). **111 tests en total,
todos en verde.** Servidor Streamlit re-verificado arrancando limpio.

### Detalle — I1 corregido: risk-free rate en vivo, prima de riesgo como slider

Los dos parámetros se trataron de forma distinta a propósito, porque
tienen disponibilidad de datos distinta:

1. **Risk-free rate (Treasury 10Y):** sí tiene fuente en vivo gratuita
   y fiable. Nuevo `treasury_yield_10y()` en `engine/yfinance_provider.py`
   (índice `^TNX`) y `AlphaVantageClient.treasury_yield()` en
   `engine/data_provider.py` (función económica `TREASURY_YIELD`, con
   refactor de `_fetch()` para soportar endpoints sin `symbol`). La app
   siempre usa la vía yfinance (sin cuota diaria), incluso en modo
   "universo cacheado con Alpha Vantage" — es un dato de mercado
   ambiental igual para cualquier ticker, no tiene sentido gastar la
   cuota de 25 peticiones/día de Alpha Vantage en él. Si la consulta en
   vivo falla, cae a la constante congelada original con aviso
   explícito en la interfaz (nunca en silencio).
2. **Prima de riesgo de mercado (ERP):** sin fuente en vivo gratuita
   fiable (Damodaran se publica a mano, no vía API estable) — convertida
   en `st.slider` ajustable en la sidebar, con el valor del Excel como
   valor por defecto documentado.

**Validado con datos reales:** el 2026-09-06 el Treasury 10Y real
cotizaba a **4.784%** frente al 3.909% congelado — +0.875 puntos.
Revalorando AMZN con el resto de supuestos idénticos: WACC 8.266% →
9.096%, precio implícito **$108.80 → $98.00 (-9.93%)**. La constante
congelada estaba inflando de forma material el precio de todas las
valoraciones de la sesión, incluida la propia verificación numérica del
fix de C1.

7 tests de regresión nuevos. **118 tests en total, todos en verde.**
Servidor Streamlit reiniciado y verificado arrancando limpio (puerto
8514, `/_stcore/health` → `ok`, sin tracebacks en el log).

### Detalle — M1 corregido: versiones fijadas en `requirements.txt`

Las 10 dependencias directas quedaron fijadas a la versión exacta ya
instalada y verificada en el venv del proyecto (`pandas==3.0.5`,
`numpy==2.5.2`, `yfinance==1.7.0`, `anthropic==1.4.0`,
`streamlit==1.63.0`, `pytest==9.1.1`, `openpyxl==3.1.5`,
`requests==2.34.2`, `python-dotenv==1.2.3`, `matplotlib==3.11.1`) —
deliberadamente solo las directas, no un `pip freeze` completo con
transitivas (más legible; pip resuelve el resto a partir de estas).

**Verificado:** `pip install -r requirements.txt --dry-run` sobre el
venv existente resuelve sin ningún conflicto. Suite completa
re-ejecutada tras el cambio: 118 tests, todos en verde — el pin no
cambia ningún comportamiento, solo fija lo que ya estaba en uso.

### Detalle — M3 corregido: `DCFInputs` valida `wacc>0` y `gordon_weight∈[0,1]`

Dos comprobaciones nuevas en `DCFInputs.__post_init__()` (mismo estilo
que las ya existentes de longitudes/`diluted_shares`): `wacc<=0` y
`gordon_weight` fuera de `[0.0, 1.0]` lanzan `ValueError` explícito. Los
límites 0.0 y 1.0 quedan incluidos a propósito — Gordon puro o múltiplo
puro son escenarios válidos, no un error.

**Verificado:** arreglo puramente defensivo — confirmado que ningún
test ni ninguna ruta del pipeline real construye `DCFInputs` fuera de
estos rangos (WACC siempre positivo por construcción, `gordon_weight`
siempre viene de un slider acotado en la app), así que no cambia ningún
resultado existente. 4 tests de regresión nuevos. **122 tests en total,
todos en verde.** Servidor Streamlit reiniciado y verificado arrancando
limpio.

### Detalle — M4 corregido: Gordon Growth ya no se calcula ni avisa con peso 0

Resultó más serio de lo que parecía al leer solo la descripción del
hallazgo: `run_dcf()` llamaba siempre a `gordon_growth_terminal_value()`,
que puede lanzar `ValueError` si `wacc<=terminal_growth_rate`. Con
`gordon_weight=0` puesto precisamente para esquivar un caso patológico
de Gordon, el DCF **igualmente fallaba** — el peso 0 no protegía nada,
porque la función se llamaba y podía reventar antes de que su resultado
se descartara en el blend.

**Corregido:** `run_dcf()` ahora calcula `gordon_tv` solo si puede
llegar a contar — múltiplo de salida disponible y `gordon_weight>0`, o
sin múltiplo de salida (Gordon es entonces la única fuente posible, sin
importar el peso). Con `gordon_weight=0` y múltiplo presente,
`gordon_terminal_value` queda en `0.0`, sin calcularse ni avisar.

**Verificado:** 4 tests de regresión nuevos (caso que antes lanzaba
`ValueError` ahora funciona; aviso de spread estrecho ya no se emite
con peso 0, verificado forzando que cualquier aviso falle el test;
Gordon se sigue calculando sin múltiplo de salida pese a peso 0; Gordon
se sigue calculando y contribuyendo con peso>0). Revalorado AMZN
(`gordon_weight=0.8`, camino no afectado): precio idéntico, **$98.00**
— confirma que no cambia ningún resultado existente. **126 tests en
total, todos en verde.** Servidor Streamlit reiniciado y verificado
arrancando limpio.

## 5. Sesión 16 — Auditoría de progreso (visión global, no solo hallazgos puntuales)

El usuario pidió una segunda auditoría, esta vez de progreso general:
qué hace la herramienta, qué no hace, qué hace bien/mal, y cómo seguir.
Informe completo en **`docs/PROGRESS_REVIEW.md`** (nuevo documento,
distinto de `docs/AUDIT.md` que sigue siendo el catálogo de hallazgos
puntuales). Resumen de lo más importante:

- **Estado de fases del blueprint:** Fases 0-4 y 6 completas; Fase 5
  (capa generativa) construida pero **nunca probada con la API real de
  Anthropic** (sin `ANTHROPIC_API_KEY`, 0 llamadas reales en la vida del
  proyecto); Fase 7 (validación) hecha pero con una conclusión que hay
  que decir con más claridad (ver abajo); **Fase 8 (despliegue)
  completamente sin empezar** — sin repo remoto en GitHub siquiera; Fase
  9 (sentiment) no iniciada (extensión opcional).
- **Se repitió la medición de precisión (Fase 7) con el código actual y
  el risk-free rate en vivo de hoy — cifra fresca, no recordada:**
  desviación media absoluta vs. mercado en los 8 tickers piloto pasa de
  **46.5% (línea base pre-sesión-15) a 41.8% (hoy)** — una mejora real,
  pero **no uniforme**: Big Tech empeora (43.4%→46.9%, el risk-free rate
  más alto baja más el precio de compañías ya infravaloradas) mientras
  Consumo defensivo mejora mucho (51.6%→33.3%, el WACC más alto
  estabiliza la fórmula de Gordon Growth en compañías con spread
  WACC-g estrecho). Ambos efectos son mecánicamente correctos y
  esperados, no bugs.
- **Conclusión honesta que hay que hacer más visible:** el motor es
  técnicamente exacto (Excel al céntimo) pero como predictor del precio
  de mercado se equivoca ~42% de media — no por un error, sino porque
  "reversión a la media" y "lo que paga el mercado hoy por crecimiento
  futuro" son cosas distintas por diseño. Esto ya estaba documentado en
  sesiones anteriores, pero no es suficientemente visible en la propia
  interfaz de la app.
- **Roadmap recomendado (por impacto, no por dificultad técnica):**
  1) desplegar (GitHub + Streamlit Cloud, Fase 8 — máxima prioridad,
  nada es demostrable sin esto); 2) probar la capa generativa con una
  llamada real antes de desplegar; 3) hacer explícita en la UI la
  limitación de precisión; 4) crear `scripts/validate_universe.py`
  reproducible en vez de reconstruir la cifra a mano cada sesión;
  5) decidir M2 (`gordon_weight=0.8`) como decisión de producto, no
  dejarlo pendiente indefinidamente; 6) Fase 9, solo después.

## 6. Principios de fondo (sesión 16)

**Principio de fondo que sigue aplicando:** cualquier UI debe mostrar el
número junto a su explicación, nunca el número solo. No revertir un
cambio metodológicamente correcto solo porque el resultado agregado no
mejora (ni ajustar supuestos para acercarse al precio de mercado).
Auditar la orquestación, no solo las fórmulas. Cada arreglo se valida
con datos reales y una cifra de impacto concreta antes de darlo por
cerrado — no basta con que los tests sintéticos pasen. Y ahora también:
**re-medir el agregado completo tras varios arreglos combinados, no
solo el impacto de cada uno por separado** — la sesión 16 mostró que la
suma de mejoras individuales puede no sumar limpiamente en el número
agregado (mejora en un grupo, empeora en otro).

## 7. Sesión 16 (continuación) — Reverse DCF: expectativas implícitas del mercado

El usuario planteó la pregunta que debía enmarcar todo lo anterior:
**¿para qué se usa un DCF de verdad, y qué información aporta?** Un DCF
hacia delante no predice el precio de mercado — la pregunta
complementaria, estándar en equity research, es "¿qué tendría que ser
cierto para justificar el precio que YA cotiza el mercado?". Eso no
existía en la herramienta: solo se mostraba el % de desviación, nunca
el mecanismo. Desarrollado con el mismo rigor que el resto del motor
(replica exactamente `run_dcf()`, no una segunda metodología):

- **`engine/valuation.py`:** `solve_for_target_price()` (bisección pura,
  sin scipy) + `implied_terminal_growth_rate()` (resuelve la tasa de
  crecimiento perpetuo que justifica un precio objetivo).
- **`engine/projections.py`:** `implied_revenue_growth()` (resuelve el
  crecimiento de ingresos plano del horizonte explícito que justifica un
  precio objetivo, misma forma que `default_assumptions_from_history()`).
- **`engine/reverse_dcf.py`** (nuevo módulo de orquestación, mismo patrón
  que `wacc_builder.py`): `compute_implied_expectations()` combina ambas
  piezas en un bundle único, con manejo explícito de "fuera de rango"
  (nunca un fallo silencioso ni un valor inventado).
- **App:** nueva sección "Expectativas implícitas del mercado (reverse
  DCF)" entre supuestos y ratios.
- **Memo:** `MemoInput.implied_expectations` nuevo, prompt de sistema
  actualizado con una sección dedicada — la desviación se explica con
  esto en vez de con una causa inventada.

**Validado con datos reales (AMZN, hoy):** el mercado paga un
crecimiento de ingresos implícito del **31.8%** frente al 11.7% asumido
(consenso: 38.3%) — un gap de +20.1pp / +26.6pp. Alternativamente, vía
solo crecimiento terminal perpetuo: 7.25%/7.72%, marcado ⚠️ por caer en
zona de inestabilidad de Gordon Growth — evidencia de que la brecha se
explica por crecimiento del horizonte explícito, no por una perpetuidad
optimista. **Validado también en un caso límite real** (NVDA, modo
"cualquier ticker"): con un CAGR ya asumido de ~100%, ambos solvers
devuelven correctamente "fuera de rango" en vez de fallar o forzar un
resultado. Verificado end-to-end con Playwright contra la app real
corriendo (no solo con tests): capturas de pantalla confirman que la
tabla renderiza los números exactos del motor, sin errores de consola
ni tracebacks, en ambos modos de datos.

14 tests de regresión nuevos. **142 tests en total, todos en verde.**
Detalle técnico completo en `docs/METHODOLOGY.md` sección 20;
`docs/PROGRESS_REVIEW.md` actualizado (sección 6, roadmap ítem 1 ya
hecho).

## 8. Sesión 16 (continuación) — Fase 8: despliegue completo

Repo público en GitHub y app desplegada y verificada en vivo:

- **Repo:** https://github.com/alvarosalasmasso-ship-it/equity-valuation-agent (público)
- **App:** https://equity-valuation-agent.streamlit.app/

Proceso (con obstáculos reales, no trivial):

1. Sin `gh` CLI instalado — se instaló vía `winget install GitHub.cli`
   y se autenticó con device-code flow en el navegador del usuario.
2. `gh repo create --private --source=. --remote=origin --push` creó el
   repo y subió el código. `gh auth setup-git` fue necesario para que
   `git push` en sí usara las credenciales de `gh` (sin él, fallaba por
   falta de terminal interactiva para el prompt de Windows).
3. Verificado el historial completo de commits (`git log --all
   --diff-filter=A --name-only`, no solo el HEAD) sin ningún `.env` ni
   credencial — limpio, antes y después del cambio de visibilidad.
4. **Bloqueo real:** Streamlit Community Cloud no encontraba el repo al
   desplegar. Diagnosticado por descarte (cuenta correcta, GitHub App
   de Streamlit instalada pero sin selector de repos en "Configure",
   solo opción de revocar) — consistente con que el repo privado no
   estaba en la lista de acceso de esa instalación. Resuelto pasando el
   repo a **público** (`gh repo edit --visibility public
   --accept-visibility-change-consequences`) en vez de seguir depurando
   permisos de la GitHub App — más rápido y correcto de todas formas
   para un proyecto de portfolio.
5. **Verificado en vivo con una captura de pantalla real** (Playwright,
   sin sesión/cookies) contra la URL pública: la app carga completa, con
   los mismos datos que la versión local (AMZN, WACC 9.10%, risk-free
   rate en vivo, escenarios) — no solo "no dio error", confirmación
   visual real de contenido correcto.

`README.md` actualizado con el enlace a la app en vivo. Detalle completo
en `docs/PROGRESS_REVIEW.md` sección 7.

## 9. Sesión 16 (continuación) — Script de validación reproducible

El usuario pidió no seguir con la prueba en vivo de la capa generativa
por ahora ("olvida lo del memo en tiempo real"). Se preguntó qué
recomendaba seguir desarrollando; recomendación: `scripts/validate_universe.py`
(mayor valor para defender cifras del CV/entrevista de forma
reproducible) frente a cerrar M2 (rápido pero bajo impacto) o Fase 9
(alcance nuevo antes de agotar lo ya construido). Aceptado.

- **`engine/validation.py`:** `value_ticker()` ahora también calcula y
  adjunta las expectativas implícitas del mercado (`engine/reverse_dcf.py`)
  al `ValuationCheck` resultante — reutiliza los `assumptions`/`base_inputs`
  que ya calculaba, no reconstruye nada.
- **`scripts/validate_universe.py`** (nuevo): corre el pipeline completo
  sobre Big Tech/Cloud (Alpha Vantage) y Consumo defensivo (yfinance) con
  el risk-free rate en vivo del día, aislando fallos por ticker (uno
  problemático no tira abajo todo el grupo), y guarda un snapshot fechado
  en `data/validation_history/<fecha>.json` — este sí versionado en el
  repo, a diferencia de `data/cache/`.

**Verificado con datos reales:** el script reproduce exactamente el
41.82% de desviación media combinada (46.94% Big Tech, 33.27% Consumo
defensivo) medido a mano en sesiones anteriores, y las expectativas
implícitas de AMZN en el JSON coinciden con las usadas en el memo de
ejemplo de esta sesión (31.8% de crecimiento implícito vs. mercado).

2 tests de regresión nuevos. **144 tests en total, todos en verde.**
Commit y push hechos.

## 10. Sesión 16 (continuación) — Pase de UX/UI

El usuario pidió centrarse en experiencia de usuario y frontend,
mencionando la extensión 21st y varias skills de diseño. Aclaración
dada antes de empezar: 21st/Figma generan componentes React/Tailwind
o archivos de Figma, que no se pueden usar directamente en una app
Streamlit (renderiza sus propios widgets en Python del lado servidor)
sin un puente de componente custom — no aplicables aquí sin un cambio
de arquitectura mucho mayor. Se aplicaron en su lugar los principios sí
transferibles (paleta, tipografía, jerarquía, corrección de gráficos
vía la skill `dataviz`) a través de lo que Streamlit sí permite
personalizar: tema, CSS inyectado, y Plotly en vez de matplotlib.

- **`.streamlit/config.toml`** (nuevo): tema "quant/banca de inversión"
  — azul marino (#1B3A5C) como acento único, grises fríos neutros. Esto
  recolorea todos los widgets nativos automáticamente.
- **CSS inyectado** (`_inject_custom_css()` en `app/streamlit_app.py`):
  IBM Plex Sans (UI/headings) + IBM Plex Mono (todos los números,
  `font-variant-numeric: tabular-nums`) vía Google Fonts. Tarjetas con
  borde para métricas, tipografía de pestañas, paleta centralizada en
  un único diccionario `COLORS` (una sola fuente de verdad, leída tanto
  por el CSS como por los gráficos Plotly).
- **Gráficos migrados de matplotlib a Plotly:** el gráfico de escenarios
  ahora tiene anotaciones de mercado/consenso en top/bottom sin solape
  (bug real del gráfico anterior, visible con NVDA: las etiquetas de
  precio de mercado y consenso quedaban una encima de otra cuando los
  precios estaban cerca) y tooltips. La matriz de sensibilidad pasó de
  una tabla `pandas.Styler` a un heatmap Plotly con una sola escala de
  color secuencial (principio `dataviz`: sequential = un solo hue,
  claro→oscuro), WACC ascendente de arriba a abajo.
- **Layout reorganizado en pestañas** (`st.tabs`): Valoración / Supuestos
  y expectativas / Fundamentales / Memo — antes era una sola página con
  scroll largo. El cómputo se separó de la presentación (todo se calcula
  una vez arriba; las pestañas solo leen resultados ya calculados), sin
  tocar ninguna lógica financiera.
- **Fix de correctitud encontrado en el propio pase de diseño:** el
  metric `ROIC vs. WACC` mostraba el delta "Crea valor"/"No crea valor"
  siempre en verde — `st.metric` no puede inferir el signo de un texto
  libre sin un "-" delante, así que "No crea valor" se pintaba en verde
  igual que "Crea valor" (activamente engañoso). Corregido con
  `delta_color="normal"/"inverse"` explícito según
  `ratio_snapshot.creates_value`.

**Decisión de alcance explícita, no un olvido:** el diseño se hizo para
un único tema (claro, "banca de inversión"), sin soporte de modo oscuro
manual de Streamlit — most viewers verán siempre el tema configurado en
`config.toml`; forzar "Dark" desde el menú de Streamlit podría chocar
con los colores fijos del CSS inyectado. Aceptable para el alcance de
esta herramienta; documentado aquí para que no se lea como un descuido.

**Verificado con Playwright contra la app real** (no solo con
`py_compile`): capturas de pantalla en las 4 pestañas, en modo universo
cacheado (AMZN) y modo cualquier ticker (NVDA) — sin errores de consola,
sin tracebacks, gráficos renderizando con los datos correctos. `plotly`
añadido y fijado en `requirements.txt` (7.0.0). 144 tests siguen en
verde (el cambio es puramente de presentación, ninguna lógica tocada).

## 11. Sesión 16 (continuación) — Cierre del rigor técnico restante (M2, M5, I2, I4)

El usuario pidió "que la herramienta sea perfecta"; ante la ambigüedad,
se le preguntó en qué eje concreto priorizar. Eligió: **rigor técnico
restante** (cerrar M2/M5, decidir I2/I4 explícitamente) frente a las
otras opciones (universo de comps, seguir con UX, probar la capa
generativa en vivo).

- **M5 (divisa de reporte) — corregido de verdad.** `market_snapshot()`
  en ambos proveedores expone `currency` (Alpha Vantage: `Currency` de
  OVERVIEW; yfinance: `financialCurrency` con fallback a `currency` —
  prioriza la divisa de los ESTADOS FINANCIEROS, no la de cotización,
  porque un ADR puede cotizar en USD con financials en otra divisa).
  `app/streamlit_app.py` bloquea (`st.error` + `st.stop()`, no solo
  avisa) si la divisa no es USD. **Verificado con un ticker real**: Toyota
  (`TM`) reporta `financialCurrency=JPY` de verdad — probado en la app,
  bloquea limpio, sin traceback. Durante la verificación se encontró y
  corrigió un problema de orden: el aviso de "WACC simplificado" se
  mostraba ANTES del bloqueo (sugiriendo un número usable) — la
  comprobación se movió a antes de calcular el WACC en modo "cualquier
  ticker". 4 tests de regresión nuevos.
- **M2 (`gordon_weight=0.8`) — investigado a fondo, se mantiene.** La
  hipótesis inicial era cambiarlo a 1.0 (Gordon puro, que es de hecho
  el default ya documentado a nivel de motor). Probado con datos reales
  de los 8 tickers piloto antes de decidir: pasar a 1.0 empeora Big Tech
  sustancialmente (AMZN -14.3pp, META -24.3pp) y **amplifica** la
  sobrevaloración de JNJ ya documentada (30.0%→35.0%) — blendear con un
  múltiplo de comparables amortigua la inestabilidad numérica de Gordon
  Growth puro cuando el spread WACC-g es estrecho, un riesgo real y ya
  verificado esta sesión. Se mantiene 0.8, con la razón documentada en
  `docs/AUDIT.md` (no como constante arbitraria, sino como blend
  deliberado entre dos riesgos: dependencia de comps de baja calidad
  vs. fragilidad de Gordon puro).
- **I2 e I4 — aceptados explícitamente como limitaciones**, no dejados
  pendientes. Ninguno de los dos tiene una fuente de datos gratuita que
  lo resuelva de verdad (tramos de opciones outstanding para TSM;
  taxonomía de industria de calidad para comps). Señalados ahora
  también en la propia UI (caption junto al stub period para I2,
  caption de la tabla de comparables para I4), no solo en la
  documentación.

**Con esto, todos los hallazgos no informativos de la auditoría de
sesión 15 tienen un estado cerrado** — corregidos con código o
decididos y documentados explícitamente. 4 tests de regresión nuevos.
**148 tests en total, todos en verde.** Verificado end-to-end con
Playwright contra la app real (TM bloqueado, AMZN sigue funcionando).

## 12. Sesión 16 (continuación) — "Que la herramienta sea perfecta": 4 mejoras de robustez

El usuario pidió seguir consolidando "hasta la mayor pulcritud" y que
analizara qué incluir. Se verificaron tres huecos reales (no
especulados) antes de proponerlos, y se ejecutaron los cuatro en
secuencia sin pausar entre uno y otro (petición explícita: "en cuanto
acabes y compruebes que está resuelto pasa al siguiente").

1. **I5 (nuevo hallazgo) — manejo de errores en modo "universo
   cacheado".** A diferencia de "cualquier ticker" (I3), el modo por
   defecto —el que usa cualquier visitante de la app ya pública, y
   comparte la cuota de 25 peticiones/día de Alpha Vantage entre
   todos— no tenía ningún `try/except`. Corregido con un mensaje
   específico para `AlphaVantageError` (menciona la cuota compartida) y
   uno genérico para cualquier otro fallo.
2. **CI en GitHub Actions** (`.github/workflows/tests.yml`): corre
   `pytest tests/ -v` en cada push/PR a `master`, Python 3.12, sin
   secrets (toda la suite es offline). Badge añadido al README.
3. **Tests automatizados de la app** (`tests/test_app.py`, cierra N2)
   con `streamlit.testing.v1.AppTest`. Dos detalles no obvios
   encontrados escribiéndolos, documentados en el propio archivo: (a)
   `AppTest` re-ejecuta el script completo en cada `.run()` -- parchear
   `app.streamlit_app.X` no sirve, hay que parchear en el módulo de
   ORIGEN (`engine.data_provider`/`engine.yfinance_provider`); (b)
   `@st.cache_data` sobrevive entre tests del mismo proceso -- sin
   limpiar el caché entre tests, un test exitoso anterior contamina los
   tests de fallo de API posteriores. 9 tests nuevos, cubren el camino
   feliz, el fix del delta "Crea valor" de esta sesión, I3, I5 y M5.
4. **TTL en el caché de universo** (`load_av_universe`/`load_yf_universe`,
   antes sin `ttl` -- vivían tanto como el proceso, potencialmente días
   en Streamlit Cloud). Añadido `ttl=3600`, consistente con
   `get_live_risk_free_rate()`.

De paso, limpiados los avisos de deprecación de Streamlit
(`use_container_width` → `width="stretch"`) que aparecieron en los
logs de los tests nuevos.

**157 tests en total, todos en verde** (148 + 9 de la app). Verificado
end-to-end con Playwright contra la app real corriendo (AMZN sigue
funcionando exactamente igual). `docs/AUDIT.md` actualizado: I5 y N2
añadidos/cerrados, contadores del resumen ejecutivo al día.

## 13. Sesión 17 — Rigor matemático: ancla robusta a outliers y elasticidades del modelo

El usuario pidió analizar cómo aumentar el rigor matemático del modelo
y de la confianza en lo que reporta — explícitamente NO acercar el
precio al mercado (ya descartado como objetivo en sesiones previas,
sería sobreajuste). Se propusieron 5 palancas (A: ancla robusta a
outliers, B: elasticidades/Greeks, C: Monte Carlo, D: backtesting, E:
intervalo de confianza sobre la desviación agregada); el usuario eligió
empezar por A+B.

**A — detector de outliers en el ancla del fade.** Primer diseño
(sustitución automática por la mediana cuando el último año es un
outlier estadístico, z-score modificado de Iglewicz & Hoaglin) parecía
correcto verificado solo con JNJ (z=21.3, sustituye 35.6%→19.1%), pero
la contraprueba con el universo piloto completo (`validate_universe.py`)
lo invalidó: disparaba en 7 de 8 tickers, incluido el CapEx de MSFT/META
(34.9%/34.7%) — el supercycle de IA ya verificado como real en sesiones
anteriores, no ruido. Con solo 2-3 años de referencia, un z-score no
distingue "ítem no recurrente" de "inicio de una tendencia real".
**Corregido a: aviso, nunca sustitución** — mismo patrón que
`MIN_PRUDENT_WACC_GROWTH_SPREAD`. No cambia ningún precio (verificado:
la desviación agregada vuelve exactamente a 41.82%/39.76%), solo añade
transparencia sobre qué años de partida son estadísticamente atípicos.
Documentado en detalle en `docs/METHODOLOGY.md` sección 21, incluido el
diseño descartado — el proceso de por qué no funcionaba es la evidencia
de rigor, no solo el resultado final.

**B — `engine/sensitivity.py`, elasticidades del modelo.**
`driver_sensitivities()` sistematiza el ejercicio que antes se hacía a
mano (desglosar el DCF para concluir "en MSFT domina el CapEx"): bump de
+1pp a la vez sobre WACC, g terminal, margen EBIT, D&A%, CapEx% y
crecimiento de ingresos, reprecio completo, ranking por impacto
absoluto. Verificado con datos reales: reproduce automáticamente el
mecanismo de AMZN (CapEx domina, -21.65%) y de PG/JNJ (WACC-g domina,
+42%) ya documentados, y añade un matiz nuevo no visto antes (en MSFT
domina g terminal/WACC por delante del CapEx, pese al mismo supercycle
— el valor terminal sigue pesando más con solo 5 años de horizonte).
Conectado a la app (nueva sección "Sensibilidad del precio a cada
supuesto" en la pestaña "Supuestos y expectativas", tornado chart
Plotly) y al memo (`MemoInput.sensitivities`, regla de la "Tesis de
Valoración" del prompt actualizada para nombrar el driver dominante).
Documentado en `docs/METHODOLOGY.md` sección 22.

**171 tests en total, todos en verde** (157 + 9 de test_sensitivity.py +
2 de test_memo_generator.py + 1 de test_app.py, con 2 ajustados de
test_projections.py). Verificado con AppTest que la nueva sección
renderiza sin excepción.

## 14. Sesión 17 (continuación) — Comparación contra banca de primer nivel: football field, comparables independientes, transparencia del valor terminal

El usuario pidió comparar el motor contra cómo bancos de primer nivel
(JP Morgan) presentan una valoración real. Revisión concreta de
`wacc_builder.py`, `DCFResult`, `comps.py` y datos crudos disponibles
(no una lista genérica). Confirmado que ya coincidimos con la práctica
profesional en varios puntos clave (reapalancar a estructura de capital
actual, crecimiento plano en el horizonte explícito, EBIT ya GAAP sin
añadir de vuelta el stock-based compensation — comprobado con el dato
real de AMZN, $19.5bn en 2025). Tres huecos reales cerrados:

1. **`engine.comps.comps_implied_share_price()`** — el múltiplo de
   peers, hasta ahora solo usado como input del valor terminal del DCF,
   ahora también se aplica directamente al EBITDA/ingresos actuales de
   la empresa como método de valoración independiente. Verificado con
   AMZN real: EV/EBITDA de peers implica $237 (mucho más cerca del
   mercado, $258.51, que el DCF conservador, ~$104) mientras EV/Revenue
   implica $631 — hallazgo real de que EV/Revenue se distorsiona entre
   peers de margen muy distinto (MSFT/GOOGL vs. AMZN), confirmando por
   qué EV/EBITDA es el múltiplo primario en la práctica bancaria.
2. **Football field chart** en la pestaña "Valoración": rango DCF +
   rango de comparables + rango de 52 semanas (nuevo campo
   `week_52_high`/`week_52_low` en ambos proveedores, sin coste
   adicional) con mercado/consenso como referencia — la vista que
   encabeza cualquier informe de equity research bancario.
3. **Desglose Gordon Growth vs. múltiplo de salida** en la UI —
   `DCFResult` ya calculaba ambos valores terminales por separado desde
   la Fase 2 pero nunca se mostraban. Verificado con JNJ real: Gordon
   Growth $1.00 billones vs. $792 mil millones del múltiplo de salida
   — brecha +26.4%, ahora visible sin desglosar el DCF a mano.

Transacciones precedentes (M&A comps) documentadas como limitación
permanente — no hay fuente gratuita de datos de transacciones M&A.

**175 tests en total, todos en verde.** Verificado además con datos
reales fuera de la suite (yfinance real sobre JNJ, el caso con spread
WACC-g más estrecho) para confirmar que no hay bugs de `None`/división
por cero no capturados por los fixtures sintéticos del AppTest.
Documentado en `docs/METHODOLOGY.md` sección 23.

## 15. Sesión 17 (continuación) — Cobertura de analistas, rango de consenso, tendencia histórica

Mismo hilo que la sección 14, con la misma disciplina: solo campos ya
disponibles sin coste adicional en Alpha Vantage/yfinance.

- **Cobertura de analistas**: desglose de recomendaciones por tramo
  (Alpha Vantage: `AnalystRatingStrongBuy/Buy/Hold/Sell/StrongSell`) o
  recomendación consenso + nº de analistas (yfinance). Verificado con
  AMZN real: 15 compra fuerte, 44 compra, 2 mantener, 0 venta —
  refuerza la narrativa del reverse DCF. Mostrado como tooltip del
  metric "Consenso analistas" ya existente.
- **Rango de precio objetivo** (yfinance: `targetLowPrice`/
  `targetHighPrice`) añadido como barra extra del football field
  cuando está disponible. JNJ real: $190–$320 frente a una media de
  $275.64.
- **Gráfico de tendencia histórica** al inicio de "Supuestos y
  expectativas": crecimiento de ingresos YoY + margen EBIT sobre todo
  el histórico disponible (no solo la ventana del fade) — contexto
  antes de la tabla de supuestos. Verificado con AMZN real (20 años de
  histórico): visualiza la misma historia de la sección 14, margen
  cerca de cero durante más de una década, expansión fuerte solo en
  los últimos 3 años.

**176 tests en total, todos en verde.** Verificado con datos reales de
AMZN y JNJ fuera de la suite. Documentado en `docs/METHODOLOGY.md`
sección 24.

## 16. Sesión 17 (continuación) — Auditoría matemática y financiera a fondo del motor

El usuario pidió, en vez de seguir añadiendo funciones, revisar en
profundidad lo que ya existe "a nivel técnico y matemático y de
análisis financiero y económico... acorde a los estándares de calidad
de bancos de primer nivel". Releídos `valuation.py`, `wacc_builder.py`,
`ratios.py`, `comps.py` completos, verificando cada fórmula contra
teoría financiera estándar y contra el propio Excel de referencia.

**Confirmado correcto** (no solo por lectura, con verificación
explícita): convención mid-year+stub del valor terminal (descuenta con
`n-0.5`, no `n` — evita un error clásico); Gordon Growth usa
`FCFF_n×(1+g)/(WACC-g)` (evita el off-by-one de omitir el `×(1+g)`);
WACC sin circularidad (usa market cap actual, no el equity value del
propio DCF); FCFF sin doble conteo del escudo fiscal; EBITDA consistente
en todo el pipeline (0.0% de diferencia verificado con 6 tickers
reales entre `ebit+d_and_a` y el `ebitda` reportado por el proveedor).

**Dos bugs reales corregidos** (I6, I7 en `docs/AUDIT.md`) — división
por cero sin proteger en `cost_of_debt()` (empresa sin deuda) y
`debt_to_ebitda()` (EBITDA nulo/negativo). El segundo era serio de
verdad: `ZeroDivisionError` no es subclase de `ValueError`, así que el
`except ValueError` ya existente en `app.py` no lo habría capturado —
hubiera sido un traceback real en producción. Ninguno de los 8 tickers
piloto lo dispara hoy, pero ambos son casos reales alcanzables.

**Dos puntos de diseño dejados abiertos a propósito** (M6, M7) —
tipo impositivo plano en el valor terminal a perpetuidad, y
Debt/EBITDA bruto sin aclarar en la etiqueta. Mismo estándar que M2:
pendientes de investigar con datos reales antes de decidir, no de
cambiar por intuición.

**180 tests en total, todos en verde.** Documentado en
`docs/METHODOLOGY.md` sección 25 y `docs/AUDIT.md` (hallazgos I6, I7,
M6, M7, resumen ejecutivo y prioridad recomendada actualizados).

## 17. Sesión 17 (continuación) — Cuota de Alpha Vantage agotada: investigación de SEC EDGAR, y un bug real más urgente encontrado por el camino

Se agotó la cuota diaria de Alpha Vantage. El usuario preguntó por
alternativas gratuitas, con una condición explícita: "es importante que
sea todo datos fiables". Investigado `data.sec.gov` (SEC EDGAR,
`companyfacts`/XBRL) como tercer proveedor, verificando con datos
reales antes de comprometerse a construirlo.

**Funciona bien para**: revenue, EBIT, net income, activos, equity,
activo/pasivo corriente, interés, caja — verificado con los 5 tickers
Big Tech, fidelidad muy alta o exacta. **No se pudo verificar con
confianza para D&A** (MSFT/GOOGL reportan D&A en 3+ líneas separadas
que, sumadas, no cuadran con Alpha Vantage ni con ninguna combinación
probada) — se dejó de intentar en vez de forzar una aproximación sin
verificar.

**El giro real**: al validar `total_debt` de AMZN entre SEC EDGAR, AV y
yfinance para decidir qué definición de "deuda" replicar, se descubrió
que **`yfinance_provider.py`'s `market_snapshot()` usaba
`ticker.info["totalDebt"]`, un campo NO fiable** — para AMZN daba
$251.6bn, frente a $153.0bn del propio `ticker.balance_sheet` de
yfinance (que coincide EXACTO con Alpha Vantage: deuda financiera +
obligaciones de leasing). `historical_financials()` del mismo módulo ya
usaba correctamente `.balance_sheet` — solo `market_snapshot()` tenía
el bug, nunca contrastado entre las dos rutas. **Corregido (I8,
`docs/AUDIT.md`)**: ahora ambas funciones usan la misma fuente fiable.
Efecto real medido en KO/PG/JNJ: pequeño (ninguno tiene tanto leasing
como AMZN), pero en modo "cualquier ticker" con una empresa intensiva
en leasing habría sido un error de hasta el 65% en la cifra de deuda
usada para el WACC.

**182 tests en total, todos en verde.** SEC EDGAR queda en pausa (no
descartado) — el usuario priorizó corregir I8 antes de retomarlo.
Documentado en `docs/METHODOLOGY.md` sección 26 y `docs/AUDIT.md`
hallazgo I8.

## 18. Próximo paso inmediato

0. M6/M7 (recién abiertos) — investigar el tipo impositivo a largo
   plazo y añadir/aclarar Net Debt/EBITDA, con el mismo rigor que M2.
0.5. SEC EDGAR — retomar si se quiere reducir la dependencia de la
   cuota de Alpha Vantage: falta D&A fiable (sin resolver) y construir
   el módulo completo con lo ya validado para el resto de campos.
1. C — Monte Carlo: bandas de confianza probabilísticas (P10/P50/P90)
   sobre el precio implícito, muestreando WACC/margen/CapEx desde su
   propia varianza histórica. La palanca de mayor rigor que queda de las
   5 propuestas en la sesión 17; construida sobre el motor actual sin
   tocar supuestos por defecto.
2. D — Backtesting walk-forward: valor muy alto si es viable, pero
   pendiente de investigar si Alpha Vantage/yfinance free tier dan
   fundamentales histórico punto-en-el-tiempo (riesgo de restatements).
3. E — Intervalo de confianza (bootstrap) sobre la desviación agregada
   del universo piloto (n=8) — complementa a C, bajo esfuerzo.
4. Fase 9 (sentiment en earnings calls) — extensión opcional.
5. Probar la capa generativa con una llamada real — aparcado por ahora
   a petición del usuario, no es una prioridad activa.
6. Universo de comparables más amplio/mejor segmentado — se evaluó como
   opción de "rigor técnico" pero no se priorizó frente a lo demás;
   sigue disponible como línea futura si se retoma.
