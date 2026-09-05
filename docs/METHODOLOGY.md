# Metodología financiera

Fuente de verdad: `Advanced DCF.xlsx` (modelo profesional de valoración de
Amazon, elaborado por un ex-banquero de M&A/ECM de JP Morgan). Este
documento mapea cada fórmula del Excel a la función Python que la
implementa en `engine/valuation.py`, y documenta las decisiones de
simplificación tomadas para generalizar el modelo a un ticker arbitrario.

## 1. Arquitectura del Excel de referencia

El workbook tiene 15 hojas divididas en tres bloques:

- **Datos históricos** (`IS`, `CFS`, `CapEx`, `Segments`): cuenta de
  resultados, cash flow y capex históricos de Amazon, feed de FactSet.
- **Operating Model**: motor de proyección. Convierte los históricos en
  10 años de proyección (Revenue, EBIT, D&A, CapEx, ΔNWC) por segmento de
  negocio, mediante supuestos de % crecimiento / % sobre ventas.
- **DCF** (`Consolidated`, `North America`, `International`, `AWS`,
  `WACC`, `Shares`, `Comps`): motor de valoración propiamente dicho. Es
  un **DCF por suma de partes**: cada segmento tiene su propio DCF
  completo (UFCF, descuento, valor terminal) y `Consolidated` sencillamente
  suma los tres.

## 2. Decisión de alcance: por qué el motor Python NO segmenta

El Excel calcula un DCF independiente para North America, International y
AWS porque Amazon reporta esos tres segmentos por separado y el analista
tenía datos suficientes para modelarlos de forma diferenciada (AWS con
mayor margen y múltiplo, NA maduro y de bajo crecimiento, etc.).

Para un agente que valora **cualquier ticker** a partir de Alpha
Vantage/yfinance, no disponemos de ese desglose por segmento de forma
sistemática. La decisión de diseño es: **replicar exactamente la misma
lógica de fórmulas, pero a nivel de empresa consolidada**, tratando a la
compañía como un único "segmento".

Esto no es una pérdida de rigor: se comprobó que, cuando el modelo real
aplica el mismo peso Gordon/múltiplo de salida y el mismo múltiplo
EV/EBITDA a los tres segmentos (que es el caso en este Excel), el
resultado consolidado con la fórmula "whole-company" reproduce el precio
objetivo real de forma **exacta** (ver `tests/test_valuation.py::test_full_dcf_blended_terminal_value_matches_excel_exactly`,
$216.41/acción, igual a `Consolidated!K47`). Es una consecuencia de que
el blend Gordon/múltiplo es lineal: `blend(Σ segmentos) = Σ blend(segmento)`
cuando peso y múltiplo son uniformes.

## 3. Mapeo fórmula Excel -> función Python

### WACC / CAPM (`WACC!*` -> `unlever_beta`, `relever_beta`, `cost_of_equity`, `cost_of_debt`, `wacc`)

```
Beta desapalancada de cada comparable:
  β_u = β_l / (1 + (1 - t) × (Deuda neta / Market Cap))          WACC!I28:I30

Beta desapalancada media del sector:
  β_u_avg = AVERAGE(comparables)                                  WACC!E32

Beta re-apalancada para la empresa objetivo:
  β_l = β_u_avg × (1 + (1 - t) × (Deuda neta empresa / Market Cap empresa))   WACC!E33

Cost of Equity (CAPM):
  Re = Rf + β_l × MRP                                             WACC!F10

Cost of Debt:
  Rd = Gasto financiero anualizado / Deuda total                  WACC!F17

WACC:
  WACC = %E × Re + %D × Rd × (1 - t)                               WACC!F22
```

Nota: el modelo real usa 3 comparables fijos (AAPL, MSFT, GOOGL). En el
agente generalizado, los comparables deben venir de un set de peers del
mismo sector que el ticker analizado (Fase 3, `engine/comps.py`).

### Acciones diluidas — Treasury Stock Method (`Shares!*` -> `treasury_stock_method`, `diluted_shares_outstanding`)

