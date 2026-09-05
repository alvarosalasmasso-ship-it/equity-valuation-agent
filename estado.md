# Estado del proyecto — Agente de valoración DCF

> Documento vivo. Actualízalo al final de cada sesión de trabajo con lo
> avanzado, las decisiones tomadas y el siguiente paso concreto. Es la
> primera lectura al retomar el proyecto.

**Última actualización:** 2026-09-05 (sesión 9)

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
| 7 — Validación vs consenso de analistas | ✅ Hecho | `engine/validation.py`. Resultado real sobre los 5 tickers piloto: desviación media absoluta 54.7% vs. mercado, 53.3% vs. consenso — ver detalle abajo, causa raíz identificada (no bugs) |
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
| AAPL | 8.77% | $143.94 | $319.97 | $323.86 | -55.0% | -55.6% |

**Desviación media absoluta: 54.7% vs. mercado, 53.3% vs. consenso.**

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
  alvarosalasmasso@gmail.com), no en la config global de git. Sin remoto
  todavía — el push a GitHub es la Fase 8 del plan.
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

## 5. Próximo paso inmediato

El motor está validado en seis capas independientes y la capa generativa
está lista salvo la llamada real (falta `ANTHROPIC_API_KEY`). yfinance
funciona como fuente alternativa sin límite de cuota. Opciones para la
próxima sesión, de más a menos prioritaria:

1. **Conseguir una API key de Anthropic** y probar `generate_memo()` en
   vivo con el caso AMZN ya preparado (o cualquier otro de los 8 tickers
   cacheados) — es la única pieza de la Fase 5 que falta por ejecutar de
   verdad, todo lo demás ya está construido y testeado.
2. Esqueleto de Streamlit (Fase 6) usando datos ya cacheados (AMZN,
   MSFT, GOOGL, META, AAPL, KO, PG, JNJ) — mostrando el rango de
   escenarios + el mecanismo de desviación detectado + (si hay key) el
   memo generado, no un número suelto. No requiere ninguna API nueva
   para desarrollarse, solo para la parte del memo.
3. Opcional, bajo interés: usar `yfinance_provider.py` como fuente
   primaria en vez de Alpha Vantage para no depender de ninguna cuota
   diaria — a cambio de menos años de histórico (4 vs. 15-20).

**Ya no aplica el veto anterior a Streamlit/LLM** ("no construir la
interfaz antes de validar") — la Fase 7 y su contraprueba ya están
medidas y explicadas. Sigue aplicando el principio de fondo: cualquier UI
debe mostrar el número junto a su explicación (supuestos, mecanismo de
desviación detectado, rango de escenarios), nunca el número solo.
