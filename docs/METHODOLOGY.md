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

> **Nota (sesión posterior):** la tabla de abajo usa los números
> originales de esta sesión, con el motor de proyección de la época
> (fade lineal de crecimiento hacia la tasa terminal DENTRO del
> horizonte explícito). La sección 14 corrige esa forma de fade tras
> comparar contra el propio Excel de referencia, y dio como resultado
> una mejora real (desviación media 42.8%/41.3% en vez de 54.8%/53.4%).
> Se deja esta tabla histórica para que el diagnóstico de más abajo
> (causa raíz: CapEx>>D&A) siga siendo trazable; la tabla vigente está
> en la sección 14.

| Ticker | WACC | Precio implícito | Mercado | Consenso analistas | Desv. vs mercado | Desv. vs consenso |
|---|---|---|---|---|---|---|
| AMZN | 8.27% | $84.82 | $258.51 | $328.17 | -67.2% | -74.2% |
| MSFT | 8.78% | $295.80 | $499.70 | $572.92 | -40.8% | -48.4% |
| GOOGL | 8.68% | $270.02 | $705.51 | $428.07 | -61.7% | -36.9% |
| META | 8.47% | $365.03 | $712.53 | $754.77 | -48.8% | -51.6% |
| AAPL | 8.84% | $142.74 | $319.97 | $323.86 | -55.4% | -55.9% |

**Desviación media absoluta: 54.8% vs. mercado, 53.4% vs. consenso de
analistas** (mediana 55.4% / 51.6%). Parámetros: `n_years=5`,
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