```
Por cada tramo de opciones "in the money" (strike < precio actual):
  Acciones dilutivas = SUM(tramos in the money)                   Shares!E30
  Recaudado = SUM(acciones × strike, tramos in the money)         Shares!E9
  Acciones recompradas = Recaudado / Precio actual                Shares!E10
  Opciones netas dilutivas = Acciones dilutivas - Acciones recompradas   Shares!E11

Acciones diluidas totales = Básicas + Opciones netas dilutivas
                             + Otros valores dilutivos (convertibles, RSUs)   Shares!E14
```

### Flujo de caja libre desapalancado (`Consolidated!F32` -> `unlevered_fcf`)

```
UFCF = EBIT × (1 - t) + D&A - CapEx - ΔNWC
```

Idéntico a la fórmula del blueprint original (sección 2). Confirmado
exacto contra el Excel real.

### Descuento — convención stub + mid-year (`Consolidated!F35:K36` -> `discount_periods`, `pv_of_cash_flows`)

El modelo NO descuenta con periodos enteros (1, 2, 3...) sino con
**convención de mitad de año** (asume que el caja se genera de forma
uniforme durante el año, no toda al cierre), y ajusta el primer año por
un **stub period** (fracción de año que queda entre la fecha de
valoración y el cierre del primer ejercicio fiscal proyectado):

```
periodo_1 = stub / 2
periodo_2 = stub + 0.5
periodo_n = periodo_(n-1) + 1                    para n > 2

PV(flujo_1) = (flujo_1 × stub) / (1 + WACC)^periodo_1
PV(flujo_n) = flujo_n / (1 + WACC)^periodo_n       para n > 1
```

### Valor terminal (`North America!E45:K53` -> `gordon_growth_terminal_value`, `exit_multiple_terminal_value`, `blended_terminal_value`)

El modelo real combina dos métodos:

```
TV Gordon Growth = FCFF_n × (1+g) / (WACC - g)

TV Múltiplo de salida = EBITDA_terminal × Múltiplo EV/EBITDA (de Comps)

TV combinado = peso × TV_Gordon + (1 - peso) × TV_Múltiplo      (peso = 80% en NA)
```

Por defecto (`gordon_weight=1.0`) el motor usa Gordon Growth puro, que es
la metodología descrita en el blueprint original y no requiere un set de
comparables. El modo combinado es una extensión opcional cuando sí hay
comparables disponibles (Fase 3).

### Enterprise Value -> Equity Value -> Precio objetivo

```
Enterprise Value = Σ PV(UFCF) + PV(Terminal Value)
Equity Value = Enterprise Value + Caja - Deuda total
Precio objetivo = Equity Value / Acciones diluidas
```

## 4. Validación

Todos los targets de `tests/test_valuation.py` son valores calculados por
el propio Excel (extraídos con `openpyxl`, `data_only=True`), no cifras
inventadas. Esto cumple la Fase 4 del blueprint: "validar el motor contra
un caso conocido a mano" — aquí el caso conocido es el propio modelo
profesional de referencia.

## 5. Motor de proyección (`engine/projections.py`) — Fase 3

El Excel resuelve la proyección con el "Operating Model": supuestos de
% crecimiento / % sobre ventas fijados a mano por el analista, por
segmento. Para un ticker arbitrario no hay un analista fijándolos a
mano, así que `default_assumptions_from_history()` los deriva del propio
histórico con un único mecanismo aplicado a TODOS los drivers
(crecimiento, margen EBIT, D&A, CapEx, ΔNWC): un **fade lineal** desde
un valor de "año 1" hasta un valor de "año N", a lo largo del horizonte
de proyección (`FadeAssumption`, con `start == end` como caso particular
de driver plano).

Por defecto:
- **Año 1** = valor real del **último ejercicio fiscal reportado** (mejor
  estimador disponible del estado actual de la compañía). Excepción: el
  crecimiento de ingresos usa el CAGR de los últimos `lookback_years`
  años en vez del crecimiento de un único año suelto, más ruidoso.
- **Año N** = media de los últimos `lookback_years` años (estimador del
  estado "normalizado" de largo plazo) para márgenes/CapEx/ΔNWC; la tasa
  de crecimiento terminal proporcionada por el usuario (ligada a
  crecimiento nominal de largo plazo, no derivada del histórico) para
  ingresos.
- El tipo impositivo se mantiene plano (fade de tipo impositivo no es
  práctica estándar; converger al tipo estatutario sería el refinamiento
  natural, no implementado).

Esto es **reversión a la media** estándar en DCF (Damodaran: toda
empresa converge con el tiempo a métricas de industria/largo plazo, no
mantiene su estado actual a perpetuidad) — y es una decisión de
modelado deliberada, no la única opción válida (ver más abajo).

Dos ventanas de datos separadas a propósito en el código: el CAGR de
ingresos necesita `lookback_years + 1` puntos (los extremos del
periodo), pero la ventana de márgenes usa exactamente `lookback_years`
puntos — mezclarlas colaría un año adicional, más antiguo, en la media
(bug real encontrado y corregido durante el desarrollo, ver
`tests/test_projections.py::test_default_assumptions_margin_window_excludes_extra_older_year`).

### Validación de punta a punta con datos reales (AMZN) y su interpretación correcta

Al correr el pipeline completo (`historical_financials` ->
`default_assumptions_from_history` -> `project_financials` -> `build_wacc`
-> `run_dcf`) con datos reales de Amazon, el precio implícito (~$55-85,
según ventana de lookback) queda por debajo del precio de mercado
(~$258) y del consenso de analistas (~$328).

Esto **NO es un bug** — el motor de valoración, el proveedor de datos y
ahora también el fade de márgenes y la reconstrucción de WACC vía
comparables están validados exactos (ver `tests/`). Es la consecuencia
esperada de una elección de modelado explícita y defendible:

**Nuestro motor asume reversión a la media** (el margen EBIT actual de
Amazon, ~14%, converge hacia su propio promedio histórico de varios años,
~8-10%, no se extrapola ni se supera). **El mercado/consenso de analistas
está pagando por continuación de tendencia** (apuesta a que el margen
sigue expandiéndose más allá del nivel actual, impulsado por AWS y
publicidad). Ambas son posturas legítimas de un analista; no hay una
"correcta" universal — por eso `ProjectionAssumptions` expone
`ebit_margin`, `capex_pct_revenue`, etc. como `FadeAssumption(start, end)`
explícitos y sobreescribibles: un analista con tesis alcista puede fijar
`end` por encima de `start` (margen que sigue mejorando) en vez de
aceptar el valor por defecto conservador.

**Deliberadamente no se ha ajustado el valor por defecto para que
"cuadre" con el precio de mercado** — sería sobreajustar el modelo a un
caso conocido, justo lo que el principio de rigor de este proyecto
quiere poder demostrar que no hace. El motor por defecto es
intencionadamente conservador (sesgo hacia reversión a la media, no
hacia extrapolar el hype), y la brecha resultante frente al consenso en
compañías con una historia de crecimiento fuerte es información legítima
del propio análisis, no un fallo a esconder — exactamente el tipo de
matiz que la Fase 7 (validación sistemática contra consenso, sobre los 5
tickers piloto) debe cuantificar y que la futura interfaz debe mostrar
junto al número, no en vez de él.

### Matriz de sensibilidad (`sensitivity_matrix` en `engine/valuation.py`)

Réplica de la Data Table del Excel (`Consolidated!N51:S57`): corre
`run_dcf` para cada combinación de WACC (filas) × tasa de crecimiento
terminal g (columnas), variando solo esos dos parámetros. Es una
función pura sobre `DCFInputs` (usa `dataclasses.replace`, no duplica la
matemática del DCF) — mismo principio de una sola fuente de verdad que
`wacc_builder.py` (ver sección 6).

### Reconstrucción de WACC vía comparables (`engine/wacc_builder.py`)

Orquesta `unlever_beta` -> media de industria -> `relever_beta` ->
`cost_of_equity` -> `cost_of_debt` -> `wacc`, exactamente como la hoja
`WACC` del Excel, pero para un ticker y un set de comparables
arbitrarios (no solo AMZN/AAPL/MSFT/GOOGL). Reutiliza las funciones de
`engine/valuation.py` tal cual — no reimplementa la matemática, así que
un cambio en la fórmula de CAPM/WACC solo se hace en un sitio.
Validado exacto contra el mismo caso AMZN/Excel que `test_valuation.py`
(`tests/test_wacc_builder.py`).