> **Nota (sesión posterior):** tabla histórica, con el fade de
> crecimiento de la época (ver nota de la sección 7). Números vigentes
> en la sección 14 — el diagnóstico de fondo (inestabilidad de Gordon
> Growth con spread WACC-g estrecho) sigue siendo válido y de hecho se
> agrava ligeramente para PG/JNJ con el fade corregido, por una razón
> explicada en la sección 14.

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
Conservador (reversión a la media)          -> $104.41
Mantener nivel actual                       ->  $87.63   <- por debajo del conservador
Alcista (continúa la tendencia reciente)    -> $131.81
```

(Cifras actualizadas tras el fix de la sección 14 — crecimiento plano
en vez de fade hacia g. El hallazgo cualitativo no cambia: "mantener
nivel actual" sigue por debajo del conservador, mismo mecanismo.)

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

## 13. Bug real encontrado y corregido: `interest_expense=0` espurio (AAPL)

Al validar por fin `engine/ratios.py` contra datos reales de los 8
tickers cacheados (nunca se había hecho — el módulo solo tenía tests con
fixtures sintéticas desde que se creó en la Fase 3), `interest_coverage()`
devolvió `inf` para AAPL. Investigado antes de aceptarlo (mismo estándar
de siempre):

**Causa raíz:** Alpha Vantage reporta `interestExpense=0` para AAPL en
FY2024 y `None` en FY2025, pese a que AAPL mantiene ~$112bn-119bn de
deuda con intereses reales (FY2023 reportó correctamente $3.933bn). Es
un hueco de calidad de datos del proveedor, no un coste de deuda real de
cero — ninguna empresa con esa deuda paga 0% de interés.

**Por qué es grave y no se detectó antes:** `interest_expense` alimenta
directamente `cost_of_debt()` dentro de `wacc_builder.build_wacc()`. Con
el dato espurio, `cost_of_debt(0, total_debt) = 0.0%`. Como el peso de
la deuda en el WACC de AAPL es pequeño, el WACC agregado seguía
"pareciendo razonable" (8.77% en vez del 8.84% correcto — un desliz de
7 puntos básicos) — **precisamente por eso pasó desapercibido en las
sesiones 6-9**: revisar solo si el output agregado parece plausible no
basta cuando un input de bajo peso está mal y el agregado lo absorbe sin
verse raro. La lección metodológica: auditar inputs individuales, no
solo el resultado final.

**Corrección** (`_clean_interest_expense()` en `engine/data_provider.py`
y `engine/yfinance_provider.py`, misma lógica en ambos proveedores para
que no reaparezca por otra fuente de datos): si `interest_expense == 0`
y `total_debt > 0`, se trata como dato faltante (`None`) en vez de cero
real, para que `.dropna().iloc[-1]` caiga en el último año con un valor
genuino. Una compañía genuinamente sin deuda sí puede tener
`interest_expense=0` legítimo — ese caso NO se filtra (verificado con
test dedicado).

**Impacto verificado tras la corrección:**
- `cost_of_debt` AAPL: 0.0% -> **3.50%** (usando el interest_expense real
  de FY2023, el último disponible)
- WACC AAPL: 8.77% -> **8.84%**
- `interest_coverage` AAPL: `inf` -> **29.06x** (razonable, no infinito)
- Tabla de la Fase 7 (sección 7) y `estado.md` actualizadas con el valor
  corregido — el precio implícito de AAPL cambia de $143.94 a $142.74
  (variación pequeña, ~0.8%, pero es la cifra correcta, no la que
  "daba la casualidad de parecer razonable").

3 tests de regresión (2 en `test_data_provider.py`, 1 en
`test_yfinance_provider.py`) verifican tanto el filtrado del cero
espurio como que un cero legítimo (sin deuda) no se filtra.

## 14. Comparación directa contra el Excel profesional: la forma del fade de crecimiento estaba mal

El usuario pidió explícitamente volver a comparar la app contra "el
modelo a seguir" — el propio Excel de Amazon — en vez de seguir
añadiendo piezas nuevas. Hasta ahora solo se había validado que las
FÓRMULAS coinciden exactamente (sección 1-4) y que el WACC vía
comparables da un resultado casi idéntico (8.27%-8.33% según la
sesión). Nunca se habían puesto lado a lado, año a año, los SUPUESTOS
de proyección del analista frente a los que genera nuestro motor
automático para la misma compañía.

### Supuestos año a año: Excel (2024-2029, congelado ~nov-2024) vs. nuestra app (2027-2031, datos de 2026)

| | Año 1 | Año 2 | Año 3 | Año 4 | Año 5 | Año 6 |
|---|---|---|---|---|---|---|
| Crecimiento ingresos — Excel | 10.6% | 11.0% | 11.2% | 10.4% | 10.1% | 10.6% |
| Crecimiento ingresos — app (antes del fix) | 11.7% | 9.4% | 7.1% | 4.8% | 2.5% | — |
| Margen EBIT — Excel | 9.8% | 11.0% | 12.5% | 13.6% | 14.3% | 15.0% |
| Margen EBIT — app | 13.9% | 13.0% | 12.2% | 11.3% | 10.5% | — |
| CapEx % ventas — Excel | 12.1% | 11.8% | 11.6% | 12.4% | 12.6% | 13.0% |
| CapEx % ventas — app | 18.4% | 17.2% | 16.0% | 14.7% | 13.5% | — |

WACC: Excel 8.33% vs. app 8.27% — prácticamente idéntico (confirma que
`wacc_builder.py` funciona bien; la diferencia mínima es solo que
usamos betas de comparables de hoy, no las congeladas del Excel).

### Tres diferencias reales, cada una con su propio veredicto

**1. Forma del fade de crecimiento — bug de diseño, corregido.** El
analista mantiene el crecimiento **plano** durante los 6 años de
previsión explícita (banda estrecha 10.1%-11.2%) y solo lo hace
converger a la tasa terminal (2.5%) **de golpe, dentro de la fórmula de
Gordon Growth** — nunca dentro del horizonte explícito. Nuestro motor,
antes de esta sesión, diluía el crecimiento LINEALMENTE durante la
propia ventana explícita, llegando ya al 2.5% en el año 5 — un año
antes de que la perpetuidad ni siquiera empezara. No es "ser más
conservador que el Excel", es replicar una forma de curva distinta a la
del modelo de referencia. **Corregido** (ver más abajo).

**2. Dirección del margen — postura de modelado, no se toca.** El Excel
apuesta a que el margen sigue mejorando (9.8%→15.0%); nuestro motor por
defecto asume reversión a la media (13.9%→10.5%). Ya documentado en
profundidad en las secciones 5-7. Dato retrospectivo interesante: el
margen EBIT *real* de Amazon en 2025 (13.9%) ya casi alcanzó la
previsión que el analista tenía para *2029* (15.0%) — la tesis de
"sigue mejorando" del Excel resultó más acertada que una reversión a la
media habría predicho. Esto es evidencia real a favor de considerar un
escenario "alcista" como punto de partida en compañías con esta
dinámica, pero sigue sin ser motivo para cambiar el valor por defecto
(seguiría siendo sobreajustar a un caso conocido) — para eso está
`engine.scenarios.bullish_scenario()`, ya construido.

**3. Nivel de CapEx — aquí la app está más actualizada que el Excel, no menos.**
El Excel (construido ~nov-2024) asume CapEx estable ~12-13% porque el
supercycle de inversión en IA todavía no se había desatado con esa
magnitud. Los datos de 2026 de la app sí lo capturan (18.4% real). En
este punto concreto, el Excel es el que está desactualizado.

### La corrección aplicada (punto 1)

`engine/projections.py::default_assumptions_from_history()`: el
crecimiento de ingresos ya NO se construye como
`FadeAssumption(cagr_reciente, terminal_growth_rate)` sino como
`FadeAssumption(cagr_reciente, cagr_reciente)` — **plano** durante todo
el horizonte explícito, igual que hace el analista del Excel. La
función **ya no recibe el parámetro `terminal_growth_rate`** — ese
concepto pertenece exclusivamente al cálculo del valor terminal
(`engine.valuation.gordon_growth_terminal_value()`, sin cambios), nunca
a la proyección de los años explícitos. Actualizados todos los sitios
que llamaban a la función con ese parámetro (`engine/scenarios.py`,
`engine/validation.py`, `app/streamlit_app.py`) para dejar de pasarlo
ahí — siguen pasándolo, como siempre, directamente a `DCFInputs`/
`run_dcf`.

**Es importante notar qué tipo de cambio es este:** no es un ajuste de
un parámetro para que el precio final se acerque al de mercado (eso es
justo lo que este proyecto ha evitado en cada hallazgo). Es corregir la
FORMA de una curva para que coincida con la que usa literalmente "el
modelo a seguir" — el primer principio del blueprint ("usa tu propia
plantilla DCF como fuente de verdad"). Que el resultado suba es una
consecuencia observada, no el objetivo del cambio.

> **Nota (sesión posterior):** la sección 16 cambia además el múltiplo
> de salida (de "propio de la empresa" a "mediana de comparables"),
> desplazando estos números una vez más. Tabla vigente en la sección 16.

### Impacto verificado (universo Big Tech, mismos parámetros que la sección 7)

| Ticker | Implícito (antes) | Implícito (después) | Mercado | Desv. antes | Desv. después |
|---|---|---|---|---|---|
| AMZN | $84.82 | $104.41 | $258.51 | -67.2% | -59.6% |
| MSFT | $295.80 | $397.36 | $499.70 | -40.8% | -20.5% |
| GOOGL | $270.02 | $333.41 | $705.51 | -61.7% | -52.7% |
| META | $365.03 | $534.56 | $712.53 | -48.8% | -25.0% |
| AAPL | $142.74 | $140.60 | $319.97 | -55.4% | -56.1% |

**Desviación media absoluta vs. mercado: 54.8% -> 42.8%. Vs. consenso:
53.4% -> 41.3%.** MSFT y META prácticamente reducen su brecha a la
mitad. AAPL apenas cambia (su CAGR histórico reciente ya era bajo y
estable, así que plano vs. decayendo hacia 2.5% no difiere mucho para
esa compañía en concreto) — consistente con que el efecto del fix es
proporcional a cuánto crecimiento reciente tenía cada compañía por
encima de la tasa terminal.

**Efecto secundario, honesto y esperado, en el otro extremo (staples):**
el mismo cambio empeora ligeramente la sobrevaloración de PG/JNJ (ya
identificada en la sección 9 como inestabilidad de Gordon Growth por
spread WACC-g estrecho): un crecimiento plano más alto en el último año
explícito alimenta un UFCF terminal mayor, que la fórmula de Gordon —ya
inestable en ese régimen— amplifica más todavía. KO, con spread también
estrecho pero sin la misma sensibilidad (múltiplo de mercado más rico,
ver sección 9), apenas se mueve. Esto no es una regresión que esconder:
es la misma causa raíz (spread WACC-g estrecho) interactuando con dos
cambios distintos de forma coherente y explicable — arreglar el
crecimiento de las growth stories no podía, por construcción, arreglar
también la inestabilidad de Gordon Growth en las staples; son dos
mecanismos independientes, cada uno con su propio aviso/tratamiento.

Nuevo test de regresión
(`test_default_assumptions_revenue_growth_is_flat_not_faded_to_terminal_rate`)
verifica que el crecimiento sale plano e idéntico en todos los años del
horizonte, sobre un histórico sintético con CAGR sostenido del 21% —
lejos de cualquier tasa terminal razonable, para que el test no pueda
pasar por coincidencia. 88 tests en total, todos en verde.

### Comprobación adicional: ¿el fade lineal es la forma correcta para margen/D&A/CapEx?

El fix de crecimiento generó la duda obvia: si la forma del fade estaba
mal para crecimiento, ¿también lo está para el resto de drivers? Se
comprobó con evidencia, no se asumió. Ajuste de regresión lineal sobre
las series reales del Excel (2024-2029, 6 puntos cada una):

| Driver | Pendiente (pp/año) | R² | Deltas año a año |
|---|---|---|---|
| Margen EBIT | 1.06 | **0.978** | 1.2, 1.5, 1.1, 0.7, 0.7 |
| D&A % ventas | 0.23 | **0.982** | 0.3, 0.2, 0.1, 0.3, 0.3 |
| CapEx % ventas | 0.22 | 0.625 | -0.3, -0.2, +0.8, +0.2, +0.4 |

**Margen y D&A: el fade lineal es una réplica excelente** (R²>0.97) de
cómo el analista los modela dentro del horizonte explícito — a
diferencia del crecimiento, aquí SÍ hay una evolución gradual real
dentro de la ventana, no un valor plano. Confirma que `_margin_fade_from_recent_to_average()`
usa la forma correcta para estos dos drivers; la única diferencia real
que queda (dirección: reversión a la media vs. continuación de
tendencia) es la ya documentada en las secciones 5-7, una postura de
modelado deliberada, no una forma de curva equivocada.

**CapEx: ajuste mediocre (R²=0.625)** — el patrón real del Excel es un
valle seguido de recuperación, no una línea recta. No se traduce en un
fix por dos razones: (a) el rango es muy estrecho (11.6%-13.0%, 1.4pp),
así que el impacto práctico de acertar la forma exacta es pequeño; (b)
el CapEx *real* de AMZN hoy (18.4%→13.5% en nuestros datos de 2026)
refleja el supercycle de inversión en IA, un régimen que el Excel de
nov-2024 no podía conocer — no es una base comparable para validar o
invalidar la forma de nuestro fade actual. Sin cambios; anotado como
comprobado y descartado, no como pendiente.

## 15. `engine/ratios.py` conectado al pipeline (memo + interfaz)

Desde la Fase 3, `ratios.py` y `comps.py` existían testeados pero
huérfanos — nadie los llamaba fuera de sus propios tests. `ratios.py`
ya se conectó:

- `engine/ratios.py`: nueva `RatioSnapshot` + `compute_ratio_snapshot(row, wacc)`
  (función pura, como el resto del módulo) + `latest_ratio_snapshot(history, wacc)`
  (selecciona el último año con todos los campos necesarios vía
  `dropna`, única función del módulo que sí toma un DataFrame, por
  conveniencia de quien la llama).
- `ai/memo_generator.py`: `build_memo_input()` acepta un `ratios: Optional[RatioSnapshot]`
  opcional; si se pasa, `MemoInput.ratios` lo expone como dict en el
  payload del prompt. `ai/prompts/investment_memo_system.md` añade una
  sección "Rentabilidad y Solvencia" (ROE, ROIC vs. WACC, apalancamiento,
  liquidez) que el LLM debe omitir por completo si `ratios_financieros`
  es `null`, no dejarla vacía.
- `app/streamlit_app.py`: nueva sección con `st.metric` para ROE, ROIC
  vs. WACC (con etiqueta "Crea valor"/"No crea valor"), Debt/EBITDA,
  cobertura de intereses y current ratio, justo después de la tabla de
  supuestos. El mismo `RatioSnapshot` se pasa también al memo.

**Guarda añadida durante la integración** (mismo patrón que
`MIN_PRUDENT_WACC_GROWTH_SPREAD`): `roic()` ahora emite un `warnings.warn`
si el capital invertido es `<= 0` (equity contable negativo por
recompras agresivas — no observado en los 8 tickers piloto, pero es un
caso real conocido en otras compañías). No bloquea el cálculo, solo
avisa de que el resultado no es comparable de la forma habitual.

**Dos bugs de serialización JSON encontrados y corregidos al conectar
ratios.py a un payload JSON real (nunca se había serializado a JSON
antes de esta sesión):**

1. `creates_value` es `np.bool_` (resultado de una comparación numpy),
   no un `bool` nativo de Python. `json.dumps()` no lo serializa sin la
   salvaguarda `default=str` del código — y con ella, lo convertía en
   la CADENA `"True"` en vez del booleano JSON `true`. Corregido con
   `bool(ratios.creates_value)` explícito antes de meterlo en el
   payload.
2. `interest_coverage` puede ser `float("inf")` (empresa sin deuda,
   caso legítimo). `json.dumps(float("inf"))` produce el token
   `Infinity`, no válido en JSON estricto (RFC 8259) aunque el propio
   parser de Python lo acepte al releerlo (por eso un test que solo
   hiciera `json.loads()` no habría detectado el problema — el test de
   regresión comprueba directamente que la subcadena `"Infinity"` no
   aparece en el texto crudo). Corregido representándolo como el texto
   `"sin deuda (cobertura infinita)"`.

Ninguno de los dos bugs rompía nada de forma visible (el memo generado
a mano en esta sesión no pasó por este código) — se encontraron al
verificar de punta a punta con datos reales antes de dar la integración
por terminada, no por casualidad.

6 tests nuevos en `test_ratios.py` (12 en total) + 4 en
`test_memo_generator.py` (12 en total). 96 tests en el proyecto, todos
en verde. `comps.py` sigue sin conectar — candidato para una próxima
sesión (tabla de comparables en la interfaz, múltiplo de salida real en
vez de las constantes puntuales usadas hasta ahora).

## 16. `engine/comps.py` conectado: múltiplo de salida de peers, no de la propia empresa

Hasta esta sesión, el múltiplo EV/EBITDA usado en el valor terminal
blended era **el de la propia empresa objetivo** (`snap["ev_to_ebitda"]`).
Esto es circular: valorar AMZN usando el múltiplo con el que el mercado
YA valora a AMZN no aporta ninguna referencia externa — si el mercado
tiene a AMZN sobre o infravalorada, ese sesgo se cuela directo en
nuestro propio valor terminal. El Excel de referencia no hace esto:
usa `Comps!AC22`, el múltiplo de una tabla de comparables, no el de la
propia Amazon.

**Corregido:** `engine.validation.value_ticker()` ahora calcula el
múltiplo de salida como la **mediana de los comparables del universo,
excluyendo el ticker objetivo** (`engine.comps.build_comps_table()` +
`peer_average_multiple(..., exclude_symbol=target, method="median")`),
igual que ya hacía con el WACC. `ValuationCheck` expone el múltiplo
usado (`peer_ev_ebitda_multiple`) para que quede trazable, no oculto
dentro del cálculo. `app/streamlit_app.py` hace lo mismo en modo
universo cacheado, y muestra la tabla de comparables completa; en modo
"cualquier ticker" (sin peers) sigue usando el múltiplo propio, con un
aviso explícito de que es una referencia más débil.

### Impacto real — mixto, no una mejora uniforme, y así se reporta

| Ticker | Múltiplo propio | Múltiplo peers | Desv. mercado (propio) | Desv. mercado (peers) |
|---|---|---|---|---|
| AMZN | 11.22x | 15.87x | -59.6% | **-54.1%** (mejora) |
| MSFT | 17.68x | 13.14x | -20.5% | -27.3% (empeora) |
| GOOGL | 12.22x | 15.87x | -52.7% | **-49.4%** (mejora) |
| META | 14.05x | 14.95x | -25.0% | -23.0% (mejora leve) |
| AAPL | 28.37x | 13.14x | -56.1% | -63.0% (empeora bastante) |
| KO | 24.06x | 17.32x | +2.8% | **+0.1%** (casi exacto) |
| PG | 14.80x | 21.95x | +85.2% | +92.1% (empeora) |
| JNJ | 19.83x | 19.43x | +52.0% | +62.6% (empeora) |

**Agregado Big Tech: 42.8%→43.4% vs. mercado, 41.3%→41.5% vs. consenso
— esencialmente sin cambio neto.** A diferencia del fix de la forma del
crecimiento (sesión 12, mejora limpia en las 5 compañías), este cambio
es mecánicamente correcto pero de efecto mixto en ESTE universo
concreto: AAPL cotiza a un múltiplo (28.4x) muy por encima de sus
propios comparables de "Big Tech" (13-16x) — excluir su propio múltiplo
rico al valorarla la hace ver más barata según sus peers, pero esos
peers no necesariamente son el grupo de comparación correcto para el
múltiplo real que el mercado le asigna a AAPL específicamente. Mismo
patrón con PG (el más barato de los tres staples) y KO (el más caro):
KO mejora casi a la perfección, PG empeora.

**Por qué se mantiene el cambio de todas formas:** es la réplica
correcta de la metodología del Excel (evitar la circularidad de
autovalorarse con el propio múltiplo) y del sentido común financiero
(un múltiplo de salida debe venir de una referencia externa, no de la
propia valoración que se está intentando cuestionar). Que el efecto
agregado sea neutro en este universo concreto no es motivo para
revertirlo — sería sobreajustar a un resultado, exactamente el error que
este proyecto ha evitado en cada sesión. El verdadero problema que
revela (AAPL/KO no tienen comparables realmente homogéneos dentro de su
grupo "Big Tech"/"Consumo defensivo") es un límite conocido de trabajar
con solo 3-5 comparables por sector en vez de un universo más amplio y
mejor segmentado — anotado como mejora futura, no resuelto aquí.

1 test de regresión que fuerza el múltiplo propio de un ticker a un
valor imposible de coincidir por casualidad (999x) para probar sin
ambigüedad que se usa el de los peers. 97 tests en total, todos en
verde.

## 17. Stub period real, no siempre 1.0 (auditoría sesión 15, hallazgo C1)

`discount_periods()`/`pv_of_cash_flows()` soportan un `stub_fraction`
desde la Fase 2, validado exacto contra el Excel (`Consolidated!F35`).
Pero la auditoría técnica completa de la sesión 15 encontró que **ningún
módulo de orquestación lo calculaba nunca** — todo `DCFInputs` se
construía con el valor por defecto (`1.0`), asumiendo implícitamente que
la fecha de valoración es siempre el 1 de enero del primer año
proyectado. Cada valoración real quedaba así infravalorada de forma
sistemática, en una cantidad que crece cuanto más avanzado esté el año
en que se ejecuta la herramienta.

**Corregido de punta a punta:**

- `engine.valuation.compute_stub_fraction(fiscal_year_end_month, fiscal_year_end_day, valuation_date)`
  — fracción real por días de calendario (convención actual/actual)
  entre la fecha de valoración y el próximo cierre de ejercicio fiscal.
  Si `valuation_date` cae justo en un cierre, salta al ejercicio
  siguiente (nunca devuelve 0.0, que violaría la restricción de
  `discount_periods()`). Fallback a día 28 si el cierre fiscal es un 29
  de febrero en un año no bisiesto.
- `historical_financials()` en `engine/data_provider.py` y
  `engine/yfinance_provider.py` ahora exponen `fiscal_year_end_month`/
  `fiscal_year_end_day` (parseados de `fiscalDateEnding` / la fecha de
  columna de yfinance) — antes solo se guardaba el año.
- `engine.projections.stub_fraction_from_history(history, valuation_date)`
  une ambas piezas: toma el cierre fiscal del último año disponible y
  calcula el stub. Degrada a `1.0` si el histórico no trae esas
  columnas (fixtures sintéticas de tests) — no rompe nada existente.
- `engine.scenarios.run_scenarios()` y `engine.validation.value_ticker()`
  aceptan `valuation_date` (por defecto, hoy) y calculan el stub
  automáticamente. `app/streamlit_app.py` añade un `date_input` en la
  sidebar (por defecto hoy) y muestra el stub calculado junto a las
  métricas clave — el número siempre va con su explicación.

**Verificado con datos reales, no solo con tests sintéticos:** AMZN
(cierre fiscal 31-dic), valorado el 5-sept-2026 → stub=0.3205 (117 días
reales hasta el cierre de 365) → precio implícito **$104.41 → $108.78
(+4.19%)** frente al comportamiento anterior. Confirma exactamente la
dirección que predecía la auditoría: el bug infravaloraba de forma
sistemática. Verificado también con MSFT (cierre fiscal 30-jun, para
confirmar que funciona con ejercicios no naturales) y con el caso límite
de que "hoy" ya haya pasado el último cierre fiscal reportado (salta
correctamente al ejercicio siguiente).

12 tests de regresión nuevos, cubriendo: el cálculo puro
(`compute_stub_fraction`, incluido el caso límite de 29 de febrero y el
de valorar justo en el día de cierre), la extracción desde un DataFrame
histórico (`stub_fraction_from_history`, incluida la degradación a 1.0
sin las columnas nuevas), y el wiring end-to-end en `run_scenarios`/
`value_ticker` (el precio cambia de verdad al pasar un histórico con
fechas fiscales reales). 109 tests en total, todos en verde.

## 18. Excepciones no controladas en modo "cualquier ticker" (auditoría sesión 15, hallazgo I3)

La rama de `app/streamlit_app.py` que acepta un ticker arbitrario (no
del universo cacheado) construye un WACC simplificado a partir de datos
de yfinance sin ningún `try/except`. La auditoría señaló dos puntos de
fallo concretos: `hist["interest_expense"].dropna().iloc[-1]` (lanza
`IndexError` si la serie queda vacía) y `cost_of_equity(..., snap["beta"], ...)`
(lanza `TypeError` si `beta` es `None`).

**Corregido con dos capas, no solo una:**

1. **Defensa en el límite del sistema** (`app/streamlit_app.py`): el
   bloque completo queda envuelto en `try/except Exception` — amplio de
   forma deliberada, justificado porque es un límite real del sistema
   (entrada de texto arbitraria del usuario contra una API externa que
   no controlamos; no se puede enumerar de antemano cada fallo posible
   de yfinance). Comprobaciones explícitas con mensajes claros para:
   histórico vacío, beta ausente, gasto financiero ausente, tipo
   impositivo ausente.

2. **Corrección de raíz, descubierta al intentar reproducir el fallo
   con un ticker real** (`ZZZZINVALID`): el crash real no estaba donde
   parecía. Ocurre un nivel más abajo, dentro de `historical_financials()`
   en AMBOS proveedores (`data_provider.py`, `yfinance_provider.py`):
   sin ningún año/fecha en común entre income statement, balance sheet
   y cash flow, `rows` queda vacío y `pd.DataFrame([]).sort_values("fiscal_year")`
   lanza `KeyError('fiscal_year')` — un DataFrame de cero filas no tiene
   ninguna columna, así que ordenar por una columna que no existe
   revienta. Corregido devolviendo un DataFrame vacío con las columnas
   esperadas (`HISTORICAL_FINANCIALS_COLUMNS`, constante compartida
   entre ambos proveedores) en vez de dejar que `sort_values` falle.
   Esto beneficia a cualquier consumidor futuro de `historical_financials()`,
   no solo a la app — es una corrección de biblioteca, no un parche de
   interfaz.

**Verificado con la API real, no con fixtures:** `ZZZZINVALID` vía
yfinance ya no lanza ninguna excepción interna (confirmado con una
llamada de red real, no mockeada) — `historical_financials()` devuelve
un DataFrame vacío con el esquema correcto, y el bloque de la app
produce el mensaje "yfinance no devolvió estados financieros para
'ZZZZINVALID' — comprueba que el símbolo es correcto" en vez de una
pantalla de error de Streamlit. Las otras tres comprobaciones (sin
beta, sin interés, sin tipo impositivo) verificadas una a una con datos
sintéticos que fuerzan cada caso por separado.

6 tests de regresión nuevos (DataFrame vacío en ambos proveedores +
verificación manual de las 4 ramas de guarda de la app, que no tiene
tests automatizados por sí misma — ver hallazgo N2). 111 tests en
total, todos en verde.

## 19. Risk-free rate en vivo, prima de riesgo como slider (auditoría sesión 15, hallazgo I1)

`app/streamlit_app.py` usaba dos constantes de módulo,
`RISK_FREE_RATE = 0.03909` y `MARKET_RISK_PREMIUM = 0.0406`, copiadas
literalmente de `WACC!F11`/`WACC!F13` del Excel de referencia (datos de
~noviembre 2024). Cada valoración, se ejecutara cuando se ejecutara,
usaba el tipo libre de riesgo de hace año y medio.

**Los dos parámetros no se tratan igual, porque no tienen la misma
disponibilidad de datos:**

- **Risk-free rate (rendimiento del Treasury a 10 años):** tiene una
  fuente en vivo estándar y gratuita. Se añadieron dos implementaciones
  equivalentes, testeadas por separado:
  - `engine/yfinance_provider.py::treasury_yield_10y(ticker)` — lee el
    índice `^TNX` de Yahoo Finance (cotiza en puntos porcentuales, un
    `Close` de 4.78 significa 4.78%) vía `ticker.history()`. Sigue el
    mismo patrón de inyección de dependencia que el resto del módulo
    (recibe el objeto `Ticker` ya construido, no el símbolo), para
    poder testear sin red.
  - `engine/data_provider.py::AlphaVantageClient.treasury_yield(maturity="10year")`
    — función económica `TREASURY_YIELD` de Alpha Vantage. Requirió
    generalizar `_fetch()`: las funciones económicas (a diferencia de
    `INCOME_STATEMENT`, `OVERVIEW`, etc.) no cuelgan de un `symbol`, así
    que se extrajo `_fetch_economic_indicator(function, extra_params, cache_key)`
    como método hermano, reutilizando la validación de respuesta
    (`_validate_response`, antes duplicada dentro de `_fetch`). El
    payload de Alpha Vantage no garantiza el orden de la serie temporal,
    así que se elige el punto más reciente por comparación explícita de
    fecha (`max(points, key=lambda p: p["date"])`), no el primer
    elemento — y se descartan los marcadores de dato faltante (`"."`,
    usados por varias series económicas de Alpha Vantage).

  La app **siempre usa la vía yfinance**, incluso en modo "universo
  cacheado con Alpha Vantage": el Treasury yield es un dato de mercado
  ambiental, igual para cualquier compañía en cualquier momento, y usar
  Alpha Vantage para él gastaría cuota (25 peticiones/día) en algo que
  no depende del ticker que se esté valorando. El método de
  `AlphaVantageClient` queda como alternativa ya testeada, no muerta —
  documentado aquí por si en el futuro conviene usarlo (p. ej. si
  yfinance deja de exponer `^TNX` de forma fiable).

  Si la consulta en vivo falla (sin red, Yahoo Finance no disponible),
  `get_live_risk_free_rate()` en `app/streamlit_app.py` cae a
  `FALLBACK_RISK_FREE_RATE` (la constante congelada original) con un
  aviso explícito en la interfaz — mismo principio que el resto de la
  sesión: nunca fallar en silencio.

- **Prima de riesgo de mercado (ERP):** no tiene un equivalente en vivo
  gratuito y fiable. El estándar de facto del sector (las series de
  Aswath Damodaran) se publica de forma manual y periódica en una
  página web, no vía una API estable — construir un scraper para eso
  sería frágil y rompería en silencio ante cualquier cambio de
  formato, cambiando un problema conocido (constante congelada, visible
  y documentada) por uno peor (fuente de datos silenciosamente rota).
  En vez de fingir una fuente en vivo que no existe con garantías, se
  convirtió en un `st.slider` ajustable en la sidebar (`DEFAULT_MARKET_RISK_PREMIUM`
  como valor por defecto, con un `help` que explica por qué no es un
  dato en vivo y sugiere Damodaran como referencia para actualizarlo a
  mano).

**Verificado con datos reales:** el 2026-09-06 el Treasury 10Y real
(vía `^TNX`) cotizaba a **4.784%**, frente al 3.909% de la constante
congelada — una diferencia de +0.875 puntos porcentuales. Revalorando
AMZN con el resto de supuestos idénticos (misma prima de riesgo,
mismos comparables, mismo escenario conservador): el WACC pasa de
8.266% a 9.096%, y el precio implícito de **$108.80 a $98.00 (-9.93%)**.
La constante congelada estaba inflando de forma material el precio
implícito de todas las valoraciones — incluida, irónicamente, la propia
verificación numérica del fix de C1 en la sección 17, hecha con la tasa
vieja (ese número histórico de la sección 17 queda tal cual, como
registro de lo que se verificó en su momento; no se reescribe con
efecto retroactivo).

7 tests de regresión nuevos (4 en `test_data_provider.py`: conversión a
fracción, selección de la fecha más reciente sin asumir orden,
descarte de marcadores `"."`, error si no hay datos válidos; 3 en
`test_yfinance_provider.py`: conversión a fracción, uso del cierre más
reciente, error si el histórico viene vacío). 118 tests en total, todos
en verde. Servidor Streamlit reiniciado y verificado arrancando limpio
tras el cambio (puerto 8514, `/_stcore/health` responde `ok`, sin
tracebacks en el log del servidor).

## 20. Reverse DCF: expectativas implícitas del mercado (sesión 16)

### La pregunta que faltaba responder

Toda la sesión 15 (auditoría técnica) y la revisión de progreso de la
sesión 16 (`docs/PROGRESS_REVIEW.md`) trataron la desviación del ~42%
frente al precio de mercado como una limitación a medir y comunicar. El
usuario planteó la pregunta correcta antes de seguir desarrollando:
**¿para qué se usa un DCF de verdad, y qué información aporta?** Un DCF
hacia delante no está diseñado para predecir el precio de mercado —
responde "¿qué precio justifican mis supuestos?". La pregunta
complementaria, igual de estándar en equity research profesional
(análisis de expectativas implícitas / reverse DCF), es "¿qué tendría
que ser cierto para justificar el precio que YA cotiza el mercado (o el
consenso)?". Esa pregunta nunca se había implementado — solo se mostraba
el TAMAÑO de la brecha (%), nunca el MECANISMO (qué crecimiento habría
que creer).

### Diseño: mismo motor, resuelto al revés — no una segunda metodología

Un reverse DCF no es una forma alternativa de calcular valor: es
`run_dcf()` (ya validado exacto contra el Excel) resuelto para una
incógnita dado un precio objetivo, en vez de resuelto para el precio
dado las hipótesis. Esto se implementó con **bisección pura, sin
dependencias externas** (`engine.valuation.solve_for_target_price()`),
consistente con el principio de "Python puro" del blueprint — válido
porque las funciones involucradas (proyección con fade, Gordon Growth)
son continuas y monótonas crecientes en el rango relevante, así que no
hace falta un método de raíces más sofisticado (Newton, scipy.optimize,
etc.). El solver:

- Verifica la monotonía contra los dos extremos del rango antes de
  buscar (no la asume a ciegas) y falla explícitamente si no se cumple.
- Falla explícitamente, con el precio real alcanzable en cada extremo,
  si `target_price` queda fuera del rango de búsqueda — ese fallo es en
  sí mismo informativo (cuantifica cuán grande es la brecha), no un
  error a esconder.
- Tolerancia de precio: $0.01 (al céntimo, mismo estándar que el resto
  del motor).

Dos incógnitas distintas, cada una responde una pregunta distinta:

1. **`implied_revenue_growth()`** (`engine/projections.py`): con
   márgenes/CapEx/D&A/ΔNWC/WACC/g terminal/múltiplo de salida fijos en
   los del escenario conservador, resuelve qué tasa de crecimiento de
   ingresos PLANA durante el horizonte explícito — la misma forma que ya
   usa `default_assumptions_from_history()` (sección 14) — reproduce el
   precio objetivo. Es la lectura más intuitiva para un memo: "el
   mercado necesita X% de crecimiento anual, nosotros asumimos Y%".
2. **`implied_terminal_growth_rate()`** (`engine/valuation.py`): con
   todo lo demás fijo, resuelve qué tasa de crecimiento PERPETUO (la
   misma `g` de Gordon Growth) reproduce el precio objetivo. Lectura
   complementaria: si el `g` resuelto es economicamente implausible para
   una perpetuidad (p. ej. por encima del crecimiento nominal del PIB a
   largo plazo), es una señal de que la brecha no se explica solo con
   más crecimiento perpetuo — hace falta crecimiento real en el
   horizonte explícito. Requiere `gordon_weight>0` cuando hay múltiplo
   de salida (si `gordon_weight<=0`, g no tiene ningún efecto sobre el
   precio — ver M4 — y se falla explícitamente en vez de devolver un
   valor sin sentido). Si el `g` resuelto cae en la zona de spread
   WACC-g estrecho (`MIN_PRUDENT_WACC_GROWTH_SPREAD`), se re-evalúa sin
   suprimir avisos al final de la búsqueda para que el mismo warning de
   siempre llegue al llamador — no se inventa un aviso nuevo ni distinto.

`engine/reverse_dcf.py` orquesta ambas piezas en un único bundle
(`compute_implied_expectations()`), igual que `engine/wacc_builder.py`
orquesta las piezas de `valuation.py` para el WACC — mismo patrón
arquitectónico ya establecido en el proyecto, sin una segunda fuente de
verdad. Cuando un target queda fuera de rango para un solver concreto,
ese campo queda en `None` sin descartar el resto del resultado.

### Verificado con datos reales — AMZN, hoy

| Precio objetivo | Crecimiento de ingresos implícito | Asumido (conservador) | Gap | g terminal implícita | Asumida |
|---|---|---|---|---|---|
| Mercado ($258.51) | **31.8%** | 11.7% | **+20.1 pp** | 7.25% ⚠️ | 2.50% |
| Consenso ($328.17) | **38.3%** | 11.7% | **+26.6 pp** | 7.72% ⚠️ | 2.50% |

**Lectura conjunta, no cada cifra por separado:** el mercado paga hoy
por AMZN un crecimiento de ingresos casi el triple del asumido en el
escenario conservador (31.8% vs. 11.7%). Alternativamente, si se
insistiera en explicar todo el precio solo vía una tasa de crecimiento
perpetuo más alta (sin subir el crecimiento del horizonte explícito),
haría falta un 7.25% de crecimiento *perpetuo* — muy por encima de
cualquier tasa de crecimiento macro de largo plazo razonable, y
correctamente marcado ⚠️ como zona de inestabilidad numérica de Gordon
Growth. **La combinación de ambas lecturas es la evidencia real de que
la brecha se explica sobre todo por expectativas de crecimiento en el
horizonte explícito (probablemente ligadas al supercycle de CapEx en
IA, ver sección 7), no por una perpetuidad optimista.** Esto es
exactamente la clase de conclusión que un DCF debe producir — no
"el modelo se equivoca 42%", sino "así de grande es la apuesta de
crecimiento implícita en el precio actual, y así se compara con la
nuestra".

**Validado con un caso límite real, no solo con el caso típico:** en
modo "cualquier ticker" (yfinance) con NVDA, el CAGR reciente ya asumido
por el escenario conservador (~100%, crecimiento real de la compañía)
es tan alto que ni el propio precio de mercado actual lo justifica del
todo con los supuestos de margen/CapEx vigentes — ambos solvers
devuelven correctamente "fuera de rango" (gap negativo) en vez de
forzar un resultado o fallar de forma opaca. Confirma que el manejo de
límites funciona en ambas direcciones (mercado exige más Y mercado
exige menos de lo asumido), no solo en el caso Big Tech ya conocido.

### Dónde vive en la app y en el memo

Nueva sección "Expectativas implícitas del mercado (reverse DCF)" en
`app/streamlit_app.py`, entre los supuestos de proyección y los ratios
financieros — tabla con crecimiento implícito, gap vs. asumido y g
terminal implícita para mercado y consenso, cada celda con manejo
explícito de "fuera de rango" quando corresponda. `ai/memo_generator.py`:
`MemoInput.implied_expectations` (nuevo campo opcional) se serializa al
payload del LLM como `expectativas_implicitas_del_mercado`; el prompt de
sistema (`ai/prompts/investment_memo_system.md`) ahora tiene una sección
dedicada del memo para esto, y la regla 6 (antes "explica el mecanismo
si está en el paquete") ahora señala este campo como LA explicación del
mecanismo cuando está presente, en vez de una entre varias posibles.

14 tests de regresión nuevos (`test_valuation.py`: solver genérico +
`implied_terminal_growth_rate`, incluyendo ida y vuelta sobre el caso
AMZN real de `TARGET_IMPLIED_PRICE`; `test_projections.py`:
`implied_revenue_growth`, ida y vuelta sobre un histórico sintético;
`test_reverse_dcf.py`: orquestación conjunta; `test_memo_generator.py`:
serialización en el payload del LLM, incluyendo el caso con campos
`None`). 142 tests en total, todos en verde. Verificado end-to-end en
la app real (Playwright, sin servidor de por medio): captura de
pantalla confirma que la tabla renderiza con los números exactos
calculados por el motor, en modo universo cacheado (AMZN) y en modo
cualquier ticker (NVDA, caso límite de fuera de rango). Servidor
Streamlit reiniciado y verificado arrancando limpio.

## 21. Detección de outliers en el ancla del fade — intento de auto-corrección, descartado con evidencia (sesión 17)

Petición explícita del usuario: analizar cómo aumentar el rigor
matemático del modelo y de la confianza en lo que reporta, más allá de
acercar el precio al mercado (ya descartado como objetivo — sería el
mismo sobreajuste evitado en cada hallazgo anterior). El punto de
partida fue el propio hallazgo de la sección 9: JNJ hereda un outlier
real (margen EBIT 2025 = 35.6% frente a 18.6%/19.6% en 2023/2024, un
ítem no recurrente ligado a la escisión de Kenvue) porque
`_margin_fade_from_recent_to_average()` ancla el año 1 del fade en el
último año real sin comprobar si ese año es representativo.

### Primer diseño (descartado): sustitución automática por la mediana

Se implementó un detector estadístico — z-score modificado (mediana +
MAD de los años de referencia, regla de Iglewicz & Hoaglin 1993,
`|z| > 3.5`), robusto en muestras pequeñas a diferencia de media/
desviación típica clásicas — que, al detectar un outlier en el último
año, sustituía el ancla `start` del fade por la mediana de los años
previos, con un aviso explícito (mismo patrón que
`MIN_PRUDENT_WACC_GROWTH_SPREAD`). Verificado primero con el caso JNJ
real: z=21.3, muy por encima del umbral, ancla correctamente sustituida
de 35.6% a 19.1% — funcionaba exactamente como se pretendía, aisladamente.

### La contraprueba con el universo piloto completo lo invalidó

Antes de dar el cambio por bueno se corrió `scripts/validate_universe.py`
sobre los 8 tickers reales (mismo estándar de verificación que cualquier
otro hallazgo de este documento) para medir el impacto agregado. El
detector disparó en **7 de 8 tickers**, no solo en JNJ:

| Ticker | Driver marcado | Último año | Mediana previa | z modificado |
|---|---|---|---|---|
| AMZN | D&A % ventas | 9.2% | 8.4% | 5.7 |
| MSFT | Margen EBIT | 50.9% | 44.9% | 18.3 |
| MSFT | CapEx % ventas | 34.9% | 20.5% | 4.1 |
| GOOGL | ΔNWC % ventas | 4.6% | -3.3% | 6.5 |
| META | CapEx % ventas | 34.7% | 21.4% | 7.3 |
| KO | Margen EBIT | 36.8% | 31.5% | 22.5 |
| PG | D&A % ventas | 3.6% | 3.4% | 4.3 |
| JNJ | Margen EBIT | 35.6% | 19.1% | 21.3 |

**El problema: el CapEx de MSFT (34.9%) y META (34.7%) marcados como
"outlier a sustituir" es precisamente el supercycle de inversión en IA
que la sección 7 ya verificó como una tendencia real y estructural, no
ruido** — sustituirlo por la mediana histórica habría borrado
silenciosamente la señal más importante y ya validada de todo el
proyecto (la explicación mecánica de por qué Big Tech sale
infravalorada). Se comprobó el histórico completo de MSFT (no solo la
ventana de 3 años) para confirmarlo antes de descartar el diseño:
margen EBIT 43.1%→45.2%→44.7%→50.9% y CapEx 13.3%→18.1%→22.9%→34.9% a
lo largo de 2023-2026 — una aceleración multi-año consistente, no un
salto puntual.

**Causa raíz del fallo de diseño:** con solo 2-3 años de referencia
(`lookback_years` por defecto = 3), un z-score no tiene forma de
distinguir estadísticamente "ítem no recurrente" (JNJ: plano durante
años, luego un salto puntual sin continuidad) de "inicio/aceleración de
una tendencia estructural real" (MSFT/META: la propia serie ya venía
subiendo antes del último año) — ambos producen una desviación enorme
frente a una referencia de solo 2 puntos. Distinguir ambos casos
requiere criterio cualitativo verificable (como se hizo a mano en la
sección 9: cruzar el dato con la escisión de Kenvue; y en la sección 7:
cruzar el CapEx con el JSON crudo y el contexto de mercado conocido),
no es inferible de un histórico tan corto.

### Corrección: aviso, nunca sustitución automática

Se mantiene el detector (matemáticamente correcto para lo que
detecta: "este año es estadísticamente atípico frente a su propia
referencia reciente") pero se elimina la sustitución. El año 1 del fade
siempre usa el valor real del último año, sin excepción — fiel al
principio original ("el último año real es el mejor estimador
disponible del estado actual"). El detector solo añade un
`warnings.warn()` señalando la anomalía para revisión manual, igual que
`MIN_PRUDENT_WACC_GROWTH_SPREAD`: nunca un ajuste silencioso, la
decisión queda del lado del analista que lee el aviso.

Este aviso llega gratis a las tres superficies que ya consumen
`warnings.warn()` sin cableado adicional: `app/streamlit_app.py`
("Aviso técnico del modelo"), `ai/memo_generator.py`
(`MemoInput.warnings_raised`, vía `run_scenarios_capturing_warnings()`)
y `scripts/validate_universe.py` (capturado por ticker en el JSON de
validación) — las tres ya envuelven el cálculo con
`warnings.catch_warnings(record=True)`.

**Verificado que el cambio final no altera ningún precio ya
documentado:** se re-corrió `validate_universe.py` con la versión
final (solo aviso) y la desviación agregada volvió exactamente a
41.82% / 39.76% (mercado/consenso), idéntica a la de antes de esta
sesión — como debía ser, ya que el ancla nunca cambia de valor, solo se
señala. 23 tests nuevos/actualizados en `test_projections.py`
(detección de outlier con aviso pero sin sustitución sobre el caso JNJ
sintético; el caso límite de la sección 9 con z=3.37 confirmado por
debajo del umbral; e insuficientes años de referencia sin falso
positivo). 159 tests en total, todos en verde.

**Valor real de lo que queda, honestamente acotado:** no es una mejora
de precisión (no cambia ningún número) — es una mejora de
**transparencia diagnóstica**: el informe ahora señala explícitamente
qué supuestos de partida son estadísticamente inusuales frente a su
propio histórico reciente, para que el analista decida con
información en vez de heredar un año atípico sin saberlo. El intento
fallido de auto-corrección queda documentado en detalle porque el
proceso de descubrir por qué no funcionaba —no solo el resultado final—
es la evidencia real de rigor que se le pidió al proyecto.

## 22. Elasticidades del modelo ("Greeks") — `engine/sensitivity.py` (sesión 17)

Segunda mitad del mismo encargo de la sección 21: sistematizar el
ejercicio que hasta ahora se hacía a mano, desglosando el DCF caso por
caso, para concluir cosas como "en MSFT domina el CapEx" (sección 7) o
"en PG/JNJ domina el spread WACC-g" (sección 9). `driver_sensitivities()`
automatiza exactamente ese ejercicio: sobre el escenario conservador,
desplaza un supuesto a la vez (+1pp por defecto — WACC, g terminal,
margen EBIT, D&A % ventas, CapEx % ventas, crecimiento de ingresos),
reejecuta el DCF completo y mide el cambio en el precio implícito,
devolviendo la lista ordenada de mayor a menor impacto absoluto. Para
margen/D&A/CapEx/crecimiento, el desplazamiento mueve TODO el tramo del
fade en paralelo (año 1 y año N por igual) — responde a "¿y si este
supuesto fuera sistemáticamente 1pp más alto?", no solo a "¿y si
cambiara el ancla?". Reutiliza literalmente el mismo pipeline que
`engine.scenarios.run_scenarios` (histórico -> supuestos -> proyección
-> DCF), así que el precio base coincide exactamente con el escenario
"Conservador".

### Verificado con datos reales — reproduce automáticamente los hallazgos ya documentados, y añade un matiz nuevo

| Ticker | WACC | Driver dominante | Efecto por +1pp |
|---|---|---|---|
| AMZN | 9.30% | CapEx % ventas | -21.65% |
| MSFT | 9.30% | Tasa de crecimiento terminal (g) | +15.75% |
| PG | 5.70% | Tasa de crecimiento terminal (g) | +42.41% |
| JNJ | 5.70% | Tasa de crecimiento terminal (g) | +42.30% |

AMZN confirma el mecanismo de la sección 7 (CapEx domina) tal cual;
PG/JNJ confirman el de la sección 9 (spread WACC-g estrecho domina,
amplificado por `gordon_weight=0.8`) con un número exacto en vez de una
narrativa. **Matiz nuevo, no documentado hasta ahora:** en MSFT domina
la tasa de crecimiento terminal (+15.75%) y el WACC (-14.34%) por
delante del CapEx, pese a que MSFT también tiene un CapEx elevado
(sección 21, supercycle de IA) — consistente con que, para un WACC-g
saludable (~6.8pp de spread), el valor terminal sigue pesando la mayor
parte del enterprise value con solo 5 años de horizonte explícito, un
hecho conocido de cualquier DCF pero que hasta ahora nunca se había
cuantificado por ticker en este proyecto.

### Dónde vive en la app y en el memo

`app/streamlit_app.py`, pestaña "Supuestos y expectativas": gráfico de
barras horizontal (tornado chart) bajo las expectativas implícitas del
mercado, un solo hue (igual criterio que el resto de gráficos de
magnitud del proyecto — la dirección ya la comunica el signo del label
y la posición izquierda/derecha del cero, no hace falta codificar
"sube/baja" con semántica de color bueno/malo, que no aplica aquí).
`ai/memo_generator.py`: `MemoInput.sensitivities` (nuevo campo opcional)
se serializa como `sensibilidad_del_precio_por_supuesto`; la regla de la
sección "Tesis de Valoración" del prompt de sistema ahora nombra el
supuesto dominante y su impacto en vez de listar los supuestos sin
jerarquía.

11 tests nuevos (`test_sensitivity.py`: los 6 drivers presentes, mismo
precio base compartido, orden descendente por impacto absoluto, signos
verificados a mano contra la mecánica de UFCF, simetría exacta CapEx/D&A
—mismo coeficiente, signo opuesto—, escalado aproximadamente lineal con
el tamaño del bump, `gordon_weight=0` anula el efecto de g terminal;
`test_memo_generator.py`: serialización con y sin el campo;
`test_app.py`: la sección renderiza con los 3 gráficos Plotly de la
pestaña). 171 tests en total, todos en verde. Verificado con AppTest que
la sección renderiza sin excepción sobre el estado por defecto de la
app.

## 23. Comparación contra la práctica de banca de primer nivel: football field, comparables independientes y transparencia del valor terminal (sesión 17)

Petición del usuario: comparar el motor contra cómo bancos como JP
Morgan presentan una valoración real, para encontrar huecos genuinos —
no una lista de "cosas que suenan a banco", sino una revisión concreta
de `wacc_builder.py`, `DCFResult`, `comps.py` y los datos crudos ya
disponibles. Confirmado primero lo que YA coincide con la práctica
profesional (no solo con teoría): reapalancar a la estructura de capital
*actual* de la empresa objetivo (igual que el Excel de referencia,
secciones 3-4); crecimiento plano en el horizonte explícito (sección
14); y el EBIT ya es GAAP — comprobado con el campo real
`stockBasedCompensation` de Alpha Vantage ($19.5bn en AMZN, ejercicio
2025) que no se añade de vuelta en ningún punto del pipeline, a
diferencia de muchos DCFs de mercado que parten de EBITDA ajustado (SBC
añadido de vuelta como si fuera gratis) — nuestro motor es, en este
punto concreto, más riguroso que la práctica común, no menos.

Tres huecos reales identificados y cerrados en esta sesión:

**1. Comparables de mercado como método de valoración independiente
(`engine.comps.comps_implied_share_price`).** Hasta ahora `comps.py`
solo usaba el múltiplo mediano de peers para UNA pieza del DCF
(`exit_multiple_terminal_value`, el valor terminal). Nunca se aplicaba
directamente al EBITDA/ingresos ACTUALES (último año real) de la
empresa objetivo para obtener un precio implícito standalone — la
segunda pata de cualquier valoración bancaria junto al DCF. Aplica
EV/EBITDA y EV/Revenue de peers (excluyendo el propio ticker) al
tamaño real de la empresa; `implied_share_price_from_ebitda`/`_revenue`
es `None` cuando la base no es significativa (EBITDA ≤ 0).

Verificado con datos reales, AMZN (peers Big Tech): EV/EBITDA mediano
de peers (15.87x) implica **$237.06** — mucho más cerca del precio de
mercado ($258.51) que el DCF conservador (~$104) — mientras que
EV/Revenue mediano (9.59x) implica **$630.94**, muy por encima de
ambos. **Hallazgo real, no solo mecánico:** EV/Revenue se distorsiona
fuertemente cuando los peers tienen perfiles de margen muy distintos —
MSFT/GOOGL (software, margen alto) inflan el múltiplo de ingresos
frente al margen mucho más fino de AMZN (retail + cloud), aunque coticen
a un EV/EBITDA parecido. Confirma por qué EV/EBITDA es el múltiplo
primario en la práctica bancaria (normaliza por margen) y EV/Revenue
uno secundario/de contraste, útil sobre todo con EBITDA negativo. Este
resultado, además, es una TERCERA confirmación independiente (junto al
DCF y al precio de mercado) de la tesis de la sección 7: el DCF
conservador es el más bajo de los tres métodos, consistente con que su
motor de reversión a la media no extrapola el retorno futuro del
supercycle de CapEx que sí paga el mercado hoy.

**2. Football field chart.** Combina en un único gráfico horizontal de
rangos: DCF (min/max de los 3 escenarios), comparables (min/max de
EV/EBITDA y EV/Revenue) y rango de cotización de 52 semanas (nuevo
campo `week_52_high`/`week_52_low`, ya expuesto sin coste adicional en
`OVERVIEW` de Alpha Vantage y en `.info` de yfinance como
`fiftyTwoWeekHigh`/`fiftyTwoWeekLow`), con el precio de mercado y el
consenso de analistas como líneas de referencia — exactamente la vista
que encabeza un informe de equity research bancario, triangulando
métodos en vez de presentar uno solo. Solo se muestra si hay ≥ 2
métodos disponibles (en modo "cualquier ticker" sin comparables, cae a
un aviso explícito en vez de un gráfico de una sola barra sin sentido
de "triangulación").

**3. Transparencia Gordon Growth vs. múltiplo de salida.**
`DCFResult` ya calculaba ambos valores terminales por separado
(`gordon_terminal_value`, `exit_multiple_terminal_value`) desde la Fase
2, pero la app nunca los mostraba — solo el blend final. Ahora se
muestran los tres números lado a lado (Gordon Growth, múltiplo de
salida, valor usado) más el % de brecha entre los dos métodos.
Verificado con datos reales, JNJ (spread WACC-g estrecho, ya conocido
de la sección 9): Gordon Growth = $1.00 billones frente a $792 mil
millones del múltiplo de salida — **brecha de +26.4%**, ahora visible
directamente en la interfaz en vez de requerir desglosar el DCF a mano
para descubrirla.

### Lo que queda fuera, documentado como limitación permanente

**Transacciones precedentes (M&A comps).** Estructuralmente imposible
de replicar sin una base de datos de transacciones M&A de pago — no
existe una fuente gratuita equivalente a Alpha Vantage/yfinance para
esto. Documentado aquí como limitación honesta y permanente, no como
un pendiente de construir.

### Verificación

4 tests nuevos en `test_comps.py` (aplicación del múltiplo mediano,
`None` cuando EBITDA no es significativo, `diluted_shares` no positivo
rechazado, caja neta suma en vez de restar al precio). Campo
`week_52_high`/`week_52_low` añadido a ambos proveedores con tests de
esquema actualizados. `test_app.py` actualizado: 4 gráficos Plotly en
el estado por defecto (football field + escenarios + heatmap WACC×g en
"Valoración", tornado chart en "Supuestos y expectativas"), más los
nuevos subheaders. **175 tests en total, todos en verde.** Verificado
además con datos reales fuera de la suite de tests (no solo con los
fixtures sintéticos del AppTest): JNJ con el proveedor yfinance real,
confirmando que `comps_implied_share_price` y el desglose Gordon/exit
multiple producen números coherentes y sin excepciones sobre el
universo "Consumo defensivo" completo, el caso con el spread WACC-g más
estrecho y por tanto el más propenso a exponer un bug de división por
cero o `None` no gestionado.

## 24. Continuación de la comparación bancaria: tendencia histórica, cobertura de analistas, rango de consenso (sesión 17)

Mismo hilo que la sección 23, con la misma disciplina: solo campos que
YA estaban disponibles sin coste adicional en los proveedores de datos,
verificados con valores reales antes de conectarlos.

**Cobertura de analistas.** `OVERVIEW` de Alpha Vantage expone el
desglose de recomendaciones por tramo (`AnalystRatingStrongBuy/Buy/
Hold/Sell/StrongSell`) — nunca usado hasta ahora, pese a estar en la
misma respuesta que ya se descarga para el precio de consenso.
Verificado con AMZN real: 15 compra fuerte, 44 compra, 2 mantener, 0
venta, 0 venta fuerte — un dato que refuerza directamente la narrativa
del reverse DCF (sección 20): el consenso no solo pone un precio alto,
la cobertura está casi unánimemente del lado comprador. yfinance no da
ese desglose por tramo en `.info` (requeriría `.recommendations`, una
llamada distinta) — se usa el mejor sustituto disponible sin coste
extra: `recommendationKey` (texto), `recommendationMean` (escala 1-5) y
`numberOfAnalystOpinions`. Se muestra como tooltip del metric "Consenso
analistas" ya existente, no como un elemento nuevo que reordene el
layout.

**Rango de precio objetivo de analistas.** yfinance expone
`targetLowPrice`/`targetHighPrice` además de la media ya usada — Alpha
Vantage solo da el promedio, sin rango. Cuando está disponible (modo
yfinance), se añade como una barra más del football field de la
sección 23 ("Consenso de analistas (rango)"), en vez de solo la línea
vertical de la media — verificado con JNJ real: rango $190–$320 frente
a una media de consenso de $275.64.

**Gráfico de tendencia histórica.** Nuevo, al principio de la pestaña
"Supuestos y expectativas": crecimiento de ingresos interanual y margen
EBIT, año a año, sobre TODO el histórico disponible del proveedor (no
solo la ventana de `lookback_years` que alimenta el fade) — da
contexto de dónde parte cada supuesto antes de mostrar la tabla de
proyección, tal como abre la sección financiera de cualquier informe
bancario. Ambas series en el mismo eje (mismo tipo de unidad,
porcentaje) — nunca un eje Y doble, ver `docs/AUDIT.md`/dataviz.
Verificado con AMZN real (20 años de histórico vía Alpha Vantage): la
serie muestra visualmente la propia historia de la sección 14 — margen
EBIT cerca de cero durante más de una década (crecimiento a toda costa)
y expandiéndose con fuerza solo en los últimos 3 años (2.4%→6.4%→
11.1%→13.9%), el mismo patrón que ya motivó preferir "continuar la
tendencia" como hipótesis alcista en vez de "revertir a la media" del
histórico completo.

### Verificación

Campos nuevos en ambos proveedores (`analyst_rating_*` en Alpha
Vantage; `analyst_recommendation_*`, `analyst_target_price_low/high` en
yfinance) con tests de esquema actualizados. `test_app.py`: nuevo test
del tooltip de cobertura de analistas; conteo de gráficos Plotly
actualizado a 5 (el nuevo gráfico de tendencia histórica). **176 tests
en total, todos en verde.** Verificado con datos reales de AMZN
(Alpha Vantage) y JNJ (yfinance) fuera de la suite de tests para
confirmar que los campos nuevos traen valores sensatos y ningún `None`
no gestionado rompe el formateo de la UI.

## 25. Auditoría matemática y financiera a fondo del motor (sesión 17)

Petición explícita del usuario: en vez de seguir añadiendo funciones,
revisar en profundidad lo que ya existe — "a nivel técnico y matemático
y de análisis financiero y económico... acorde a los estándares de
calidad que se piden en estas herramientas en bancos de primer nivel".
Se releyó `engine/valuation.py`, `wacc_builder.py`, `ratios.py` y
`comps.py` completos, verificando cada fórmula contra teoría financiera
estándar (Damodaran, Rosenbaum & Pearl) y contra el propio Excel de
referencia — no una lista de "cosas que suenan a banco", una revisión
línea a línea. Detalle completo de cada hallazgo en `docs/AUDIT.md`
(I6, I7, M6, M7); resumen aquí:

**Confirmado correcto, con verificación explícita, no solo lectura:**
- Convención mid-year + stub: el valor terminal se descuenta con el
  mismo periodo que el último flujo explícito (`n-0.5`, no `n`) — la
  convención estándar de banca de inversión, y uno de los puntos donde
  un DCF construido sin cuidado suele fallar.
- Gordon Growth usa `FCFF_n × (1+g) / (WACC-g)`, no `FCFF_n / (WACC-g)`
  — evita el error de "off-by-one" más común de esta fórmula (olvidar
  crecer el último flujo antes de aplicar la perpetuidad).
- El WACC no tiene la circularidad clásica de un DCF (usa `market_cap`
  actual, observable y externo — no el equity value que el propio DCF
  produce, que crearía una referencia circular).
- FCFF no cuenta dos veces el escudo fiscal de la deuda: tributa sobre
  EBIT desapalancado, no sobre EBT — el ahorro fiscal de los intereses
  ya está capturado en el término `Rd×(1-t)` del propio WACC.
- El EBITDA es consistente en todo el pipeline: verificado con datos
  reales de 6 tickers (AMZN, MSFT, AAPL vía Alpha Vantage; KO, PG, JNJ
  vía yfinance) que `ebit + d_and_a` (usado en `run_dcf`/`comps.py`)
  coincide EXACTO (0.0% de diferencia) con el campo `ebitda` reportado
  por cada proveedor (usado en `ratios.py`) — no son dos definiciones
  divergentes por casualidad.

**Dos bugs reales encontrados y corregidos** (ninguno disparado por el
universo piloto actual, ambos alcanzables con inputs reales — empresa
sin deuda, año de EBITDA nulo): `cost_of_debt()` y `debt_to_ebitda()`
no protegían división por cero. El segundo era el más serio:
`ZeroDivisionError` no es subclase de `ValueError` en Python, así que
el `except ValueError` ya existente en `app.py` no lo habría capturado
— un traceback real en producción, no un mensaje controlado. Detalle
completo, incluida la reproducción exacta de cada bug antes de
corregirlo, en `docs/AUDIT.md` hallazgos I6/I7.

**Dos puntos de diseño abiertos, pendientes de decidir con datos
reales** (mismo estándar que M2 en su momento): (1) el tipo impositivo
se proyecta plano —media histórica de `lookback_years`— incluso dentro
del valor terminal a perpetuidad, pese a que el tipo EFECTIVO histórico
de Big Tech es muy volátil y sistemáticamente inferior al estatutario
(AMZN: 54.2%/19.0%/13.5%/19.7% en los últimos 4 ejercicios); (2)
`Debt/EBITDA` usa deuda bruta sin aclararlo en la etiqueta, cuando Net
Debt/EBITDA es al menos igual de común en la práctica bancaria real.
Ninguno de los dos se ha cambiado todavía — deliberadamente: cambiar
una calibración sin medir primero el impacto real repetiría el error
que ya se evitó una vez con `gordon_weight` (M2). Detalle completo en
`docs/AUDIT.md` hallazgos M6/M7.

**180 tests en total, todos en verde** (176 + 4: 1 en
`test_valuation.py`, 3 en `test_ratios.py`, incluida una verificación
de punta a punta de que el `ValueError` de `debt_to_ebitda()` sale
limpio desde `latest_ratio_snapshot()`, no solo desde la función
aislada).

## 26. Investigación de SEC EDGAR como tercer proveedor: hallazgo real, distinto del esperado (sesión 17)

Motivo: la cuota diaria de Alpha Vantage (25 peticiones/día,
compartida entre todos los visitantes de la app) se agotó durante la
sesión. El usuario preguntó por alternativas gratuitas, con una
condición explícita por delante de todo: "es importante que sea todo
datos fiables". Se investigó `data.sec.gov` (API `companyfacts`, XBRL)
como posible tercer proveedor antes de comprometerse a construirlo —
mismo criterio que cualquier otro cambio de esta sesión: verificar con
datos reales antes de decidir, no estimar el esfuerzo de oídas.

### Lo que SEC EDGAR sí resuelve bien

Gratis, sin API key, sin cuota diaria (solo una guía de buen uso de
~10 peticiones/segundo). Verificado con los 5 tickers de Big Tech:
`revenue`, `ebit` (`OperatingIncomeLoss`), `net_income`
(`NetIncomeLoss`), `total_assets` (`Assets`), `total_equity`
(`StockholdersEquity`), `current_assets`/`current_liabilities`,
`interest_expense` e `cash` reconstruyen con fidelidad muy alta o
exacta frente a los datos ya usados. Un problema real y esperado: las
etiquetas de la taxonomía US-GAAP cambian por empresa y por año (AMZN
usa `RevenueFromContractWithCustomerExcludingAssessedTax` para
ingresos, no el más simple `Revenues`) — requiere listas de fallback
por concepto, no una etiqueta única.

### Lo que NO se pudo verificar con la confianza necesaria

`d_and_a`: algunas empresas (AMZN, META, AAPL) reportan una única línea
combinada de D&A en el cash flow; otras (MSFT, GOOGL) la reportan
partida en 3+ líneas separadas (`Depreciation`,
`AmortizationOfIntangibleAssets`, `FinanceLeaseRightOfUseAssetAmortization`,
posiblemente más), y sumarlas para MSFT dio un número que seguía sin
cuadrar con Alpha Vantage (hasta 18% de diferencia, incluso probando
distintas combinaciones). No se encontró una reconstrucción fiable
antes de que la investigación tomara un giro más importante (ver
abajo) — se dejó de intentar en vez de forzar una aproximación sin
verificar, exactamente el mismo criterio que llevó a descartar la
sustitución automática de outliers en la sección 21.

### El giro: la investigación reveló un bug real en el pipeline YA existente, más urgente que el problema original

Al intentar validar `total_debt` de AMZN vía SEC EDGAR contra Alpha
Vantage y yfinance para decidir qué "definición de deuda" replicar,
las tres fuentes dieron números muy distintos: SEC EDGAR (solo deuda
financiera) $68.8bn, Alpha Vantage $153.0bn, yfinance (`.info`)
$251.6bn. Investigado a fondo (no aceptado como "ambigüedad
inevitable"): **Alpha Vantage's `shortLongTermDebtTotal` = `longTermDebt`
+ `capitalLeaseObligations`, exacto al dólar ($65.648bn + $87.339bn =
$152.987bn)** — no es un error, incluye deliberadamente las
obligaciones de leasing (post ASC 842) además de la deuda financiera
pura. Y el propio `ticker.balance_sheet` de **yfinance** (su
estado financiero detallado, no `.info`) da exactamente el mismo
$152.987bn — coincide con Alpha Vantage al dólar. El campo
`ticker.info["totalDebt"]` que SÍ usaba `market_snapshot()`
(`engine/yfinance_provider.py`) es una fuente distinta, menos curada,
que diverge del propio balance sheet de yfinance — un bug real dentro
de nuestro propio código (I8, `docs/AUDIT.md`), no una diferencia de
metodología entre proveedores. `historical_financials()` de ese mismo
módulo ya usaba correctamente `.balance_sheet` — sólo `market_snapshot()`
tenía el problema, nunca contrastado hasta ahora entre las dos rutas
del mismo archivo.

**Corregido** (ver I8): `market_snapshot()` ahora lee `cash`/`total_debt`
de `ticker.balance_sheet`, con `.info` solo como último recurso. Efecto
medido con datos reales sobre el universo piloto (`Consumo defensivo`,
el único que usa yfinance en producción): pequeño (KO -20.4%→-22.3%,
PG y JNJ casi sin cambio) porque ninguno de los tres tiene una carga de
leasing tan grande como AMZN — pero el bug era real y, en modo
"cualquier ticker" con una empresa intensiva en leasing (retail,
aerolíneas, restauración), habría producido un WACC calculado sobre una
cifra de deuda hasta un 65% más alta de la real.

### Decisión: SEC EDGAR queda en pausa, no descartado

El usuario decidió priorizar investigar y corregir esta inconsistencia
(I8) antes de seguir con SEC EDGAR — la construcción del tercer
proveedor no se ha retomado todavía. Queda documentado el trabajo real
ya invertido (qué tags funcionan, cuáles no, y por qué) para no tener
que rehacerlo si se retoma más adelante.

## 27. Prueba de estrés "como si un banco la usara": 11 tickers reales fuera del universo piloto (sesión 17)

Petición explícita del usuario: probar la herramienta con más empresas,
"como si de verdad un banco fuese a usarla", para encontrar y diseñar
un análisis de debilidades y fortalezas real. Hasta ahora toda la
validación (Fase 7, secciones 7-16) se había hecho sobre el mismo
universo piloto de 8 empresas (Big Tech + Consumo defensivo) — nunca
contra un conjunto deliberadamente diverso y desconocido para el motor.

### Metodología

11 tickers elegidos a propósito para estresar ejes distintos, ninguno
en el universo piloto: **NVDA** (hiper-crecimiento extremo), **TSLA**
(alta volatilidad de márgenes), **XOM** (energía, cíclico de
comodities), **UNH** (salud), **CAT** (industrial cíclico), **SBUX**
(consumo, mucho leasing), **T** (telecom, muy apalancado), **BA**
(pérdidas reales recientes, muy apalancado), **PLD** (REIT), **JPM**
(banco), **BABA** (ADR chino, para reconfirmar M5). Ejecutados en modo
"cualquier ticker" contra la app REAL vía `streamlit.testing.v1.AppTest`
**sin mocks, con red real** — a diferencia de `tests/test_app.py`
(offline por diseño), esto ejercita el código exactamente como lo haría
un usuario real tecleando un símbolo.

### Resultado: 3 de 11 tickers (27%) crashearon con un traceback real

**XOM** (yfinance no reporta D&A para esta empresa), **PLD** (REIT, sin
CapEx en el esquema esperado) y **JPM** (banco, sin EBIT ni CapEx
tradicionales) hacían `crashear` con `IndexError: list index out of
range` en `_margin_fade_from_recent_to_average()` — una columna
histórica sin NINGÚN dato válido, nunca contemplada. El fallo ocurre en
la sección de cómputo compartida por los dos modos de la app, fuera de
cualquier `try/except` existente (I3 y I5 protegen tramos anteriores,
no este). **Corregido** (I9, `docs/AUDIT.md`): `ValueError` explícito
nombrando la partida sin datos + `try/except` alrededor de la primera
llamada de cómputo compartida.

### Segundo resultado: valores terminales sin sentido, sin ningún aviso

- **NVDA**: Gordon Growth = **$21.7 billones** (trillion en inglés),
  más que el PIB mundial. Causa investigada: el CAGR reciente de NVDA
  es de verdad ~100%/año (demanda de chips de IA, no un error), y la
  metodología de crecimiento plano dentro del horizonte explícito
  (sección 14, correcta para AMZN ~10-11%) compone eso durante 5 años
  seguidos sin ninguna desaceleración — ingresos año 5 de $6.9
  billones, 30x cualquier ingreso empresarial real. **Dejado abierto a
  propósito** (no hay todavía un umbral objetivo de "growth rate
  demasiado alto" verificado con datos, y forzar uno sin ese rigor
  repetiría exactamente lo que se evitó con M2).
- **TSLA** (-$45.9bn) y **BA** (-$184.1bn): valor terminal de Gordon
  Growth NEGATIVO. Investigado con el desglose completo del UFCF: en
  TSLA, el ΔNWC proyectado (consumo de caja creciente) supera a
  EBIT·(1-t)+D&A-CapEx todos los años; en BA, el margen EBIT revierte
  hacia la media de un histórico con dos años de pérdidas reales
  (737 MAX, pandemia). Matemáticamente consistente con la fórmula, pero
  sin ningún aviso al usuario. **Corregido** (I10): aviso explícito
  cuando el FCFF del último año explícito es negativo o cero,
  explicando el mecanismo probable — no se ajusta ningún número.

### Tercer resultado: confirmación de un límite estructural ya conocido en la teoría, nunca antes encontrado en la práctica

JPM (banco) y PLD (REIT) no solo dispararon el crash de I9 — confirman
que un DCF FCFF genérico no encaja con estos sectores: los bancos no
separan financiación de operación de la misma forma (los intereses SON
el negocio), y los REITs se valoran en la práctica real con FFO/AFFO,
no UFCF. Documentado (I11) como limitación estructural aceptada, mismo
criterio que I2/I4 — no una limitación de esta herramienta en
particular, sino de la metodología DCF FCFF aplicada fuera de su
dominio.

### El resto del universo de estrés funcionó correctamente

NVDA, TSLA, UNH, CAT, SBUX, T y BA (una vez con el aviso I10 añadido)
corrieron de punta a punta sin excepción, con avisos técnicos
pertinentes en cada caso (outliers de ancla, spread WACC-g estrecho) —
el mecanismo de avisos de las secciones 21/22 funcionó exactamente como
se diseñó sobre tickers nunca antes probados. BABA confirmó M5
(bloqueo por divisa, CNY) en un ADR distinto de los ya probados (Toyota).

### Verificación

187 tests en total (182 + 5: 1 en `test_projections.py`, 3 en
`test_valuation.py`, 1 en `test_app.py`), más la prueba de estrés en sí
(fuera de la suite de CI por diseño, ya que depende de red real) —
documentada aquí para que sea reproducible sin tener que rehacerla
desde cero.

## 28. DAFO y lotes de mejora: Net Debt/EBITDA, aviso de sector, tipo impositivo (Excel real), aviso de hiper-crecimiento (sesión 17)

Petición del usuario: un DAFO del estado de la herramienta (entregado
en el chat), y trabajar sus debilidades/oportunidades en lotes,
priorizados de mayor a menor certeza/menor riesgo.

### Lote A — ganancias rápidas

**M7 cerrado**: `engine.ratios.net_debt_to_ebitda()` nueva (mismo guard
de EBITDA<=0 que la versión bruta, pero un resultado negativo aquí SÍ
es interpretable — caja neta positiva). `RatioSnapshot` expone ambas
versiones; la UI las rotula explícitamente ("Deuda bruta/EBITDA" /
"Deuda neta/EBITDA"); el memo usa la neta como cifra principal de
apalancamiento.

**Aviso proactivo de sector incompatible** (mejora sobre I11): la app
ahora muestra un `st.warning` en cuanto detecta `sector` conteniendo
"financial", "real estate", "bank" o "insurance" — ANTES de que el
cómputo llegue a fallar (I9), no solo después. Verificado con JPM
("Financial Services") y PLD ("Real Estate") reales.

### Lote B — decisiones investigadas con el mismo rigor que M2

**M6 (tipo impositivo a largo plazo) — resuelto con la evidencia más
fuerte de todo el proyecto hasta ahora.** Primero se midió el impacto
de un fade hacia el 25% estatutario en los 5 tickers de Big Tech:
-8.5% (AAPL) a -19.0% (GOOGL) — empeoraría la brecha ya documentada, lo
cual por sí solo no es motivo para descartarlo (el proyecto nunca
decide por "qué acerca más al precio de mercado"). Así que se buscó
evidencia independiente: se abrió `Advanced DCF.xlsx` con `openpyxl` y
se leyeron las celdas REALES de tipo impositivo proyectado para AMZN
(hoja "Operating Model", fila 52 "% tax rate"; hoja "North America",
fila 19) — el analista de JPM proyecta **17.75% (2024) → 15.28% (2025)
→ 16.13% (2026) → 17.03% (2027) → 17.03% (2028) → 16.64% (2029)**: una
banda estrecha de ~15-18%, SIN ninguna convergencia hacia el ~25%
estatutario, ni en los años más lejanos del horizonte. El propio banco
de referencia ancla el tipo impositivo cerca del nivel reciente
observado. **Decisión: se mantiene el tipo histórico plano, sin fade**
— no por intuición, sino porque es literalmente lo que hace "el modelo
a seguir" (principio del blueprint), verificado celda a celda.

**I12 (hiper-crecimiento extremo, NVDA) — corrección parcial: aviso,
no umbral de ajuste.** No existe un umbral objetivo verificable con
datos de "a partir de qué CAGR el flat-growth deja de ser razonable" —
inventar uno con precisión falsa habría repetido el error que el
proyecto evitó con M2. Se añadió `EXTREME_FLAT_GROWTH_WARNING_THRESHOLD
= 0.50` (50%/año) en `engine/projections.py`, una regla de pulgar
deliberadamente conservadora (mismo espíritu que
`MIN_PRUDENT_WACC_GROWTH_SPREAD`) que avisa —sin ajustar ningún
número— mencionando el múltiplo real al que compone el CAGR plano hacia
el año N. Verificado con datos reales: NVDA ("100%/año... multiplica
los ingresos por 32.0x hacia el año 5") dispara el aviso; AMZN, MSFT,
TSLA y BA (5%-21% de CAGR) no.

### Verificación

193 tests en total, todos en verde (187 + 6: 3 en `test_ratios.py`
para Net Debt/EBITDA, 1 en `test_app.py` para el aviso de sector, 2 en
`test_projections.py` para el aviso de hiper-crecimiento). Ambos avisos
nuevos verificados con `AppTest` contra la app real (JPM/PLD para el
aviso de sector, NVDA para el de hiper-crecimiento) — aparecen en el
panel "Aviso técnico del modelo" tal como se diseñaron.

## 29. Monte Carlo: bandas de confianza probabilísticas (sesión 17, Lote C)

Última de las 5 palancas de rigor matemático propuestas al principio de
la sesión 17 (junto a A/B, ya construidas). En vez de un precio único +
3 escenarios con nombre, `engine/monte_carlo.py::run_monte_carlo()`
corre el DCF completo 2.000 veces, muestreando 4 supuestos A LA VEZ
desde su propia dispersión histórica real (no un rango inventado):
margen EBIT y CapEx % ventas (`Normal(media, sigma)` medidos sobre la
misma ventana `lookback_years` que usa el fade por defecto, nueva
`engine.projections.historical_ratio_stats()`), crecimiento de ingresos
(`Normal` sobre la dispersión real de las tasas año a año, nueva
`historical_revenue_growth_stats()`) y tasa de crecimiento terminal g
(`Normal(g, 0.5pp)`, incertidumbre macro fija y pequeña, no de la
empresa). WACC y D&A/ΔNWC se mantienen fijos — alcance deliberado,
documentado en el propio módulo, no un hueco escondido.

### Dos problemas reales encontrados y corregidos antes de dar la simulación por buena

**1. Ventana de dispersión del crecimiento inconsistente con la de
margen/CapEx.** Primera versión: `historical_revenue_growth_stats()`
medía la desviación típica sobre TODO el histórico disponible.
Verificado con AMZN real (20+ años vía Alpha Vantage): eso mezcla la
era de hiper-crecimiento inicial (2005-2010) con el régimen actual,
inflando la sigma de crecimiento a 9.9pp (media 25.1%) frente al ~1pp
que da la misma ventana de 3 años que ya usan margen/CapEx — y producía
precios simulados absurdos. Corregido: `historical_revenue_growth_stats()`
acepta ahora `lookback_years`, alineado con el resto de supuestos.

**2. Precios implícitos negativos en la cola de la distribución.**
Incluso con la ventana corregida, algunos draws (margen bajo + CapEx
alto a la vez, ambos dentro del rango real observado de AMZN) siguen
dando un FCFF terminal negativo — mismo mecanismo que I10 (TSLA/BA) —
y por tanto un "precio" negativo. Verificado con AMZN real: sin
protección, P10 salía en -$34, un número sin sentido económico para un
accionista de responsabilidad limitada (nunca pierde MÁS que su
inversión). Corregido: cada precio simulado se flota en $0 (no se
descarta la simulación, cuenta como éxito) — deliberadamente NO
aplicado en `run_dcf()` ni en los escenarios con nombre, donde un valor
terminal negativo real (I10) es información diagnóstica útil sobre un
caso concreto, no ruido a limpiar en una distribución de miles de
draws.

### Resultado real, verificado con AMZN

Tras ambas correcciones: **P10=$17.88, P50=$115.45, P90=$214.74**
(media $118.36, sigma $72.51), con **6% de las simulaciones cayendo en
$0** — destrucción total de valor bajo una combinación de margen/CapEx
extremos pero dentro de la dispersión histórica real de la propia
empresa. Esto es, en sí mismo, un hallazgo cuantitativo honesto: la
propia dispersión reciente de AMZN en margen (dominado por el
supercycle de CapEx de IA, secciones 7/21) es tan grande que una cola
no despreciable de escenarios plausibles destruye el valor del equity
bajo este DCF — exactamente el tipo de información que un punto único
nunca comunica.

### Dónde vive en la app

`app/streamlit_app.py`, pestaña "Valoración", justo debajo del football
field: histograma Plotly de los 2.000 precios simulados, líneas P10/
P50/P90 y precio de mercado, métricas P10/P50/P90 y caption con el %
de simulaciones en el suelo de $0 cuando es material (>2%). Envuelto en
`try/except ValueError` — si todos los draws de g terminal cruzan WACC
(caso extremo), degrada a un caption explicando por qué en vez de
crashear o bloquear el resto de la valoración ya calculada.

### Verificación

9 tests nuevos en `test_monte_carlo.py` (orden P10<P50<P90,
reproducibilidad con semilla fija, CapEx nunca negativo, suelo de $0
verificado explícitamente, fallo explícito si todos los draws fallan) +
2 en `test_projections.py` (`historical_ratio_stats`,
`historical_revenue_growth_stats` con y sin `lookback_years`) + 2 en
`test_app.py` (degradación con historial corto vía `AppTest`, render
completo con historial suficiente). **209 tests en total, todos en
verde.** Verificado de punta a punta con datos reales de AMZN vía
`AppTest` sin mocks (red real): 2.1s de carga total de página,
incluidas las 2.000 simulaciones — rendimiento aceptable para
recalcularse en cada interacción de Streamlit (el modelo de rerun
completo del framework).