## 6. Tabla de comparables y ratios — Fase 3

- `engine/comps.py`: `build_comps_table()` agrega varios
  `market_snapshot()` en una tabla indexada por ticker;
  `peer_average_multiple()` calcula la media/mediana de un múltiplo entre
  peers, excluyendo opcionalmente el ticker objetivo — pensado para
  alimentar `exit_multiple_terminal_value()` con el múltiplo de mercado
  de los comparables en vez del múltiplo de la propia empresa (que puede
  estar ya sobre/infravalorado).
- `engine/ratios.py`: ROE por Dupont, ROIC vs. WACC (creación de valor),
  Debt/EBITDA, cobertura de intereses, current ratio — tabla de "Ratios y
  comparables" del blueprint sección 2, implementada como funciones
  puras sobre números sueltos (no DataFrames) para facilidad de testeo.

## 7. Fase 7 — Validación contra consenso, universo piloto completo

`engine/validation.py` productiviza la comprobación manual de la sesión
anterior: `value_ticker()` corre el pipeline completo (WACC vía
comparables -> proyección con fade -> DCF) para un ticker usando el
RESTO del universo como comparables (mismo momento temporal para todos,
sin la desalineación de usar constantes congeladas del Excel);
`validate_universe()` lo repite para cada ticker del universo;
`summarize_deviation()` agrega la desviación media/mediana absoluta
frente a precio de mercado y consenso de analistas.

### Resultado real, universo piloto (AMZN, MSFT, GOOGL, META, AAPL)

| Ticker | WACC | Precio implícito | Mercado | Consenso analistas | Desv. vs mercado | Desv. vs consenso |
|---|---|---|---|---|---|---|
| AMZN | 8.27% | $84.82 | $258.51 | $328.17 | -67.2% | -74.2% |
| MSFT | 8.78% | $295.80 | $499.70 | $572.92 | -40.8% | -48.4% |
| GOOGL | 8.68% | $270.02 | $705.51 | $428.07 | -61.7% | -36.9% |
| META | 8.47% | $365.03 | $712.53 | $754.77 | -48.8% | -51.6% |
| AAPL | 8.77% | $143.94 | $319.97 | $323.86 | -55.0% | -55.6% |

**Desviación media absoluta: 54.7% vs. mercado, 53.3% vs. consenso de
analistas** (mediana 55.0% / 51.6%). Parámetros: `n_years=5`,
`terminal_growth_rate=2.5%`, `lookback_years=3`, `gordon_weight=0.8`
(80% Gordon Growth / 20% múltiplo de salida).

### Diagnóstico: causa raíz identificada, no una lista de bugs

Que las 5 compañías —independientes entre sí— salgan infravaloradas en
una magnitud similar (41%-67%) es una señal de un factor sistemático
compartido, no de 5 "historias de crecimiento" que casualmente
coinciden. Se investigó con un desglose completo del DCF de MSFT
(reproducible, no solo afirmado):

**El CapEx actual de estas compañías, como % de ventas, es
extraordinario y muy superior a su D&A** (MSFT: CapEx = 34.9% de ventas
en el último ejercicio fiscal frente a D&A = 11.6% — CapEx consume el
63% del cash flow operativo). Es el gasto real reportado en
`CASH_FLOW.capitalExpenditures` (verificado contra el JSON crudo de
Alpha Vantage, no un artefacto de cálculo), consistente con el
supercycle de inversión en infraestructura de IA (datacenters, chips)
que estas compañías están ejecutando ahora mismo.

Un UFCF = EBIT×(1-t) + D&A - CapEx - ΔNWC con un CapEx de esa magnitud
absorbe la mayor parte del EBIT, y aunque el fade lo reduce hacia el
promedio histórico (menor, pre-supercycle) a lo largo del horizonte de
proyección, el efecto sobre el valor presente del flujo de los primeros
años —y sobre todo sobre el valor terminal Gordon Growth, que parte del
UFCF del año 5— es enorme.

**Esto es la misma conclusión metodológica de la sección 5, ahora
confirmada de forma sistemática en 5 compañías independientes, no
anecdótica en una sola:** el mercado está pagando hoy por la
productividad futura de ese CapEx (más ingresos/EBIT en años más allá
del horizonte de 5 años, o márgenes que siguen expandiéndose en vez de
revertir a la media). Un motor conservador, sin extrapolar ese retorno
futuro no verificado, no puede — ni debe — reproducir ese precio. Esa
es precisamente la función de la Fase 7: cuantificar la brecha con
rigor, no maquillarla.

**Esto no es un resultado "malo" para el proyecto — es el resultado
correcto de una herramienta rigurosa aplicada a un momento de mercado
donde el consenso está pagando una prima considerable por crecimiento
no garantizado.** El valor demostrable para un entrevistador de banca
no es "el modelo replica el precio de mercado" (cualquier calculadora
de múltiplos hace eso por construcción) sino "el modelo cuantifica
*cuánta* prima de crecimiento no verificado está pagando el mercado, y
explica mecánicamente de dónde viene esa prima" (CapEx muy por encima
de D&A, en este caso concreto).

**Uso previsto en el CV** (blueprint sección 4, viñeta 2): "...validado
frente a 5 empresas del sector Big Tech/Cloud con una desviación media
del 53% frente al consenso de mercado, explicada por un motor
conservador (reversión a la media) frente al actual supercycle de CapEx
en infraestructura de IA no descontado de forma determinista" — una
cifra real medida, con mecanismo explicado, no un porcentaje inventado.

### Contraprueba: ¿el motor funciona mejor en empresas maduras?

La hipótesis directa de todo lo anterior es que un motor de reversión a
la media debería comportarse mucho mejor en negocios estables/maduros
(bajo CapEx de reinversión, márgenes ya asentados) que en historias de
hiper-crecimiento como las 5 anteriores. Se probó con Coca-Cola (KO) —
CapEx = 4.4% de ventas frente a D&A = 2.2% (vs. MSFT: CapEx 34.9% frente
a D&A 11.6%), el contraste casi perfecto para la prueba:

| Ticker | Lookback | Implícito | Mercado | Consenso |
|---|---|---|---|---|
| KO | 3 años | $83.13 | $88.07 (**-5.6%**) | $94.70 (**-12.2%**) |
| KO | 5 años | $110.67 | $88.07 (**+25.7%**) | $94.70 (**+16.9%**) |

Desviación de un dígito a ~20%, un orden de magnitud por debajo del
41%-67% de Big Tech. **Confirma la hipótesis con evidencia, no solo con
argumento teórico**: el motor conservador por defecto es fiable
precisamente donde la teoría dice que debe serlo (negocios maduros de
bajo CapEx de reinversión), y diverge mucho donde también predice que
debe divergir (hiper-crecimiento con CapEx >> D&A) — comportamiento
consistente en ambos extremos, no un motor que simplemente "da números
bajos siempre".

**Limitaciones de esta contraprueba, explícitas:**
- Un solo dato (KO), no los 3 planeados (KO + Procter & Gamble +
  Johnson & Johnson) — la cuota gratuita de Alpha Vantage (25
  peticiones/día) se agotó al intentar el segundo ticker (P&G), fallo
  limpio sin datos corruptos. Pendiente para cuando la cuota se resetee.
- WACC simplificado con el beta propio de KO (`cost_of_equity` +
  `cost_of_debt` + `wacc` directos), no la reconstrucción vía
  comparables de `wacc_builder.py` — no había un set de comparables del
  mismo sector (consumo defensivo) descargado. No afecta a la conclusión
  sobre el motor de proyección, que es lo que esta contraprueba mide.
- La brecha entre lookback=3 y lookback=5 (de -5.6% a +25.7% frente a
  mercado) muestra que el resultado sigue siendo sensible a la ventana
  de histórico elegida, incluso en una empresa estable — otro recordatorio
  de que el número es función de supuestos explícitos, no una verdad fija.

## 8. Proveedor de datos alternativo: `engine/yfinance_provider.py`

Alpha Vantage limita a 25 peticiones/día en el free tier — insuficiente
para iterar con soltura. `yfinance` no tiene ese límite (a cambio de
solo ~4 años de histórico anual, frente a los 15-20 de Alpha Vantage).
`yfinance_provider.py` normaliza a **exactamente el mismo esquema** de
columnas/claves que `data_provider.py` (`historical_financials` /
`market_snapshot`), así que es intercambiable en `projections.py`,
`wacc_builder.py` y `validation.py` sin tocar esos módulos — mismo
principio de una sola metodología con proveedores de datos
intercambiables que ya usa `wacc_builder.py` con `valuation.py`.

Diferencias de convención manejadas explícitamente: yfinance reporta
CapEx como salida de caja (negativo) — se toma valor absoluto para
igualar la convención de `data_provider.py`. Validado con datos reales
(KO, PG, JNJ): mismo precio y beta que Alpha Vantage para KO
($88.07 / 0.342 en ambos), confirmando que ambas fuentes parten de los
mismos datos de mercado subyacentes.

## 9. Contraprueba de empresas maduras completada: WACC bajo y la inestabilidad de Gordon Growth

Con `yfinance_provider.py` se completó la contraprueba pendiente de la
sección 7 (KO + Procter & Gamble + Johnson & Johnson, WACC vía
comparables real entre las tres, no simplificado):

| Ticker | WACC | Implícito | Mercado | Consenso | Desv. mercado | Desv. consenso |
|---|---|---|---|---|---|---|
| KO | 4.95% | $90.55 | $88.07 | $94.70 | **+2.8%** | -4.4% |
| PG | 4.80% | $271.16 | $146.44 | $160.61 | **+85.2%** | +68.8% |
| JNJ | 5.08% | $418.26 | $275.23 | $275.64 | **+52.0%** | +51.7% |

KO reproduce el mercado casi exacto. **PG y JNJ salen SOBREvaloradas por
el motor — la dirección opuesta al patrón de Big Tech.** Se investigó
antes de documentar (mismo estándar que con MSFT):

### Mecanismo identificado: margen WACC-g estrecho, no un problema de márgenes

Para PG, el margen EBIT no tiene ninguna anomalía (24.4% -> 24.3%,
prácticamente plano) y el UFCF proyectado es estable (~$17-18bn/año). El
problema está en el valor terminal: **Gordon Growth da $793.6bn frente a
$399.9bn del múltiplo de salida — casi el doble** — y Gordon pesa 80% en
el blend. Causa: WACC = 4.80% (beta muy bajo, 0.377, consumo defensivo)
con tasa de crecimiento terminal fija en 2.5% deja un margen WACC-g de
solo 2.3%. La fórmula de Gordon Growth divide por ese margen — cuanto
más estrecho, más se amplifica el resultado. Es una **inestabilidad
matemática conocida del modelo de Gordon Growth**, no un fallo de nuestra
implementación ni de los datos: cualquier DCF académico advierte de la
alta sensibilidad de la perpetuidad cuando WACC y g están próximos.

JNJ combina este mismo efecto (spread 2.58%) con un segundo problema
independiente: **el margen EBIT del último ejercicio (35.6%) es un
outlier frente a los 3 años previos (~19-25%)** — casi con certeza un
ítem no recurrente (ganancia por desinversión, reversión de litigio;
JNJ escindió Kenvue en 2023, consistente con movimientos contables
grandes y puntuales en esos años). Nuestra metodología usa el último año
real como ancla del fade (`start`), así que hereda ese outlier
directamente en el supuesto de proyección — no un bug, pero sí una
vulnerabilidad real y ahora evidenciada de "usar el último año como
estado actual" cuando ese año tiene ruido no operativo.

**Por qué KO no muestra el mismo problema con un spread igual de
estrecho (2.45%):** KO cotiza a un múltiplo EV/EBITDA de mercado mucho
más rico (24.06x) que PG (14.8x) o JNJ (19.8x). Gordon Growth se infla de
forma parecida en los tres casos por el spread estrecho, pero en KO esa
cifra inflada resulta que coincide, más o menos, con lo que ya implica su
múltiplo de mercado alto — en PG/JNJ, con múltiplos más bajos, la
distancia entre ambos métodos queda expuesta y domina el blended al 80%.

### Corrección aplicada: aviso, no un ajuste silencioso

Se añadió `MIN_PRUDENT_WACC_GROWTH_SPREAD = 0.03` en `engine/valuation.py`:
`gordon_growth_terminal_value()` emite un `warnings.warn` (no bloquea el
cálculo, no cambia el resultado) cuando WACC-g queda por debajo de ese
umbral, señalando la inestabilidad y sugiriendo revisar
`terminal_growth_rate` o `gordon_weight`. Es una regla de pulgar de la
industria (documentada como tal, no una ley exacta), y **deliberadamente
no se ha bajado `gordon_weight` por defecto ni ajustado la tasa terminal
para "arreglar" el caso PG/JNJ** — sería la misma sobreajuste que el
proyecto ha evitado en cada hallazgo anterior. El usuario que vea el aviso
decide con criterio, igual que un analista humano ajustaría el peso del
múltiplo de salida al ver una perpetuidad que dobla al múltiplo de
mercado.

**Conclusión honesta de la contraprueba completa:** el motor por defecto
no es uniformemente "conservador" ni uniformemente "fiable en empresas
maduras" — es fiable cuando el spread WACC-g es saludable y el histórico
reciente no tiene ítems no recurrentes (KO), e inestable cuando cualquiera
de esas dos condiciones falla (PG, JNJ), independientemente de si la
compañía es "madura" o "de crecimiento". Ambos mecanismos (CapEx >> D&A
en Big Tech; spread WACC-g estrecho + outliers de un año en staples de
bajo beta) están ahora identificados, verificados con desgloses completos
y explicados con precisión — el objetivo de un motor riguroso no es "dar
siempre el número correcto" sino "saber exactamente por qué da lo que da".

## 10. Escenarios explícitos (`engine/scenarios.py`)

Ni "conservador" ni "agresivo" describen bien el comportamiento real del
motor (secciones 7-9: el mismo motor sale infravalorado en Big Tech y
sobrevalorado en algunas maduras, por mecanismos distintos). En vez de
intentar una heurística única "mejor calibrada", `scenarios.py` hace la
ambigüedad explícita: ofrece 2-3 lecturas alternativas del mismo
histórico, todas derivadas de datos reales, ninguna inventada:

- **Conservador** — el valor por defecto de `default_assumptions_from_history()`:
  cada driver revierte a su media histórica.
- **Mantener nivel actual** — margen, D&A, CapEx y ΔNWC se congelan en
  el valor real del último ejercicio fiscal (ni reversión ni mejora).
- **Alcista** — el margen EBIT extrapola hacia delante la MISMA
  magnitud de mejora que ya se observó frente a su media histórica (no
  una cifra arbitraria); D&A/CapEx/ΔNWC se mantienen en su nivel actual.

### Hallazgo no intuitivo, verificado con AMZN (datos ya cacheados, sin API nueva)

```
Conservador (reversión a la media)          -> $84.82
Mantener nivel actual                       -> $71.04   <- por debajo del conservador
Alcista (continúa la tendencia reciente)    -> $107.09
```

"Mantener nivel actual" da un precio MENOR que "conservador", pese a
partir de un margen más alto. Investigado (mismo estándar que siempre):
AMZN tiene el CapEx actual en pico (18.4% de ventas, supercycle de IA,
sección 7) frente a un promedio histórico de 13.5%. El escenario
conservador deja que el CapEx revierta a la baja (13.5%) igual que hace
con el margen; "mantener nivel actual" congela el CapEx en su pico
(18.4%) durante los 5 años, igual que congela el margen. El lastre de
CapEx elevado pesa más que la mejora de margen — el motor revela así
**cuál de los dos supuestos domina realmente la sensibilidad del valor
en esta compañía concreta**, algo que un único número nunca hubiera
comunicado. No se ha "corregido" el orden para que parezca más
intuitivo (bear < base < bull) — sería ocultar información real detrás
de una expectativa estética.

## 11. Capa generativa — Fase 5 (`ai/memo_generator.py`)

Principio de arquitectura del proyecto, ahora implementado: **el LLM
nunca calcula, solo redacta** a partir de un paquete de datos ya cerrado
(`MemoInput`). Todo lo que puede aparecer en el memo está ya en ese
paquete; el prompt (`ai/prompts/investment_memo_system.md`) prohíbe
explícitamente inventar, calcular o estimar cualquier cifra que no esté
ahí, y obliga a decir "no disponible" en vez de omitir un dato en
silencio.

`MemoInput` incluye: ticker, precio de mercado, consenso de analistas,
WACC, los 2-3 escenarios de `engine.scenarios` (bear/hold/bull),
desviación vs. mercado/consenso del escenario conservador, supuestos
clave de proyección, y **los avisos técnicos del propio modelo**
(`warnings.warn` de `MIN_PRUDENT_WACC_GROWTH_SPREAD`, capturados por
`run_scenarios_capturing_warnings()`) — el memo debe explicar esos
avisos como riesgo real del cálculo, no suavizarlos.

**Diseño para ser testeable sin clave de Anthropic:** `generate_memo()`
acepta un `client` inyectable (cualquier objeto con
`.messages.create(...)`), así que `build_memo_input()` y `build_prompt()`
(las partes deterministas, sin red) tienen cobertura completa de tests,
y `generate_memo()` se testea con un cliente falso que no llama a la
API real. Solo falta una `ANTHROPIC_API_KEY` en `.env` para probar la
llamada real — el resto del pipeline (empaquetado + prompt) está
verificado de punta a punta con datos reales de AMZN (ver
`tests/test_memo_generator.py` y el dry-run documentado en `estado.md`).

**Decisión de producto (sesión de generación del memo):** en vez de
configurar la API de pago para esta fase de desarrollo, se generó un
memo real manualmente (el propio Claude, en la sesión de Claude Code,
siguiendo exactamente `ai/prompts/investment_memo_system.md` sobre el
paquete de datos de AMZN) — cero coste, y sirve como validación
cualitativa de que el prompt produce la estructura y el rigor
esperados antes de gastar nada en la API real. `app/streamlit_app.py`
reproduce este mismo flujo: sin `ANTHROPIC_API_KEY` configurada, muestra
el prompt listo para copiar y pegar en Claude.ai en vez de bloquear la
funcionalidad.

## 12. Interfaz — Fase 6 (`app/streamlit_app.py`)

Capa de presentación pura: no calcula nada, orquesta llamadas a
`engine/` y `ai/` ya validadas. Dos modos de datos:

- **Universo cacheado** (`AMZN/MSFT/GOOGL/META/AAPL` vía Alpha Vantage,
  `KO/PG/JNJ` vía yfinance) — WACC riguroso vía comparables reales del
  propio grupo (`engine.validation.build_peer_set` + `engine.wacc_builder.build_wacc`,
  sin reimplementar la construcción de peers).
- **Cualquier ticker** vía yfinance (sin límite de cuota) — WACC
  simplificado con el beta propio de la compañía (`cost_of_equity` +
  `cost_of_debt` + `wacc` directos, sin comparables), etiquetado como tal
  en la propia interfaz.

Controles interactivos para `n_years`, `terminal_growth_rate`,
`lookback_years` y `gordon_weight` — el usuario puede reproducir
cualquiera de los hallazgos de las secciones 7-10 cambiando un slider,
en vez de tener que releer el código.

Secciones mostradas: métricas clave, gráfico de los 3 escenarios (bear/
hold/bull) con el precio de mercado y el consenso como líneas de
referencia, tabla de supuestos de proyección, avisos técnicos del
modelo (`MIN_PRUDENT_WACC_GROWTH_SPREAD`), matriz de sensibilidad
WACC×g con gradiente de color, y el memo (generado en vivo si hay
`ANTHROPIC_API_KEY`, o el prompt listo para copiar si no la hay).

**Validación:** sin entorno de navegador disponible en esta sesión para
probar la interfaz renderizada directamente, se verificó (a) que el
servidor Streamlit arranca sin errores y responde HTTP 200, y (b) que
la ruta de cómputo completa que ejecuta la app con los valores por
defecto de cada widget —incluida la figura de matplotlib y la tabla
`pandas.Styler` con gradiente— corre de punta a punta sin excepciones,
tanto para el modo de universo cacheado (AMZN) como para el modo de
ticker arbitrario (NVDA, vía yfinance). Pendiente de una sesión futura
con navegador: captura visual real de la interfaz renderizada.
