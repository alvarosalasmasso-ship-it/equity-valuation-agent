# Auditoría técnica — Agente de valoración DCF

**Fecha:** 2026-09-05 (sesión 15), extendida 2026-09-06 (sesión 17).
**Alcance:** todo `engine/`, `ai/`, `app/`, `tests/`, comparado
sistemáticamente contra `Advanced DCF.xlsx` (el modelo profesional de
referencia). Metodología: relectura completa del código (no solo
memoria de sesiones anteriores) + verificación puntual de cada hallazgo
con datos reales antes de reportarlo — mismo estándar que se ha
aplicado en toda la sesión. La sesión 17 añadió un pase específico de
rigor matemático/financiero (fórmula a fórmula contra teoría estándar,
no solo revisión de código) a petición explícita del usuario.

Cada hallazgo indica: qué es, por qué importa, y si se ha verificado
con evidencia concreta (no solo inspección de código).

---

## Resumen ejecutivo

El motor de cálculo (`engine/valuation.py`) está validado **exacto**
contra el Excel a nivel de fórmula — WACC, FCFF, descuento, TSM, valor
terminal blended reproducen el precio real ($216.41) al céntimo. Esa
parte es sólida y no tiene hallazgos nuevos.

Los hallazgos reales de esta auditoría están en la **orquestación**: piezas
del motor que están bien construidas y testeadas de forma aislada, pero
que el pipeline real (`scenarios.py`, `validation.py`, `app/streamlit_app.py`)
no termina de aprovechar, o usa con supuestos congelados que ya no
reflejan "hoy". Es el mismo patrón que los bugs de `interest_expense`
y de serialización JSON encontrados en sesiones anteriores: no rompen
nada de forma visible, pero sí introducen un sesgo silencioso.

**2 hallazgos críticos (✅ ambos corregidos — C2, sesión 17, es el hallazgo de mayor impacto de todo el proyecto: "EBIT" de ambos proveedores incluía partidas no operativas, sobrevalorando el input central de cada DCF desde el inicio), 16 importantes (11 ✅ corregidos —
I6/I7/I8 de la auditoría matemática/financiera a fondo, I9/I10 de una
prueba de estrés con 11 tickers reales ("como si un banco fuese a
usarla"), I12 (aviso de hiper-crecimiento extremo, corrección parcial:
avisa, no ajusta el número), I13 (precio de mercado de Alpha Vantage
mal para GOOGL/META, corrige la desviación media del universo piloto de
41.82% a 34.21%), I15 (tax_rate proyectado sin detección de outliers,
corrección parcial: avisa cuando el resultado final cae fuera de una
banda plausible, sin ajustar el número — la exclusión automática se
probó y se rechazó, dispara falsos positivos en 9 de 24 tickers reales)
—, 1 ✅ corregido parcialmente con detección objetiva — I16 (el motor
revertía SIEMPRE a la media histórica, incluso contra una tendencia
estructural real y sostenida; ahora la mantiene cuando hay evidencia
objetiva, R²≥0.70 verificado contra 25 tickers reales, sin extrapolar
más allá — mismo criterio "avisar/ajustar solo con evidencia, nunca
extrapolar sin límite" que I12/I15), 4 ✅ aceptados/documentados como
limitación — I2, I4 (con nueva evidencia real de contaminación de WACC
entre comparables heterogéneos, grupo de semiconductores), I11 (DCF
FCFF no encaja con bancos/REITs), I14 (reverse DCF solo resuelve
crecimiento, no margen — hallazgo real con AMD)), 9 moderados (**todos
cerrados**: 8 ✅ corregidos incluido M7 en el Lote A, M8 (evaluación
del sector Utilities: yfinance no reportaba D&A para D bajo la etiqueta
estándar) y M9 (grupo small/mid-cap: `build_peer_wacc()` sin el mismo
guard de `interest_expense`/`tax_rate` vacíos que ya tenía el modo
"cualquier ticker"), 1 ✅ decisión explícita investigada — M2 (con nueva
evidencia de fragilidad sistemática de Gordon Growth en todo el sector
Utilities, no solo un caso puntual), y M6 recién investigado con
evidencia directa del Excel de referencia y también cerrado), 3
informativos (1
✅ corregido — N2 —, 2 sin acción necesaria), más dos entradas nuevas no
correctivas de la sesión 18 (N4 ampliada en sesión 19) — N4: supuestos
del analista, un 4º escenario opcional construido a mano por el
usuario, al lado de los 3 objetivos, nunca mezclado en silencio con
ellos; N5: Alpha Vantage retirado como fuente automática por defecto
de la app en sesión 18, y eliminado del proyecto por completo en
sesión 19, a petición explícita del usuario, en favor de yfinance (sin
límite de cuota). Con esto, **no queda
ningún hallazgo moderado o importante abierto** — I12 e I15 se
corrigieron parcialmente (avisan, sin ajuste automático por falta de
evidencia objetiva suficiente para justificarlo); I16 se corrigió
parcialmente con ajuste objetivo (mantiene el nivel actual con
evidencia de tendencia, sin extrapolar), mismo criterio en
ambos casos).**
La sesión 17 añadió una auditoría explícita del rigor matemático y
financiero del motor (`engine/valuation.py`, `wacc_builder.py`,
`ratios.py`, `comps.py`) verificado fórmula a fórmula contra teoría
financiera estándar y contra el propio Excel de referencia — confirmó
correcto lo más crítico (convención mid-year + stub, fórmula de Gordon
Growth, ausencia de circularidad en WACC, ausencia de doble conteo del
escudo fiscal, consistencia de EBITDA entre módulos) y encontró dos
bugs reales no detectados hasta entonces (I6, I7) más dos puntos de
diseño que merecen una decisión explícita, no silenciosa (M6, M7).
"Cerrado" no significa "arreglado con código" en todos los casos: I2 e
I4 son limitaciones estructurales aceptadas (sin fuente de datos
gratuita para resolverlas), M2 es una constante que se investigó a
fondo y se decidió mantener, y M6/M7 siguen abiertos a propósito hasta
investigarlos con el mismo rigor.

---

## Crítico

### C1. El "stub period" nunca se calcula en el pipeline real — ✅ CORREGIDO (sesión 15, misma sesión)

**Qué es:** `discount_periods()` y `pv_of_cash_flows()` soportan un
`stub_fraction` — la fracción del primer año fiscal que queda entre la
fecha de valoración y el cierre del ejercicio (validado exacto contra
el Excel, sesión 2: `stub=0.1667` para una valoración de noviembre).
`DCFInputs.stub_fraction` tiene un default de `1.0`.

**El problema:** ningún sitio del pipeline real (`engine/scenarios.py`,
`engine/validation.py`, `ai/memo_generator.py`, `app/streamlit_app.py`)
calcula jamás un stub real a partir de la fecha de hoy. Verificado por
grep: `stub_fraction` solo aparece definido y documentado dentro de
`engine/valuation.py`, en ningún sitio más.

**Por qué importa:** cada valoración que produce la herramienta asume
implícitamente que "hoy" es el 1 de enero del primer año proyectado
(stub=1.0, sin convención de mitad de año en el primer periodo). Si se
ejecuta hoy (septiembre), el stub real sería ~4/12 ≈ 0.33, no 1.0 — el
primer flujo de caja se está descontando por más tiempo del que
corresponde, lo que **infravalora sistemáticamente** el precio implícito
en una cantidad pequeña pero real y sistemática, mayor cuanto más
avanzado esté el año en que se ejecuta la herramienta.

**Verificado:** sí, por inspección directa del código y confirmado que
no hay ningún cálculo de fecha (`datetime`, `date.today()`) en ninguno
de los módulos de orquestación.

**Corregido:** `engine.valuation.compute_stub_fraction(fiscal_year_end_month, fiscal_year_end_day, valuation_date)`
calcula la fracción real por días de calendario (convención
actual/actual, no el 30/360 de `YEARFRAC` del Excel — ambas dan una
fracción de año equivalente en la práctica). `historical_financials()`
en ambos proveedores de datos (`data_provider.py`, `yfinance_provider.py`)
ahora expone `fiscal_year_end_month`/`fiscal_year_end_day` (parseado de
`fiscalDateEnding`). `engine.projections.stub_fraction_from_history()`
une ambas piezas (degrada a `1.0` si el histórico no trae esas columnas,
p.ej. fixtures sintéticas — sin romper ningún test existente).
`run_scenarios()` y `value_ticker()` aceptan `valuation_date` (por
defecto, hoy) y calculan el stub automáticamente; `app/streamlit_app.py`
añade un `date_input` en la sidebar y muestra el stub calculado.

**Verificado con datos reales, no solo con tests:** AMZN (cierre fiscal
31-dic), valorado el 5-sept-2026 → stub=0.3205 (117 días reales hasta
el cierre, sobre 365) → precio implícito **$104.41 → $108.78 (+4.19%)**
frente al comportamiento anterior (stub=1.0 implícito). Confirma la
dirección predicha: el bug infravaloraba sistemáticamente. Verificado
también con MSFT (cierre fiscal 30-jun, no diciembre) y con el caso
límite de que "hoy" ya haya pasado el último cierre reportado (salta
correctamente al ejercicio siguiente, sin devolver una fracción &gt;1
ni negativa).

12 tests de regresión nuevos (`compute_stub_fraction`,
`stub_fraction_from_history`, wiring en `run_scenarios`/`value_ticker`).
109 tests en total, todos en verde.

---

### C2. El campo "EBIT" de Alpha Vantage y yfinance NO es Operating Income — incluye partidas no operativas, sobrevalora sistemáticamente — ✅ CORREGIDO (sesión 17, retomando SEC EDGAR como validador cruzado)

**Qué es:** construyendo `engine/edgar_provider.py` como validador
cruzado contra SEC EDGAR (petición explícita del usuario tras observar
que la mayoría de bugs de código de esta sesión —M8, M9, I13— eran
huecos/inconsistencias del proveedor de datos, no errores del motor),
la primera comparación real (MSFT) mostró que **8 de 9 conceptos
coinciden EXACTOS** entre el proveedor activo y SEC EDGAR (revenue,
net_income, total_assets, total_equity, current_assets,
current_liabilities, cash, interest_expense — diferencia 0.00% en
todos) — una validación fuerte de que el resto del pipeline de datos
es correcto. Pero **`ebit` mostró +8.86%**: $168.985bn (yfinance/Alpha
Vantage) vs $155.237bn (SEC EDGAR `OperatingIncomeLoss`, que también
coincide exacto con el "Operating Income" que ambos proveedores YA
tienen disponible, sin usar).

**Causa raíz**: tanto Alpha Vantage (`inc["ebit"]`) como yfinance
(`inc["EBIT"]`) calculan ese campo como `incomeBeforeTax +
interestExpense` — matemáticamente correcto como definición literal de
"Earnings Before Interest and Taxes", pero esa fórmula **incluye
cualquier partida no operativa** que ya esté dentro de `incomeBeforeTax`
(ganancias/pérdidas de inversión, resultado por método de
participación, otros ingresos/gastos no operativos) — exactamente lo
que un DCF de flujo de caja libre debe EXCLUIR, porque el objetivo es
valorar el negocio operativo recurrente, no ganancias de inversión no
recurrentes. Ambos proveedores YA exponen el campo correcto por
separado (`operatingIncome` / `"Operating Income"`), pero el código
nunca lo usaba — `engine/data_provider.py` preferían `ebit` explícitamente,
`engine/yfinance_provider.py` leía directamente `"EBIT"`.

**Magnitud verificada con datos reales, no solo MSFT** — comparación
directa "EBIT" vs "Operating Income" (yfinance, 7 tickers reales):

| Ticker | Operating Income | "EBIT" (Pretax+Interest) | Diferencia |
|---|---|---|---|
| AAPL | $133.05bn | $133.05bn | +0.00% |
| MSFT | $155.24bn | $168.99bn | **+8.86%** |
| NVDA | $130.39bn | $141.71bn | **+8.68%** |
| META | $83.28bn | $87.10bn | **+4.59%** |
| KO | $14.91bn | $17.65bn | **+18.38%** |
| AMZN | $79.98bn | $99.59bn | **+24.52%** |
| GOOGL | $129.04bn | $159.56bn | **+23.65%** |
| JNJ | $25.60bn | $33.55bn | **+31.08%** |

Solo AAPL no muestra diferencia (sin ingresos de inversión/no
operativos materiales ese ejercicio) — **6 de 7 compañías reales
muestran una inflación sistemática de doble dígito**. Esto es, con
diferencia, el input individual más importante del motor (EBIT
alimenta directamente `unlevered_fcf()`), y el bug estaba presente en
AMBOS proveedores desde el inicio del proyecto, no solo en esta sesión.

**Evidencia decisiva del Excel de referencia** (mismo criterio que
M6): `North America` (fila 15, "EBIT") lee `='Operating Model'!K34`,
que a su vez lee `=Segments!K12` — el Operating Income reportado POR
SEGMENTO de AMZN, una cifra puramente operativa por construcción (el
reporting de segmentos bajo US GAAP no incluye partidas corporativas
no operativas). El banco de referencia nunca usó "pretax + interest"
como EBIT — confirma que "Operating Income" es la cifra
metodológicamente correcta, no una elección arbitraria entre dos
igual de válidas.

**Efecto real medido, universo piloto completo (n=8, recalculado sobre
la caché ya existente de Alpha Vantage, sin gastar cuota nueva)**:

| | Antes (EBIT=pretax+interest) | Después (Operating Income) |
|---|---|---|
| Desviación media abs. vs. mercado | 34.21% | **38.97%** |
| IC bootstrap 80% | [24.84%, 43.65%] | [31.43%, 46.75%] |
| GOOGL individual | -0.56% | **-16.72%** |
| AMZN individual | -56.4% | -62.8% |

**La desviación EMPEORÓ, no mejoró** — señal de anti-sobreajuste
importante: el fix no se hizo porque acercara el precio al mercado (se
aleja), se hizo porque es lo metodológicamente correcto, confirmado con
evidencia independiente (SEC EDGAR) y con el propio Excel de
referencia. Mismo principio que ha gobernado todo el proyecto desde
las primeras sesiones.

**Alcance del impacto**: afecta a TODAS las valoraciones de la
herramienta desde su inicio, ambos proveedores, cualquier empresa con
ingresos/gastos no operativos materiales -- incluye retroactivamente
todo el trabajo de "dogfooding" sectorial de esta misma sesión (MSFT,
semiconductores, utilities, biotech, small/mid-cap: sus cifras de
precio implícito, margen EBIT y ROIC quedan desactualizadas por este
fix, no por un error en el análisis de esos sectores en sí). No se
rehacen esos análisis completos por alcance/tiempo — quedan marcados
como referencia histórica del comportamiento de la herramienta, no
como cifras vigentes.

**Corregido**: ambos proveedores ahora prefieren `operatingIncome` /
`"Operating Income"`, con `ebit`/`"EBIT"` como fallback solo si el
campo preferido no está disponible (robustez, no el camino principal).
4 tests de regresión nuevos (preferencia verificada cuando ambos campos
están presentes; fallback verificado cuando falta el campo preferido,
en ambos proveedores). **239 tests en total, todos en verde.**
Documentado en detalle en `docs/METHODOLOGY.md` sección 33.

---

## Importante

### I1. Risk-free rate y prima de riesgo de mercado: constantes congeladas, no en vivo — ✅ CORREGIDO (sesión 15)

**Qué es:** `RISK_FREE_RATE = 0.03909` y `MARKET_RISK_PREMIUM = 0.0406`
en `app/streamlit_app.py`, copiadas literalmente del Excel de
referencia (WACC!F11, WACC!F13 — datos de ~noviembre 2024).

**Por qué importa:** el tipo libre de riesgo (rendimiento del bono a 10
años) se mueve de forma no trivial en 18-24 meses. Usar un valor
congelado de hace año y medio para valorar una empresa "hoy" introduce
un error sistemático en el WACC de todas las valoraciones, y no es
ajustable desde la interfaz (a diferencia de `terminal_growth_rate`,
`n_years`, etc., que sí son sliders).

**Verificado:** sí, por grep — son las únicas dos apariciones de esos
valores en toda la base de código, sin ninguna fuente de datos en vivo
detrás.

**Cómo se corrigió:** el risk-free rate y la prima de riesgo se tratan
de forma distinta a propósito, porque tienen disponibilidad de datos
distinta:

- **Risk-free rate:** sí tiene una fuente en vivo estándar y gratuita
  (rendimiento del Treasury a 10 años). Nuevo `treasury_yield_10y()` en
  `engine/yfinance_provider.py` (vía el índice `^TNX`) y
  `AlphaVantageClient.treasury_yield()` en `engine/data_provider.py`
  (función económica `TREASURY_YIELD`, como alternativa testeada pero
  no usada en la app por motivo de cuota — ver abajo). La app siempre
  usa la vía yfinance, incluso en modo "universo cacheado con Alpha
  Vantage": es un dato de mercado ambiental, igual para cualquier
  compañía, y así no se gasta la cuota de 25 peticiones/día de Alpha
  Vantage en algo que no depende del ticker. Si la consulta en vivo
  falla (sin red, Yahoo no disponible), cae a la constante congelada
  original con un aviso explícito en la interfaz — nunca fallaba en
  silencio, y sigue sin hacerlo.
- **Prima de riesgo de mercado (ERP):** no tiene un equivalente en vivo
  gratuito y fiable — el estándar del sector (series de Damodaran) se
  publica de forma manual y periódica, no vía una API estable. En vez
  de fingir una fuente en vivo que no existe, se convirtió en un
  `st.slider` ajustable en la sidebar, con el valor del Excel de
  referencia como valor por defecto documentado y explicado.

**Verificado con datos reales:** el 2026-09-06 el Treasury 10Y real
(vía `^TNX`) cotizaba a **4.784%**, frente al 3.909% congelado del
Excel (~noviembre 2024) — una diferencia de +0.875 puntos porcentuales,
nada trivial. Revalorando AMZN con el resto de supuestos idénticos: el
WACC pasa de 8.266% a 9.096%, y el precio implícito del escenario
conservador de **$108.80 a $98.00 (-9.93%)**. Confirma que el hallazgo
no era cosmético: la constante congelada estaba inflando materialmente
el precio implícito de todas las valoraciones de esta sesión (incluida
la propia validación del fix de C1, hecha con esa misma tasa vieja).
7 tests de regresión nuevos (4 en `data_provider.py`, 3 en
`yfinance_provider.py`). 118 tests en total, todos en verde.

### I2. El Treasury Stock Method está construido y validado, pero nunca se usa — ✅ DECISIÓN EXPLÍCITA (sesión 16): aceptado como limitación, no se corrige

**Qué es:** `treasury_stock_method()` / `diluted_shares_outstanding()`
en `engine/valuation.py`, validados exactos contra `Shares!E14` del
Excel (10,876.07M).

**El problema:** verificado por grep — ninguna llamada a estas
funciones existe fuera de `engine/valuation.py` y sus propios tests.
Todo el pipeline real usa `snap["shares_outstanding"]` en bruto
(directamente de Alpha Vantage/yfinance), sin ajuste por opciones
in-the-money ni convertibles.

**Por qué importa:** para compañías con programas de opciones/RSUs
grandes (típico en tech), el recuento diluido real puede ser
sensiblemente mayor que el "basic shares outstanding" reportado — el
precio objetivo implícito estaría inflado en esa proporción.

**Verificado:** sí, por grep. No verificado el impacto cuantitativo
(requeriría datos de tramos de opciones por ticker, que ni Alpha
Vantage ni yfinance exponen de forma limpia y gratuita — es la razón
real de que nunca se haya conectado).

**Cómo se arreglaría:** en la práctica, sin una fuente de datos de
opciones outstanding por tramo de precio de ejercicio, no hay mucho que
hacer salvo documentarlo como limitación conocida.

**Decisión explícita (sesión 16, "rigor técnico restante"):** se acepta
como limitación permanente del alcance actual, no como un hallazgo
pendiente de arreglo. Motivo: ni Alpha Vantage ni yfinance exponen
tramos de opciones outstanding por precio de ejercicio de forma
gratuita — no es una cuestión de tiempo de desarrollo, es una ausencia
de dato de entrada. Cerrar esto de verdad exigiría una fuente de pago
(p. ej. el propio 10-K/10-Q vía scraping o un proveedor premium), fuera
del alcance de un proyecto que corre enteramente sobre APIs gratuitas.
Para que la limitación sea visible donde importa, no solo en este
documento, `app/streamlit_app.py` ahora muestra una nota junto al precio
implícito indicando que las acciones diluidas usan el recuento básico
reportado, no Treasury Stock Method (ver el propio código para el texto
exacto).

### I3. Riesgos de excepción no controlados en modo "cualquier ticker" — ✅ CORREGIDO (sesión 15)

**Qué es:** en `app/streamlit_app.py`, la rama `else` (ticker arbitrario
vía yfinance) hace:
```python
rd = cost_of_debt(hist["interest_expense"].dropna().iloc[-1] or 0, ...)
...
re = cost_of_equity(RISK_FREE_RATE, snap["beta"], MARKET_RISK_PREMIUM)
```

**El problema:** si `hist["interest_expense"].dropna()` da una serie
vacía (posible con yfinance, que solo trae ~4 años de historia — más
probable en compañías con poco histórico), `.iloc[-1]` lanza
`IndexError` sin capturar. Si `snap["beta"]` es `None` (yfinance no
siempre calcula beta para compañías de baja liquidez o recién
salidas a bolsa), `cost_of_equity()` lanza `TypeError` al intentar
`None * MARKET_RISK_PREMIUM`. Ninguno de los dos casos está envuelto en
`try/except`, a diferencia de la sección de ratios más abajo en el
mismo archivo, que sí maneja `ValueError` limpiamente.

**Por qué importa:** la app crashearía (pantalla de error de Streamlit)
para cualquier ticker real que caiga en estos casos — que existen (small
caps, IPOs recientes, compañías con estructura de capital atípica).

**Corregido:** la construcción del WACC simplificado en modo "cualquier
ticker" queda envuelta en `try/except Exception` (amplio deliberado —
es un límite del sistema: entrada de usuario arbitraria contra una API
externa que no controlamos, no se puede enumerar de antemano cada fallo
posible). Se añaden comprobaciones explícitas con mensajes claros para
histórico vacío, beta ausente, gasto financiero ausente y tipo
impositivo ausente, antes de que cualquiera de esos huecos llegue a
`cost_of_equity()`/`cost_of_debt()`.

**Encontrado y corregido de raíz, no solo capturado:** al intentar
reproducir el fallo con un ticker real inexistente (`ZZZZINVALID`), se
descubrió que el crash real ocurre un nivel más abajo de lo esperado —
dentro de `historical_financials()` en ambos proveedores de datos
(`data_provider.py` y `yfinance_provider.py`): sin ningún año/fecha en
común entre los tres estados financieros, `pd.DataFrame([]).sort_values("fiscal_year")`
lanza `KeyError('fiscal_year')` en vez de devolver un DataFrame vacío
predecible. Corregido en ambos proveedores (devuelven un DataFrame
vacío con las columnas esperadas), no solo capturado en la capa de la
app — cualquier otro consumidor futuro de `historical_financials()` con
un ticker inválido se beneficia del mismo arreglo.

**Verificado con la API real, no solo con fixtures sintéticas:**
`ZZZZINVALID` vía yfinance ya no lanza excepción interna; el usuario ve
el mensaje "yfinance no devolvió estados financieros para
'ZZZZINVALID' — comprueba que el símbolo es correcto" en vez de una
pantalla de error de Streamlit. Las 4 comprobaciones (histórico vacío,
sin beta, sin interés, sin tipo impositivo) verificadas una a una con
datos sintéticos que fuerzan cada caso.

6 tests de regresión nuevos (2 de DataFrame vacío en los proveedores +
verificación manual de las 4 ramas de guarda). 111 tests en total.

### I4. Universo de comparables pequeño y no siempre homogéneo — ✅ DECISIÓN EXPLÍCITA (sesión 16): aceptado como limitación, no se corrige

Ya identificado y documentado en la sesión 14 (`docs/METHODOLOGY.md`
sección 16): con solo 3-5 comparables por grupo, un ticker con un
perfil de negocio distinto al resto del grupo (AAPL dentro de "Big
Tech", que en realidad es hardware premium frente a cloud/software)
recibe un múltiplo de salida poco representativo. Se incluye aquí
formalmente como hallazgo de auditoría, no solo nota de sesión — está
verificado con el impacto cuantitativo real ya medido (AAPL empeoró de
-56.1% a -63.0% de desviación tras el fix del múltiplo de peers).

**Decisión explícita (sesión 16):** se acepta como limitación del
alcance actual. Motivo: ampliar y segmentar bien un universo de
comparables por sector/subsector exige una taxonomía de industria de
calidad (GICS o similar) y suficientes tickers líquidos con datos
completos por subsector — ninguna de las dos cosas está disponible de
forma gratuita y fiable con Alpha Vantage/yfinance más allá de una
lista curada a mano como la actual. Ampliar la lista a mano (p. ej. de
5 a 15 tickers por grupo) no resolvería la heterogeneidad de fondo
(seguiría mezclando hardware con software "porque están en el mismo
índice popular"), así que no se persigue como arreglo de esta sesión.
Ya está señalado en la propia UI (`app/streamlit_app.py`, tabla de
comparables) que el múltiplo es la mediana de un grupo concreto, no de
un universo exhaustivo.

**Nueva evidencia cuantitativa (sesión 17, evaluación de un grupo real
de semiconductores — NVDA, AMD, AVGO, QCOM, INTC):** la heterogeneidad
del grupo no solo distorsiona el múltiplo de salida (ya documentado
arriba) — también **contamina el WACC de los miembros más
conservadores del grupo vía la beta de industria**. QCOM e INTC,
negocios maduros de riesgo mucho más bajo que el resto, heredan un WACC
de 12.57%/11.60% porque la beta de industria promedia sus betas reales
(1.68/2.23) con las de NVDA (2.22) y AMD (2.48) — betas extremas
propias de nombres de hiper-crecimiento muy volátiles, no del perfil de
QCOM/INTC. Mecanismo ya conocido en la práctica bancaria real (un
comparable de riesgo muy distinto sesga la beta de industria), pero
nunca antes cuantificado en este proyecto con un grupo curado a
propósito para exponerlo. Misma decisión que arriba: aceptado como
limitación del alcance actual, mismo motivo (sin taxonomía de industria
de calidad gratuita para segmentar comparables por subsector de riesgo
homogéneo).

### I5. Sin manejo de errores en modo "universo cacheado" — ✅ CORREGIDO (sesión 16)

**Qué es:** a diferencia del modo "cualquier ticker" (I3, con manejo de
errores desde la sesión 15), el modo por defecto de la app —el que usa
cualquier visitante que no toque nada, y el que comparte la cuota de 25
peticiones/día de Alpha Vantage entre TODOS los visitantes de la app ya
pública— no tenía ningún `try/except` alrededor de `load_av_universe()`
ni de la construcción del WACC. Encontrado y verificado por inspección
directa del código, no por especulación: `AlphaVantageError` (cuota
agotada) o cualquier fallo de red se propagaban sin capturar.

**Por qué importa:** con la app ya desplegada y pública, esto dejó de
ser un riesgo teórico — un visitante cualquiera agotando la cuota
compartida (o un fallo de red puntual de Alpha Vantage) vería un
traceback crudo de Streamlit en el camino principal de la herramienta,
no en un caso límite.

**Cómo se corrigió:** `try/except` alrededor de `loader(tuple(tickers))`
+ `build_peer_wacc(...)`, con un mensaje específico para
`AlphaVantageError` (menciona la cuota compartida y sugiere el grupo
"Consumo defensivo" o "Cualquier ticker" como alternativas que no
dependen de Alpha Vantage) y uno genérico para cualquier otro fallo.
Mismo principio que I3: límite del sistema (API externa que no
controlamos), excepción amplia deliberada.

**De paso, en la misma revisión de robustez:** `load_av_universe()` y
`load_yf_universe()` no tenían `ttl` en su `@st.cache_data` — el
resultado vivía tanto como el proceso de Streamlit Cloud (potencialmente
días sin reiniciar), dejando el precio de mercado y el consenso de
analistas congelados por accidente en una herramienta que presume de
"risk-free rate en vivo". Añadido `ttl=3600` (1h, consistente con
`get_live_risk_free_rate()`) — no agota la cuota de Alpha Vantage porque
su propio caché en disco (24h) sigue absorbiendo la mayoría de las
re-peticiones.

**Verificado:** 2 tests de regresión nuevos en `tests/test_app.py`
(`AlphaVantageError` y un `ConnectionError` genérico, cada uno
confirmando un `st.error` accionable y cero excepciones sin capturar) +
verificación visual con Playwright de que el camino feliz (AMZN) sigue
funcionando exactamente igual tras el cambio.

---

### I6. `cost_of_debt()` sin proteger `total_debt<=0` — ✅ CORREGIDO (sesión 17)

**Qué es:** auditoría en profundidad de `engine/valuation.py` a petición
del usuario ("revisa que todo tenga sentido... acorde a los estándares
de bancos de primer nivel"), verificando cada fórmula contra teoría
financiera estándar y el propio Excel de referencia. `cost_of_debt()`
(`interest_expense / total_debt`) no tenía guarda para `total_debt<=0`
— una empresa sin deuda hacía `crashear` la función con
`ZeroDivisionError`. Confirmado con reproducción directa
(`cost_of_debt(0.0, 0.0)` → `ZeroDivisionError`), no solo por
inspección.

**Por qué importa:** alcanzable en modo "universo cacheado"
(`build_peer_wacc()` en `app/streamlit_app.py`, sin ninguna guarda) —
a diferencia del modo "cualquier ticker", que ya usaba `... or 1` como
parche defensivo en el mismo punto, una protección **inconsistente**
entre las dos rutas de cálculo de WACC. Ninguno de los 8 tickers piloto
lo dispara hoy (todos tienen deuda), por lo que quedó sin detectar hasta
esta revisión explícita — pero una empresa sin deuda es un caso real,
no hipotético, y el manejo de errores de I5 solo lo habría capturado de
forma genérica (mensaje poco claro: "float division by zero"), no
evitado.

**Cómo se corrigió:** `cost_of_debt()` devuelve `0.0` cuando
`total_debt<=0`, con el motivo documentado en el propio docstring: es
seguro porque `wacc()` pondera el coste de la deuda por
`total_debt/(total_debt+market_cap)`, que también es 0 en ese caso — el
valor devuelto nunca influye en el resultado final, no es un ajuste
silencioso de nada que importe. Se simplificó también el parche `or 1`
de `app.py` (ya innecesario) a `or 0`, consistente con el resto del
código, y `build_peer_wacc()` ahora usa `snap.get("total_debt") or 0`
en vez de `snap["total_debt"]` sin guarda (protege además contra
`None`, no solo contra `0`).

**Verificado:** test de regresión en `tests/test_valuation.py`
(`cost_of_debt(0.0, 0.0) == 0.0`); suite completa (187 tests) y
`tests/test_app.py` en verde tras el cambio.

---

### I7. `debt_to_ebitda()` sin proteger `EBITDA<=0` — ✅ CORREGIDO (sesión 17)

**Qué es:** mismo pase de auditoría que I6. `debt_to_ebitda()`
(`total_debt / ebitda`) no protegía `ebitda<=0` — un año de
break-even o pérdida operativa antes de D&A hacía `crashear` la función
con `ZeroDivisionError` (`ebitda=0`) o devolvía un ratio negativo sin
sentido interpretable (`ebitda<0`, un Debt/EBITDA "negativo" no
significa "menos apalancado").

**Por qué importa — el hallazgo más serio de los dos:**
`ZeroDivisionError` **no es una subclase de `ValueError`** en Python.
`latest_ratio_snapshot()` se llama en `app.py` dentro de un
`except ValueError:` que ya existe (pensado para "no hay ningún año
con datos completos") — ese `except` **no habría capturado** el
`ZeroDivisionError` de un año con EBITDA exactamente 0. Habría sido un
traceback real y sin capturar en la pestaña "Fundamentales" en
producción, no un mensaje de error controlado — confirmado
reproduciendo el camino completo (`latest_ratio_snapshot()` con
`ebitda=0` en el último año, que sí pasa el `dropna()` porque 0 no es
`NaN`).

**Cómo se corrigió:** `debt_to_ebitda()` ahora lanza `ValueError`
explícito con `ebitda<=0` (mensaje: "EBITDA no positivo... no es un
ratio interpretable de la forma habitual"), en vez de devolver un valor
trivial seguro — a diferencia de I6, aquí no hay un "0.0 inofensivo"
posible porque el ratio se muestra directamente al usuario, no se
pondera a cero en ningún sitio. `ValueError` es exactamente el
contrato que `app.py` ya espera de esta ruta, así que no hizo falta
tocar `app.py` — el `except ValueError` ya existente ahora sí lo
captura.

**Verificado:** 3 tests de regresión nuevos en `tests/test_ratios.py`
(EBITDA=0 lanza `ValueError`; EBITDA negativo lanza `ValueError`; y un
test de punta a punta vía `latest_ratio_snapshot()` con un año de
EBITDA=0 real, confirmando que el `ValueError` sale limpio de todo el
pipeline, no solo de la función aislada).

---

### I8. `market_snapshot()` de yfinance usaba `info["totalDebt"]`/`["totalCash"]`, campos no fiables — ✅ CORREGIDO (sesión 17)

**Qué es:** al investigar la viabilidad de un tercer proveedor de datos
(SEC EDGAR, a petición del usuario tras agotarse la cuota de Alpha
Vantage), se necesitaba una fuente de verdad independiente contra la
que contrastar "deuda total". Comparando SEC EDGAR, Alpha Vantage y
yfinance para AMZN salió un desajuste enorme: `ticker.info["totalDebt"]`
daba **$251.6bn**, frente a `ticker.balance_sheet.loc["Total Debt"]`
(el propio balance detallado de yfinance) que daba **$153.0bn** — y
este segundo número coincide EXACTO con Alpha Vantage (`longTermDebt` +
`capitalLeaseObligations` = `shortLongTermDebtTotal`, verificado dólar
a dólar). No es una diferencia de definición legítima entre proveedores
— es una inconsistencia **dentro del propio yfinance**, entre dos
campos que deberían decir lo mismo y no lo dicen. Mismo patrón en
`totalCash` ($123.0bn en `.info` frente a $86.8bn en `.balance_sheet`,
este último también exacto a Alpha Vantage).

**Por qué importa — y por qué no se detectó antes:** `historical_financials()`
de este mismo módulo YA usaba correctamente `ticker.balance_sheet` para
la serie histórica de deuda/caja que alimenta el fade de proyección —
pero `market_snapshot()` (el snapshot puntual que alimenta el WACC vía
`wacc_builder.py`) usaba el campo `.info` menos fiable, sin que nadie lo
hubiera contrastado nunca contra la otra ruta del mismo módulo. Con los
8 tickers piloto el efecto medido es pequeño (KO -2.7%, PG -0.03%, JNJ
+2.3% — verificado con datos reales), porque ninguno tiene una carga de
leasing tan grande como AMZN. Pero **en modo "cualquier ticker" (yfinance,
sin restricción de qué empresa se puede valorar), cualquier compañía con
mucho leasing —retail, aerolíneas, restauración— habría heredado un WACC
calculado sobre una cifra de deuda hasta un 65% más alta de lo real**, un
error de escala real, no un matiz.

**Cómo se corrigió:** `market_snapshot()` ahora lee `cash`/`total_debt`
de `ticker.balance_sheet` (la misma fuente que `historical_financials()`
ya usaba, ahora consistente dentro del propio módulo), con `.info` como
mejor esfuerzo únicamente si el balance sheet no está disponible.

**Verificado:** 2 tests de regresión nuevos en `tests/test_yfinance_provider.py`
(`.info` y `.balance_sheet` en desacuerdo deliberado — gana
`balance_sheet`; `.balance_sheet` vacío — cae a `.info` sin crashear).
Re-corrido `scripts/validate_universe.py` con datos reales: el efecto
en KO/PG/JNJ es el esperado, pequeño y en la dirección medida
(-20.4%→-22.3% KO, PG y JNJ casi sin cambio) — no una sorpresa, la
magnitud coincide exactamente con la del desajuste `.info` vs.
`.balance_sheet` encontrado por ticker.

---

### I9. `IndexError` sin capturar cuando una partida histórica no tiene ningún dato válido — ✅ CORREGIDO (sesión 17)

**Qué es:** a petición del usuario, se probó la herramienta "como si de
verdad un banco fuese a usarla" — 11 tickers reales, deliberadamente
diversos (hiper-crecimiento, cíclicos, apalancados, en pérdidas,
bancos, REITs, ADRs), fuera del universo piloto de 8 empresas ya
conocido, en modo "cualquier ticker" vía `AppTest` contra la app real
(sin mocks, red real). 3 de 11 (27%) crashearon con
`IndexError: list index out of range`: **XOM** (yfinance no reporta
D&A para esta empresa, en ningún año del histórico), **PLD** (REIT —
sin CapEx en el esquema esperado, se reporta de otra forma) y **JPM**
(banco — sin EBIT ni CapEx en el sentido tradicional, en ningún año).
`_margin_fade_from_recent_to_average()` hacía `ratios[-1]` sin
comprobar que `ratios` no estuviera vacío.

**Por qué importa:** el crash ocurre en la sección de cómputo
compartida por AMBOS modos ("cualquier ticker" y "universo cacheado"),
fuera de cualquier `try/except` existente (el de "cualquier ticker"
termina antes de este punto; I5 solo cubre la construcción del WACC en
"universo cacheado"). Un traceback real y sin capturar, alcanzable con
tickers de primera línea (Exxon, Prologis, JPMorgan), no con casos de
laboratorio.

**Cómo se corrigió:** `_margin_fade_from_recent_to_average()` lanza un
`ValueError` explícito, nombrando la partida sin datos, en vez de
`IndexError`; la primera llamada de cómputo en `app.py` (compartida
por ambos modos) ahora está protegida con `try/except` + `st.error` +
`st.stop()`, mismo patrón que I3/I5/I8.

**Verificado:** 1 test de regresión en `test_projections.py` (CapEx
`None` en todos los años → `ValueError` nombrando "CapEx"), 1 en
`test_app.py` (mismo caso vía `AppTest`, confirma cero excepciones y
mensaje accionable). Re-verificado con datos reales: XOM, PLD y JPM ya
no crashean, muestran un mensaje claro.

---

### I10. Valor terminal de Gordon Growth negativo/nulo sin ningún aviso — ✅ CORREGIDO (sesión 17)

**Qué es:** en la misma prueba de estrés, **TSLA** y **BA** mostraron
un "Gordon Growth" de **-$45.9 mil millones** y **-$184.1 mil
millones** respectivamente — valores terminales negativos, sin ningún
aviso al usuario. Investigado con el desglose completo del UFCF
proyectado: en TSLA, el ΔNWC proyectado (consumo de caja creciente,
$6.6bn→$9.7bn/año) supera a EBIT·(1-t)+D&A-CapEx todos los años del
horizonte, dando UFCF negativo de forma sostenida; en BA, el margen
EBIT revierte hacia la media de un histórico con dos años de pérdidas
reales (2022, 2024 — era de crisis 737 MAX/pandemia), arrastrando el
EBIT proyectado a territorio negativo hacia el año 5.

**Por qué importa:** matemáticamente consistente con la fórmula de
Gordon Growth (`TV = FCFF_n×(1+g)/(WACC-g)`, un FCFF_n negativo da un
TV negativo) — no es un bug de cálculo. Pero un valor terminal negativo
presentado sin contexto es indistinguible, para un analista que no
desglose el DCF a mano, de un resultado roto. Mismo espíritu que
`MIN_PRUDENT_WACC_GROWTH_SPREAD`: la fórmula no está mal, pero el
régimen merece una señal explícita.

**Cómo se corrigió:** `gordon_growth_terminal_value()` emite un
`warnings.warn()` cuando `final_year_fcf <= 0`, explicando el mecanismo
más probable (consumo de working capital que crece más rápido que el
EBIT, o margen revertido a una media con pérdidas reales) — no se
ajusta ningún número, mismo principio de "avisar, no maquillar" que el
resto del proyecto.

**Verificado:** 3 tests de regresión en `test_valuation.py` (FCF
negativo avisa y menciona "negativo"; FCF cero avisa y menciona "cero";
FCF positivo no avisa). Re-verificado con datos reales vía `AppTest`
contra la app real: el aviso aparece en el panel "Aviso técnico del
modelo" para TSLA y BA, sin excepciones.

### I12. "flat CAGR" se vuelve económicamente absurdo en compañías de hiper-crecimiento extremo — ✅ CORREGIDO parcialmente (sesión 17, Lote B): aviso añadido, sin umbral de corrección automática

**NVDA** mostró un "Gordon Growth" de **$21.7 billones** (trillion en
inglés) — más que el PIB mundial. Investigado: el CAGR reciente de
NVDA (2023→2026, motor de la sección 14 — crecimiento plano durante
todo el horizonte explícito, sin fade) es de verdad ~100%/año (demanda
real de chips de IA, no un error de datos), pero mantenerlo PLANO
durante 5 años seguidos compone los ingresos hasta $6.9 billones en el
año 5 — 30 veces cualquier ingreso empresarial real registrado. La
metodología "crecimiento plano, sin decaer dentro del horizonte
explícito" (sección 14, validada contra el Excel de referencia para
AMZN, ~10-11% de crecimiento) es correcta para el caso que la motivó,
pero se rompe en el extremo opuesto: ningún analista real modelaría 5
años seguidos de +90% de crecimiento sin ninguna desaceleración.

**Por qué no se corrige el número:** no hay un umbral objetivo,
verificable con datos, de "a partir de qué tasa de crecimiento el flat
CAGR deja de ser razonable" — inventar uno con precisión falsa sería
exactamente el tipo de ajuste sin verificar que este proyecto ha
evitado siempre.

**Lo que sí se añadió (Lote B, mismo día):**
`default_assumptions_from_history()` avisa cuando el CAGR reciente
plano supera el 50%/año — regla de pulgar deliberadamente conservadora
(mismo espíritu que `MIN_PRUDENT_WACC_GROWTH_SPREAD`, no una ley
exacta), mencionando el múltiplo real al que compone hacia el año N
(NVDA real: "multiplica los ingresos por 32.0x hacia el año 5"). No
cambia el precio ni la tasa asumida — solo hace explícito, en el propio
resultado, lo que antes solo se veía desglosando el DCF a mano.
Verificado con datos reales: NVDA dispara el aviso, AMZN/MSFT/TSLA/BA
(crecimiento normal, 5-21%) no.

---

### I11. DCF de flujo de caja libre no encaja con bancos ni REITs — ✅ DOCUMENTADO como limitación estructural (sesión 17)

**Qué es:** la misma prueba de estrés confirmó (I9) que **JPM**
(banco) y **PLD** (REIT) no tienen EBIT/CapEx en el esquema que asume
un DCF FCFF genérico — no es solo un hueco de datos de yfinance, es
que estos sectores estructuralmente no encajan con la metodología: los
bancos no separan "coste de financiación" de "actividad operativa" de
la misma forma (los intereses SON el negocio, no un coste de
financiación externo al negocio), y los REITs se valoran en la
práctica real con métricas propias (FFO/AFFO, no UFCF) precisamente
porque su CapEx y depreciación no se comportan como los de una empresa
operativa normal.

**Por qué se documenta así, no se "corrige":** un banco de primer
nivel real NUNCA aplicaría un DCF FCFF genérico a un banco o un REIT
sin adaptaciones metodológicas específicas (Dividend Discount Model /
Excess Return Model para bancos; FFO-multiple para REITs) — no es una
limitación de esta herramienta en particular, es una limitación de la
metodología DCF FCFF en sí misma aplicada fuera de su dominio. Mismo
criterio que I2/I4: limitación estructural aceptada, señalada con
claridad (ahora con un mensaje de error específico, ver I9) en vez de
dejar que la herramienta finja que puede valorar cualquier sector por
igual.

**Mejora añadida (mismo día, Lote A de la sesión 17):** además del
mensaje de error reactivo de I9, la app ahora muestra un `st.warning`
proactivo en cuanto detecta `sector` conteniendo "financial", "real
estate", "bank" o "insurance" (case-insensitive) — ANTES de que el
cómputo llegue a fallar, no solo después. Verificado con datos reales:
JPM ("Financial Services") y PLD ("Real Estate") muestran el aviso;
AMZN no. Aviso, no bloqueo — alguna empresa concreta del sector puede
tener datos suficientes para no fallar.

---

### I13. Precio de mercado de Alpha Vantage mal para GOOGL/META — afecta a hallazgos ya documentados en varias sesiones — ✅ CORREGIDO (sesión 17)

**Qué es:** construyendo el backtest walk-forward (Lote C, ítem D), al
comparar el precio de mercado de hoy contra el precio histórico de
GOOGL salió una desviación absurda (+188% de "infravaloración" de
nuestro propio modelo, un valor atípico frente al resto de Big Tech).
Investigado a fondo (no descartado como "ruido"): `engine.data_provider
.market_snapshot()` deriva `"price"` como `MarketCapitalization /
SharesOutstanding` del `OVERVIEW` de Alpha Vantage — y ese cociente
sale **2.08x inflado para GOOGL** ($705.51 en vez de $338.46 reales,
confirmado con yfinance Y con el propio `52WeekHigh` de Alpha Vantage,
$408.10, y `AnalystTargetPrice`, $428.07 — ambos en el rango correcto).
Causa: `SharesOutstanding` de Alpha Vantage (5.867bn) solo cuenta una
de las dos clases de acciones de Alphabet (GOOGL/GOOG), mientras
`MarketCapitalization` ($4.139T) sí refleja la compañía completa
(`SharesFloat`, 10.88bn, casi el doble de `SharesOutstanding`, lo
confirma). **META tiene el mismo problema, más leve y de otra
naturaleza** (1.155x inflado, $712.53 en vez de $616.77 reales) — ahí
`SharesOutstanding`/`SharesFloat` son consistentes entre sí (no es un
problema de clases de acciones), así que `MarketCapitalization` parece
desincronizada en el tiempo respecto al resto del snapshot.

**Por qué importa — más allá del backtest:** este precio de mercado
alimenta `deviation_vs_market` en TODAS las sesiones de validación
anteriores. Con el precio corregido, la desviación de GOOGL frente al
mercado pasa de **-52.3% a -0.56%** (esencialmente en su valor justo,
no infravalorada como el resto de Big Tech) y la de META de **-26.2% a
-14.8%**. La desviación media combinada del universo piloto (8
tickers) pasa de **41.82% a 34.21%** — una cifra citada repetidamente
en `docs/METHODOLOGY.md` (secciones 7, 14, 16, 23, 24) y en el
borrador de uso para CV (sección 7) queda desactualizada por este bug,
no solo por el ajuste normal de precios de mercado día a día.

**Por qué no se detectó antes:** GOOGL y META llevan en el universo
piloto desde las primeras sesiones, pero nunca se había contrastado el
precio de mercado derivado contra una fuente independiente — se
confiaba en que `MarketCapitalization`/`SharesOutstanding` de un
proveedor de pago fueran mutuamente consistentes, sin verificarlo.

**Cómo se corrigió, en dos capas:**
1. `engine.data_provider._validate_derived_price()` (nueva): descarta
   el precio derivado a `None` cuando cae fuera de 1.5x/0.5x el rango
   de 52 semanas de la propia Alpha Vantage — cobertura PARCIAL,
   documentada como tal (detecta GOOGL, no detecta META, cuyo precio
   erróneo cae igualmente dentro del rango de 52 semanas).
2. `app/streamlit_app.py`, `scripts/validate_universe.py` y
   `scripts/run_backtest.py`: yfinance (`engine.yfinance_provider
   .live_price()`, nueva) pasa a ser la fuente PREFERIDA de cotización
   para TODOS los tickers, con el precio derivado de Alpha Vantage solo
   como último recurso si yfinance no responde — no una capa 1 sin la
   capa 2, porque la capa 1 por sí sola no habría arreglado META.
   yfinance ya era una dependencia transversal de la app (el risk-free
   rate en vivo se usa sin importar el modo) — extenderla a la
   cotización no añade una categoría nueva de fragilidad.

**Verificado:** 4 tests nuevos en `test_data_provider.py` (descarta el
precio fuera de rango con aviso; lo mantiene dentro de rango; lo deja
pasar sin rango de 52 semanas disponible) + 4 en `test_yfinance_provider.py`
(`live_price()` con sus 3 fuentes en cascada y el caso sin ninguna
disponible). Re-verificado de punta a punta con `AppTest` + red real:
GOOGL en modo "Big Tech" (Alpha Vantage) ahora muestra
`Precio de mercado: $338.46`, el valor correcto. `scripts/validate_universe.py`
re-corrido con datos reales: universo completo recalculado con el
precio correcto (ver `data/validation_history/2026-09-06.json`).

---

### I14. Reverse DCF solo resuelve crecimiento de ingresos o g terminal, no margen — ✅ DOCUMENTADO como limitación de alcance (sesión 17)

**Qué es:** evaluando un grupo real de semiconductores (NVDA, AMD,
AVGO, QCOM, INTC, vía yfinance) como prueba de extremo a extremo,
`engine.reverse_dcf.compute_implied_expectations()` devolvió "fuera de
rango" para AMD — ni con un crecimiento de ingresos del 60% (el límite
superior de búsqueda) se justifica el precio de mercado ($477.57 frente
a un precio conservador de $55.23). El propio dato de AMD explica por
qué: ROIC=7.1% frente a WACC=12.3% (no crea valor con la rentabilidad
ACTUAL), y un PE ratio de 121.8x en la tabla de comparables — señales
de que el mercado no está pagando por más ingresos, sino por una
recuperación de MARGEN (los cargos de amortización de la adquisición de
Xilinx, 2022, deprimen la rentabilidad reportada actual de forma no
recurrente). El reverse DCF de este proyecto solo tiene una palanca de
resolución -- crecimiento de ingresos (`engine.projections
.implied_revenue_growth`) o tasa de crecimiento terminal
(`engine.valuation.implied_terminal_growth_rate`) -- nunca margen, así
que no puede expresar "el mercado espera que el margen EBIT vuelva al
X%" como hipótesis alternativa, aunque sea la lectura más plausible
para una empresa como AMD en este momento concreto.

**Por qué se documenta así, no se "corrige":** añadir un tercer solver
(`implied_ebit_margin`, con la misma bisección que ya usan los otros
dos) es mecánicamente sencillo — el diseño y la infraestructura de
`solve_for_target_price()` ya son genéricos, no específicos de
crecimiento. No se construye en esta sesión porque no se ha investigado
todavía si margen y crecimiento son solubles de forma independiente sin
ambigüedad (a diferencia de crecimiento/g, que son parámetros
claramente separados en el pipeline, un cambio de margen afecta
simultáneamente a EBIT y, indirectamente, a la lectura de "qué domina"
ya dada por `engine.sensitivity` — mezclar ambas herramientas sin
pensarlo bien podría confundir más de lo que aclara). Queda como
candidato concreto para una futura sesión, no como pendiente indefinido
sin dueño.

---

### I15. `tax_rate` proyectado se calcula como media histórica SIN detección de outliers — a diferencia de margen/CapEx/D&A/ΔNWC — ✅ CORREGIDO parcialmente (sesión 17, continuación): aviso añadido, sin ajuste automático del número

**Qué es:** evaluando un grupo real de biotech/farma (REGN, VRTX,
MRNA, BIIB, vía yfinance), **VRTX** — una compañía que crea valor de
forma clara (ROIC=25.4% vs WACC=6.7%) y con un crecimiento de ingresos
razonable asumido (10.4%/año) — dio un precio implícito conservador
**NEGATIVO de -$133.78** frente a un precio de mercado de $546.12.
Investigado con el desglose de la proyección: el `tax_rate` que
alimenta los 5 años del horizonte explícito sale **115.9%** — un tipo
impositivo matemáticamente imposible (más del 100% del beneficio antes
de impuestos). La causa raíz: `default_assumptions_from_history()`
(línea 375, `engine/projections.py`) calcula `tax_rate` como
`margin_window["tax_rate"].dropna().mean()` — una media aritmética
simple del histórico, **sin ningún guard de outliers**, a diferencia
de margen EBIT/CapEx/D&A/ΔNWC, que sí pasan por
`_margin_fade_from_recent_to_average()` con detección Iglewicz &
Hoaglin (z modificado, umbral 3.5). El histórico real de VRTX:
tax_rate = 21.5% (2022), 17.4% (2023), **315.5% (2024)**, 14.9% (2025)
— el año 2024 es un outlier severo causado por un EBIT que colapsó a
$279M (cargo de I+D en proceso por la adquisición de Alpine Immune
Sciences, ~$4.9bn, un evento real y no recurrente, no un error de
datos), mientras el tax_provision no colapsó proporcionalmente. Con
`lookback_years=3`, ese único año arrastra la media a 115.9%.

**Por qué es un hallazgo distinto de I10:** I10 (ya corregido) avisa
cuando el FCF proyectado sale negativo, sea cual sea la causa — y ese
aviso SÍ dispara aquí. Pero la causa concreta en este caso no es
consumo de NWC ni reversión de margen a una media con pérdidas reales
(los dos mecanismos ya documentados en I10): es un tercer mecanismo no
cubierto hasta ahora — **`tax_rate` es un ratio inherentemente
inestable cuando su denominador (`pretax_income`) se acerca a cero**,
a diferencia de los ratios de margen/CapEx/D&A/ΔNWC, que dividen entre
ingresos (un denominador que nunca es cercano a cero para una empresa
operativa real). Un solo año con `pretax_income` casi nulo puede
producir un `tax_rate` de cientos o miles por ciento sin que el propio
dato esté "mal" — es aritméticamente correcto, solo inutilizable como
insumo de una media plana a 5 años.

**Investigado con más casos reales antes de decidir el fix, no solo
VRTX.** Se escaneó `tax_rate` histórico de los 24 tickers reales ya
usados en la auditoría de esta sesión (5 sectores distintos): **5 de
24 (20.8%)** tienen al menos un año individual fuera de un rango sano
(AMD, INTC, NEE, VRTX, MRNA) — no es un caso aislado, es un patrón que
aparece en 1 de cada 5 compañías reales, sobre todo en sectores con
beneficio antes de impuestos cerca de cero (turnarounds, reestructuración,
biotechs con pérdidas).

**Primera hipótesis probada y RECHAZADA con datos reales:** excluir de
la media el año detectado como outlier (mismo detector Iglewicz &
Hoaglin, umbral 3.5, ya usado en `_detect_anchor_outlier`), aplicado a
CUALQUIER año de la ventana (no solo el último, ya que el outlier de
`tax_rate` no tiene por qué ser el año más reciente). Funciona bien
para VRTX (115.9%→16.1%, coincide casi exacto con la banda real del
propio Excel de referencia para AMZN, 15-18%, ver M6) y mejora MRNA
levemente. Pero probado contra los 24 tickers completos, **dispara
falsos positivos en 9 de 24** — casos de variación normal amplificada
por el tamaño de muestra pequeño (n=3), no ítems no recurrentes reales.
El caso más claro: **QCOM**, cuyo único año alto (56.2%) se excluye,
dejando una media de **1.8%** — un número igual de irreal que el
115.9% original, solo que menos obvio. Este es literalmente el mismo
error, ya investigado y rechazado una vez, que motivó que
`_margin_fade_from_recent_to_average()` NUNCA sustituya
automáticamente el ancla de un fade (ver su docstring, sesión 17,
Lote B) — con muestras de 2-3 años, un z-score no puede distinguir
fiablemente "ítem no recurrente" de "variación normal amplificada por
poca muestra". Repetir ese error para `tax_rate` habría sido ignorar
una lección ya aprendida y documentada en el propio proyecto.

**Corrección aplicada:** en vez de excluir/sustituir, se avisa cuando
el `tax_rate` proyectado (la media ya calculada, sin tocar) cae fuera
de una banda plausible de referencia (`TAX_RATE_PLAUSIBLE_RANGE =
(-10%, 60%)`, regla de pulgar documentada en el código, mismo espíritu
que `EXTREME_FLAT_GROWTH_WARNING_THRESHOLD` — no un tipo estatutario,
M6 ya investigó y rechazó anclar a eso). Verificado contra los mismos
24 tickers: la banda captura exactamente los 3 casos donde la MEDIA
FINAL (no un año individual) resulta implausible — AMD (-17.9%), INTC
(-31.0%), VRTX (115.9%) — con **cero falsos positivos** en los 21
restantes (NEE y MRNA, pese a tener años individuales extremos, ya
tienen una media final dentro de la banda y no disparan). El número
NO se toca en ningún caso — mismo principio "avisar, no maquillar" que
I10/I12/M2: el mensaje explica el mecanismo (año con beneficio antes
de impuestos cerca de cero dentro de la ventana) y sugiere revisión
manual o ampliar `lookback_years`, sin fabricar una sustitución sin
evidencia suficiente para justificarla.

**Verificado:** 2 tests de regresión nuevos en `test_projections.py`
(caso VRTX con datos reales dispara el aviso sin alterar el número;
25% plano no dispara nada). Re-verificado con el pipeline completo
real (yfinance, sin llamadas a Alpha Vantage): VRTX sigue dando
-$133.78 (sin cambios, correcto — el precio no se maquilla), pero
ahora el aviso "tax_rate proyectado (115.9%) está muy fuera de un
rango plausible..." aparece junto al resto de diagnósticos técnicos,
dando al usuario la causa raíz real en vez de solo el síntoma (FCF
negativo, ya cubierto por I10). 230 tests en total, todos en verde.

---

### I16. `default_assumptions_from_history()` revertía SIEMPRE hacia la media histórica, aunque la empresa mostrara una tendencia estructural real y sostenida — ✅ CORREGIDO parcialmente (sesión 17): detección objetiva (R²), sin extrapolar más allá de mantener el nivel actual

**Qué es:** el usuario pidió parar de dispersarse (DAFO/lotes/dogfooding/
SEC EDGAR) y volver al núcleo: la matemática del DCF ya está validada
al céntimo, pero la selección de supuestos no se adapta a la situación
real de cada empresa — trata a cualquier compañía con la misma receta
mecánica ("año 1 = dato real, año 5 = media de los últimos N años").
Un repaso de punta a punta con **AMZN** (la misma empresa del Excel de
referencia) lo expuso con un caso concreto: el margen EBIT real lleva
**4 ejercicios seguidos mejorando** (2.4%→6.4%→10.8%→11.2%) y el CapEx
real lleva **subiendo por el supercycle de IA** (12.4%→9.2%→13.0%→
18.4%, mismo patrón ya confirmado esta sesión para MSFT/META/GOOGL/
NVDA). El motor, en el escenario que se usaba como cifra de cabecera
de cada memo, proyectaba **ambos bajando** — la dirección contraria a
4 años de evidencia real. Revertir CONTRA una tendencia real y sostenida
no es "conservador", es la asunción equivocada.

**Investigado con evidencia objetiva antes de decidir el criterio, no
solo con AMZN.** Se probó R² de un ajuste lineal por mínimos cuadrados
sobre la ventana histórica de margen/CapEx/D&A/ΔNWC, verificado contra
los 25 tickers reales ya usados en la auditoría de esta sesión (Big
Tech, semiconductores, utilities, biotech, small-caps) — separa limpio
tendencia real de ruido:

| Casos con R² alto (>0.85, tendencia real) | Casos con R² bajo (<0.30, ruido) |
|---|---|
| AMZN margen 0.92, MSFT margen 0.93, MSFT CapEx 0.94, KO margen 0.98, PG CapEx 0.96, DUK margen 0.94, D margen 0.95, MRNA margen 0.94 | JNJ margen 0.01, PG margen 0.10, BOOT margen 0.05, VRTX margen 0.16 (el mismo año outlier de I15), QCOM/INTC/NEE margen 0.24-0.30 |

La monotonicidad simple resultó ser un criterio peor: **GOOGL** margen
(26.5%/27.4%/32.1%/32.0%) tiene R²=0.86 (tendencia real clara) pero
falla monotonicidad estricta por un último paso casi plano — R² lo
captura correctamente, monotonicidad lo habría rechazado mal.

**Hipótesis de diseño alternativa considerada y descartada:** extrapolar
la tendencia detectada más allá del nivel actual (en vez de solo
mantenerlo). Se rechazó por el mismo motivo que ya cerró I12 (NVDA,
flat CAGR compuesto 5 años → $21.7 billones): extrapolar una tendencia
fuerte varios años seguidos puede producir valores implausibles sin que
haya una forma objetiva de saber CUÁNTO extrapolar. La corrección
aplicada es deliberadamente conservadora: cuando hay evidencia de
tendencia, el motor deja de apostar CONTRA ella (revertir a la media),
pero tampoco apuesta a que continúe — se mantiene en el nivel actual.

**Cómo se corrigió:** `_detect_structural_trend()` (nueva,
`engine/projections.py`) calcula R² de un ajuste lineal por mínimos
cuadrados, Python puro sin numpy. `TREND_R_SQUARED_THRESHOLD = 0.70`
(regla de pulgar documentada, mismo espíritu que
`EXTREME_FLAT_GROWTH_WARNING_THRESHOLD`/`TAX_RATE_PLAUSIBLE_RANGE`) —
caso conocido y deliberadamente NO resuelto: el CapEx real de AMZN
(R²=0.545) cae bajo el umbral y sigue revirtiendo a la media; se
prefiere dejar pasar un caso real antes que repetir el error ya
investigado y rechazado en I15 (excluir outliers de `tax_rate` disparaba
falsos positivos en 9/24 tickers reales). Cuando el test dispara,
`end` pasa a ser el nivel actual en vez de la media histórica, con un
`warnings.warn()` explícito — nunca un ajuste silencioso.

**Detalle no obvio, encontrado en la implementación:** con
`lookback_years=3` (el valor por defecto real en TODOS los call sites
del pipeline — `run_scenarios`, `driver_sensitivities`,
`run_monte_carlo`, el slider de la app), la ventana de margen tiene
exactamente 3 puntos — insuficiente para un R² fiable (1 grado de
libertad residual, degenerado). La ventana del TEST de tendencia se
desacopló de `lookback_years` (`max(lookback_years,
MIN_TREND_DATA_POINTS=4)`, sin tocar la ventana de la media histórica)
para que la corrección funcione con los valores por defecto reales de
la app, no solo si el usuario descubre y sube un slider no relacionado.

**Interacción con `engine/scenarios.py`, encontrada por un agente de
planificación antes de escribir código, no después:** `bullish_scenario()`
extrapolaba "la misma magnitud que el margen ya se movió frente a
`fade.end`" — pero `fade.end` puede estar ahora sobrescrito al nivel
actual cuando el override dispara, lo que habría colapsado
silenciosamente el escenario "Alcista" en un duplicado exacto de
"Mantener nivel actual" justo para las empresas que motivan este
cambio (AMZN, MSFT...). Corregido separando la media histórica real en
un campo nuevo (`DriverTrendInfo.historical_mean`) que `bullish_scenario()`
usa explícitamente en vez de `fade.end`. Verificado con test de
regresión dedicado.

**Renombrado, no solo corregido:** el escenario `"Conservador
(reversión a la media)"` ya no describe con precisión lo que hace
cuando el override dispara — renombrado a `"Base (histórico)"`
(`BASE_SCENARIO_NAME`, `engine/scenarios.py`), con una descripción
generada dinámicamente driver por driver (qué revierte, qué se
mantiene, y por qué) en vez de un texto fijo que puede quedar
desactualizado. `ai/memo_generator.py` tenía el nombre anterior
duplicado a mano como literal (`CONSERVATIVE_SCENARIO_NAME`) en vez de
importarlo — se habría roto en silencio (`base_result = None`, sin
excepción, memo sin desviación vs. mercado) en cuanto se renombrara el
escenario sin corregir también esto.

**Efecto real medido (sin gastar cuota nueva de Alpha Vantage, WACC vía
comparables real), comparando precio con la corrección vs. reversión
pura forzada a mano sobre los mismos datos:**

| Ticker | Antes (reversión pura) | Después (I16) | Cambio | Qué domina |
|---|---|---|---|---|
| AMZN | $47.51 | $68.20 | **+43.5%** | margen (único driver con tendencia real) |
| MSFT | $269.05 | $195.23 | **-27.4%** | CapEx elevado (R²=0.94) domina sobre la mejora de margen |
| GOOGL | $212.17 | $134.95 | **-36.4%** | CapEx elevado (R²=0.81) domina sobre la mejora de margen |
| META | $333.11 | $384.34 | **+15.4%** | margen + D&A, sin CapEx disparando |

El efecto no es uniformemente alcista ni bajista — depende de qué
driver concreto tiene tendencia real en cada empresa, y de si ese
driver es un ingreso (margen: sube el precio si se mantiene alto) o un
gasto (CapEx: baja el precio si se mantiene alto). Esto confirma que la
corrección responde a evidencia por empresa, no a un sesgo direccional
inventado — mismo tipo de verificación de anti-sobreajuste que ya
confirmó C2 (la desviación agregada del universo piloto empeoró tras
ese fix, no mejoró).

**Escaneado el conjunto completo de 25 tickers**: 22 de 25 disparan el
override en al menos un driver (AMZN, MSFT, GOOGL, META, AAPL, KO, PG,
JNJ, NVDA, AMD, AVGO, QCOM, DUK, SO, D, REGN, MRNA, BIIB, MCRI, SHOO,
BOOT, FIZZ); INTC, NEE y VRTX no disparan en ningún driver —
consistente con ser, precisamente, los tres casos ya documentados como
genuinamente volátiles/con outliers reales (I15, restructuración de
INTC, créditos fiscales de NEE).

**Alcance, documentado sin ocultar el hueco:** el test se aplica
genéricamente a los 4 drivers con fade (margen EBIT, D&A%, CapEx%,
ΔNWC%) — limitarlo a solo margen/CapEx habría sido una asimetría
arbitraria no documentada. D&A/ΔNWC reciben el mismo tratamiento "por
extensión del principio general", sin verificación caso a caso tan
exhaustiva como margen/CapEx (mismo estado que M6/M7 antes de
cerrarse). `revenue_growth`/`tax_rate` NO se tocan — crecimiento ya es
plano por diseño (sección 14), tax_rate ya es plano por decisión
investigada (M6).

**Verificado:** 6 tests de regresión nuevos en `test_projections.py`
(AMZN real margen dispara/CapEx no, GOOGL vs. monotonicidad, gate de
`MIN_TREND_DATA_POINTS` con historia total insuficiente, outlier+
tendencia co-disparando sin contradicción, reescritura del test que
antes afirmaba justo lo contrario) + 4 en `test_scenarios.py`
(incluida la regresión directa del colapso de `bullish_scenario()`) +
ajustes en `test_memo_generator.py`/`app/streamlit_app.py` para el
renombrado. **262 tests en total, todos en verde.**

---

### N4. Capa de supuestos del analista: un 4º escenario, construido a mano por el usuario, al lado de los 3 objetivos — ✅ AÑADIDO (sesión 18)

**Qué es:** tras cerrar I16 y evaluar el grupo Big Tech, el usuario
preguntó si la herramienta ya funciona "a nivel profesional". La
respuesta calibrada fue: sí como motor de cálculo y disciplina de
supuestos, pero no sustituye el juicio cualitativo de un analista senior
— el caso del Excel de AMZN es el ejemplo concreto (margen EBIT modelado
expandiéndose 6 años seguidos por criterio del banquero, 9.8%→15.0%,
frente a nuestro motor que solo mantiene el nivel actual sin extrapolar,
ver sección 36 de `docs/METHODOLOGY.md`). Se le ofreció aumentar la
fiabilidad con contexto externo estructurado (consenso de analistas vía
Alpha Vantage `EARNINGS_ESTIMATES`, o extracción de guidance de earnings
calls) — lo rechazó explícitamente: quiere una casilla donde el propio
analista escriba sus supuestos, para combinarlos con los datos auditables
que ya usa la herramienta.

**Principio de diseño que gobierna todo el cambio** (mismo que ha regido
el proyecto entero): el override del analista nunca sustituye ni se
mezcla en silencio con los 3 escenarios objetivos (`Base`, `Mantener
nivel actual`, `Alcista`) — se añade como una CUARTA lectura, separada,
etiquetada explícitamente como juicio humano, tanto en el gráfico (color
distinto), como en la pestaña de supuestos (tabla objetivo vs. analista)
y en el memo generado (regla nueva del prompt de sistema que cita la
justificación tal cual).

**Cómo funciona:** `engine.scenarios.analyst_scenario(base, overrides,
rationale)` anula solo el AÑO N (`end`) de los drivers que el analista
elige — el AÑO 1 (`start`, dato real del último ejercicio) nunca se
puede tocar, mismo principio que todo el resto del motor. Requiere
justificación obligatoria (`ValueError` si se omite) y avisa (sin
bloquear) si el valor cae muy fuera de un rango plausible
(`ANALYST_OVERRIDE_PLAUSIBLE_RANGE = (-0.50, 1.00)`, mismo espíritu que
`TAX_RATE_PLAUSIBLE_RANGE`). `run_scenarios()` gana
`analyst_overrides`/`analyst_rationale` opcionales, retrocompatibles
(sin ellos, comportamiento idéntico al de antes). En
`app/streamlit_app.py`, los inputs (checkbox + valor por driver, más la
justificación) viven en un expander del sidebar, namespaceados por
ticker para que cambiar de empresa no arrastre en silencio el override
de otra.

**3 hallazgos reales encontrados por un agente de planificación antes de
escribir código** (mismo patrón que I16): (1) sin un nombre de escenario
FIJO, un nombre generado dinámicamente podía colisionar con uno de los 3
objetivos y sobrescribir su `DCFResult` en silencio en el `dict`
indexado por nombre — corregido con la constante exportada
`ANALYST_SCENARIO_NAME`; (2) `build_memo_input()` no puede reconstruir
la justificación/drivers-anulados a partir de `scenario_results`
(`dict[str, DCFResult]`, sin el objeto `Scenario` completo) — corregido
recibiéndolos como parámetros explícitos, las mismas variables ya
pasadas a `run_scenarios()`; (3) `revenue_growth` nunca revierte a una
media histórica (queda plano al CAGR reciente por diseño, verificado
contra el Excel) — reutilizar mecánicamente el patrón de descripción de
los otros 4 drivers habría generado la frase falsa "revierte hacia su
media histórica" — corregido con una rama de descripción propia para
ese driver.

**Verificado con datos reales de AMZN** (WACC 9.11% vía comparables,
sin gastar cuota de Alpha Vantage): sin override, los 3 escenarios
objetivos dan $126.73/$81.56/$105.04. Anulando el margen EBIT a 16%
(informado por el propio guidance del Excel de referencia, que proyecta
15% en el año 5) el escenario del analista da **$193.03** — sustancialmente
más cerca del precio implícito del Excel ($216.41) y del precio de
mercado actual ($258.51) que cualquiera de los 3 escenarios objetivos,
justo la brecha que motivó este cambio. También verificado: el aviso de
rango plausible dispara con un valor de fat-finger (500% en vez de 5%);
el `ValueError` por justificación vacía se lanza correctamente; sin
overrides, `run_scenarios()` sigue devolviendo exactamente los 3
escenarios de siempre (retrocompatibilidad confirmada).

**Alcance explícitamente fuera de este cambio:** `driver_sensitivities`,
`run_monte_carlo`, `compute_implied_expectations` (reverse DCF) no se
tocan — operan sobre el `ProjectionAssumptions` base objetivo, no sobre
el dict de escenarios. Solo se puede anular el AÑO N, nunca el AÑO 1.
Solo un escenario de analista a la vez.

**Verificado:** 14 tests nuevos (`tests/test_scenarios.py`,
`tests/test_memo_generator.py`) + verificación manual con la app real
(Streamlit lanzada, sin tracebacks) y con datos reales de AMZN vía
script de dogfooding. **276 tests en total, todos en verde.**

---

### N5. Alpha Vantage retirado como fuente automática, y luego eliminado del todo — ✅ CAMBIADO (sesión 18, ampliado sesión 19)

**Qué es:** el usuario, al probar la app tras el commit de N4, notó que
seleccionar el grupo por defecto ("Big Tech / Cloud") consumía cuota de
Alpha Vantage automáticamente sin haber elegido nada explícito. Causa:
`CACHED_GROUPS` (`app/streamlit_app.py`) tenía como primer grupo del
diccionario "Big Tech / Cloud (Alpha Vantage)" — `st.selectbox` toma
siempre el primer elemento como valor por defecto, así que cualquier
visitante de la app pública que no tocara nada ya gastaba parte de la
cuota compartida de 25 peticiones/día, solo por el orden del dict.

**Decisión del usuario, explícita:** "abandonemos alpha de momento" —
usar los medios gratuitos ya desarrollados (yfinance, sin límite de
cuota) en vez de Alpha Vantage de forma automática.

**Corregido:** el grupo se renombró a "Big Tech / Cloud (yfinance)"
(mismos 5 tickers, AMZN/MSFT/GOOGL/META/AAPL, ya verificados
exhaustivamente vía yfinance en el dogfooding de esta sesión) —
`loader = load_av_universe if "Alpha Vantage" in group_name else
load_yf_universe` ya seleccionaba el proveedor por el nombre del grupo,
así que ningún grupo de `CACHED_GROUPS` usa ya Alpha Vantage. **No se
borra la capacidad**: `load_av_universe()`, `AlphaVantageClient` y el
manejo de `AlphaVantageError` siguen intactos en el código — solo
dejan de invocarse automáticamente. Reactivarlos en el futuro es tan
simple como añadir de nuevo un grupo con "Alpha Vantage" en el nombre.

**Trade-off conocido y aceptado, no oculto:** Alpha Vantage da el
desglose de recomendaciones de analistas por tramo (Strong Buy/Buy/
Hold/Sell/Strong Sell); yfinance solo da una recomendación consenso +
media 1-5 + nº de analistas. La app ya tenía manejo gracioso para esto
desde la sesión 17 (`app/streamlit_app.py`, tooltip de "Consenso
analistas" con rama alternativa si faltan los campos de tramo) — el
grupo por defecto pasa a usar esa rama alternativa en vez de la
detallada, sin ningún crash ni dato faltante sin explicar.

**Impacto en tests:** `tests/test_app.py` tenía 4 tests que parcheaban
`engine.data_provider.*` (Alpha Vantage) asumiendo que era la ruta real
del camino feliz por defecto — 3 se corrigieron parcheando
`engine.yfinance_provider.*` en su lugar (el guard de
`interest_expense` vacío y el manejo de excepción genérica no dependen
del proveedor, solo cambia qué módulo hay que parchear).
`test_alpha_vantage_error_shows_actionable_message_not_a_traceback`
(regresión de sesión 16) se retiró explícitamente: el camino que
prueba (fallo de cuota de Alpha Vantage en el modo por defecto) ya no
es alcanzable desde la UI sin editar el archivo fuente durante el test
(`CACHED_GROUPS` se define dentro del propio script y `AppTest` lo
re-ejecuta desde el archivo en cada `.run()`, así que no se puede
parchear desde fuera). Hueco explícito y documentado: si se reactiva
un grupo con Alpha Vantage, esa cobertura debe recuperarse.

**Verificado (sesión 18):** suite completa, 275 tests (276 − 1 test
retirado, todos en verde). App Streamlit relanzada limpia y verificada
sin tracebacks tras el cambio.

**Ampliación sesión 19 — eliminación completa, no solo dormancia:** el
usuario, tras usar la app un tiempo, decidió que la cuota gratuita de
Alpha Vantage (25 peticiones/día, compartida entre todos los
visitantes de la app pública) es una limitación práctica real, no
teórica, y pidió eliminarlo del proyecto por completo en vez de
dejarlo dormido. Se elimina `engine/data_provider.py` (el módulo
entero: `AlphaVantageClient`, `AlphaVantageError`, `historical_financials`,
`market_snapshot`), su test dedicado (`tests/test_data_provider.py`),
la caché en disco (`data/cache/alpha_vantage/`), y la clave
`ALPHA_VANTAGE_API_KEY` de `.env`. `app/streamlit_app.py` pierde toda
referencia a Alpha Vantage (import, `load_av_universe()`, el
`except AlphaVantageError`, y la rama de tramos de rating por analista
que dependía de campos exclusivos de Alpha Vantage). Los 3 scripts de
`scripts/` que tenían una rama condicional por proveedor
(`validate_universe.py`, `run_backtest.py`, `cross_validate_edgar.py`)
se reescriben para usar solo yfinance. A diferencia del cambio de
sesión 18, esta vez la capacidad SÍ se borra, no se deja dormida —
decisión explícita del usuario, no un descuido: reactivar Alpha
Vantage en el futuro requeriría reescribir el módulo, no solo añadir
un grupo con ese nombre.

**Verificado (sesión 19):** suite completa, 253 tests (275 − 22 tests
que solo cubrían código ahora eliminado, todos en verde). App
Streamlit relanzada limpia sin ninguna referencia a Alpha Vantage.

---

## Moderado

### M1. `requirements.txt` sin versiones fijadas — ✅ CORREGIDO (sesión 15)

Ninguna dependencia tiene versión pinneada (`pandas` en vez de
`pandas==3.0.5`). Verificado: las versiones realmente instaladas en el
venv (`pip freeze`) son bastante recientes (pandas 3.0.5, numpy 2.5.2,
streamlit 1.63.0) — una instalación fresca dentro de unos meses podría
traer versiones con cambios incompatibles (p. ej., un pandas 4.x)
sin ningún aviso. Para un proyecto que se presenta como riguroso, esto
es una brecha de reproducibilidad real, fácil de cerrar (`pip freeze > requirements.txt`
sobre el venv actual, curado a mano).

**Cómo se corrigió:** las 10 dependencias directas de `requirements.txt`
quedaron fijadas a la versión exacta ya instalada y verificada en el
venv del proyecto (`pandas==3.0.5`, `numpy==2.5.2`, `yfinance==1.7.0`,
`anthropic==1.4.0`, `streamlit==1.63.0`, `pytest==9.1.1`,
`openpyxl==3.1.5`, `requests==2.34.2`, `python-dotenv==1.2.3`,
`matplotlib==3.11.1`). Deliberadamente solo las directas, no un
`pip freeze` completo con transitivas — más legible y suficiente, ya
que pip resuelve el resto a partir de estas.

**Verificado:** `pip install -r requirements.txt --dry-run` sobre el
venv existente resuelve sin ningún conflicto ("Requirement already
satisfied" en las 10 líneas y en todas sus transitivas). Suite completa
re-ejecutada tras el cambio: 118 tests, todos en verde — el pin no
cambia ningún comportamiento, solo fija lo que ya estaba en uso.

### M2. `gordon_weight` por defecto (0.8) es una constante heredada, no justificada para el caso general — ✅ DECISIÓN EXPLÍCITA E INVESTIGADA (sesión 16): se mantiene 0.8, con motivo

El Excel usa 80% Gordon / 20% múltiplo específicamente para el segmento
North America (maduro, bajo crecimiento) — un peso pensado para ESE
segmento, no una regla general de la industria. Nuestro motor lo usa
como valor por defecto para cualquier compañía, en cualquier sector,
sin ninguna justificación propia más allá de "es lo que traía el
Excel". No es necesariamente incorrecto, pero sí es una constante sin
razonar explícitamente, cuando el proyecto se ha esforzado en razonar
cada supuesto (ver secciones 5-16 de `docs/METHODOLOGY.md`).

**Investigado con datos reales antes de decidir, no se cambió el número
a ciegas.** La hipótesis inicial era cambiar el default a `1.0` (Gordon
Growth puro), que es de hecho el default ya documentado a nivel de
motor (`engine/valuation.py::DCFInputs.gordon_weight = 1.0`,
`engine/scenarios.py::run_scenarios(gordon_weight=1.0)`) — la capa de
orquestación (`value_ticker()`, el slider de la app, el script de
validación) es la única que usa 0.8. Antes de "corregir" esa
inconsistencia aparente, se probó con datos reales de hoy qué pasaría:

| Ticker | Precio (gw=0.8) | Precio (gw=1.0) | Efecto |
|---|---|---|---|
| AMZN | $112.74 | $75.71 | empeora -14.3pp |
| MSFT | $330.28 | $281.66 | empeora -9.7pp |
| GOOGL | $336.56 | $274.96 | empeora -8.7pp |
| META | $525.54 | $352.93 | empeora -24.3pp |
| AAPL | $109.18 | $106.03 | empeora -1.0pp |
| KO | $70.11 | $70.92 | ~neutro |
| PG | $218.82 | $218.63 | ~neutro |
| JNJ | $357.78 | $371.62 | **empeora +5.0pp de sobrevaloración** |

**Hallazgo real, no anticipado:** pasar a Gordon puro (1.0) empeora la
desviación agregada en Big Tech de forma sustancial, y en JNJ concreto
*amplifica* la sobrevaloración ya documentada (sección 9 de
`docs/METHODOLOGY.md`) causada por la inestabilidad numérica de Gordon
Growth cuando el spread WACC-g es estrecho. El motivo mecánico:
blendear con un múltiplo de comparables ancla una parte del valor
terminal a una referencia de mercado que NO sufre esa inestabilidad,
amortiguando el efecto. Gordon puro expone el 100% del valor terminal a
esa fragilidad, en vez del 20% actual.

**Decisión:** se mantiene `gordon_weight=0.8` como default de la capa
de orquestación (app, `value_ticker()`, script de validación) —
**no** porque 0.8 en concreto esté derivado de primeros principios (no
lo está: no hay una forma de calcular el blend "óptimo" sin análisis
específico por compañía, para eso está el slider), sino porque hay una
razón real para preferir un blend por defecto frente a cualquiera de
los dos extremos: `0.0` dependería por completo de un universo de
comparables ya señalado como pequeño/heterogéneo (I4); `1.0` expone el
100% del valor terminal a una fragilidad numérica ya documentada y
verificada esta sesión que empeora el resultado. `0.8`, heredado del
Excel, cae en un punto razonable de ese rango intermedio — se conserva
en vez de sustituirlo por otro número igual de arbitrario.
`DCFInputs.gordon_weight=1.0` en el motor NO es una inconsistencia: es
el default correcto para la primitiva de bajo nivel, cuando no se sabe
si habrá un múltiplo de comparables disponible ni si es de fiar — la
capa de orquestación, que sí tiene esa información, decide distinto con
motivo.

**Nueva evidencia cuantitativa (sesión 17, evaluación de un grupo real
de utilities reguladas — NEE, DUK, SO, D, vía yfinance):** la
fragilidad de Gordon Growth con spread WACC-g estrecho, hasta ahora
ilustrada con un caso puntual (JNJ, tabla de arriba), resulta ser un
**patrón sistemático de sector completo**, no una excepción aislada.
El WACC construido con peers (beta relevered ~0.32-0.65, típico de
utilities reguladas de bajo riesgo) sale entre 5.20% y 6.16% para las 4
compañías — con `terminal_growth_rate=2.5%` fijo, el spread WACC-g cae
por debajo del umbral prudente de 3% (`MIN_PRUDENT_WACC_GROWTH_SPREAD`,
dispara el aviso ya existente) en 3 de los 4 casos (DUK: 2.90pp, SO:
2.99pp, D: 2.70pp — solo NEE, con el beta más alto del grupo, 0.64,
queda holgado en 3.66pp). El resultado en precio conservador es
extremo: DUK $29.35 vs mercado $120.22 (-75.6%), SO $8.34 vs $88.11
(-90.5%), y **D da un precio implícito NEGATIVO de -$255.48** (vs
mercado $65.84) — matemáticamente consistente con la fórmula (mismo
mecanismo que I10), pero un resultado que ningún analista presentaría
sin contexto adicional.

**Confirma, con un caso más severo, la lógica de M2:** con
`gordon_weight=0.8`, el 20% de peso en el múltiplo de comparables
amortigua parcialmente el efecto (evita que el precio explote a
infinito), pero no lo suficiente cuando el spread es tan estrecho como
en este sector — el resultado sigue siendo económicamente absurdo en 3
de 4 casos. Esto no cambia la decisión de M2 (`gordon_weight=1.0`
expondría el 100% del valor terminal a esta misma fragilidad, sería
peor, no mejor), pero sí confirma que **sectores de bajo beta y bajo
WACC construidos vía comparables son, como grupo, el caso de uso donde
la fragilidad de Gordon Growth con spread estrecho es más probable de
encontrarse en la práctica** — no un caso de laboratorio aislado como
JNJ, sino el comportamiento esperable de cualquier sector regulado de
bajo riesgo (utilities, algunas REITs si se forzara el DCF genérico,
consumo defensivo con beta bajo). El aviso de spread estrecho ya
existente cumple su función (avisa en 3 de 4 casos reales), pero un
usuario que ignore el aviso y confíe en el número de D vería un precio
implícito negativo sin entender por qué — mismo espíritu de "avisar, no
maquillar" que I10, aplicado aquí a escala de sector completo en vez de
a un ticker aislado.

### M3. `DCFInputs` no valida `wacc > 0` ni `0 <= gordon_weight <= 1` — ✅ CORREGIDO (sesión 15)

`__post_init__` valida longitudes de listas y `diluted_shares > 0`, pero
no protege contra un WACC negativo o cero, ni contra un `gordon_weight`
fuera de `[0,1]` (p. ej. 1.5, que produciría un blended_terminal_value
extrapolado, no una media ponderada real). Estos valores nunca deberían
llegar así desde el pipeline real (WACC siempre positivo por
construcción, gordon_weight siempre viene de un slider acotado a
[0,1]), pero la clase en sí no lo garantiza si se usa directamente
(p. ej. en un test futuro, o si alguien integra `engine/` en otro
proyecto sin pasar por la interfaz).

**Cómo se corrigió:** dos comprobaciones nuevas en `DCFInputs.__post_init__()`:
`wacc <= 0` y `not (0.0 <= gordon_weight <= 1.0)`, ambas con
`ValueError` explícito — mismo estilo que las validaciones ya
existentes de longitudes y `diluted_shares`. Los límites 0.0 y 1.0
quedan incluidos a propósito (Gordon puro o múltiplo puro son
escenarios válidos, no un error).

**Verificado:** 4 tests de regresión nuevos (WACC no positivo,
`gordon_weight` por encima de 1, por debajo de 0, y aceptación
explícita de ambos límites 0.0/1.0). Confirmado que ningún test ni
ninguna ruta del pipeline real (`scenarios.py`, `validation.py`,
`app/streamlit_app.py`) construye `DCFInputs` con valores fuera de
estos rangos — el cambio es puramente defensivo, no modifica ningún
resultado existente. 122 tests en total, todos en verde. Servidor
Streamlit reiniciado y verificado arrancando limpio tras el cambio.

### M4. `gordon_growth_terminal_value()` se calcula siempre, incluso con `gordon_weight=0` — ✅ CORREGIDO (sesión 15)

Visto en `run_dcf()`: el cálculo y el aviso de spread WACC-g estrecho
se disparan aunque el resultado de Gordon Growth no vaya a usarse en
absoluto (peso 0 en el blend). No es un bug de resultado (la media
ponderada da el número correcto), pero si un usuario pone
`gordon_weight=0` específicamente para evitar la inestabilidad de
Gordon Growth en un caso con spread estrecho, seguirá viendo el aviso
—que en ese caso concreto ya no es relevante para su resultado— y
gastando cómputo en un valor que se descarta.

**Hallazgo más serio de lo que parecía al auditar solo el aviso:**
`gordon_growth_terminal_value()` también lanza `ValueError` si
`wacc <= terminal_growth_rate`. Antes del fix, poner `gordon_weight=0`
precisamente para esquivar un caso patológico de Gordon Growth (WACC
por debajo de g, o un spread demasiado estrecho) **no lo esquivaba en
absoluto** — `run_dcf()` seguía llamando a la función y el DCF entero
fallaba con una excepción, pese a que el resultado de Gordon iba a
descartarse por completo en el blend. El "peso 0" no protegía nada.

**Cómo se corrigió:** `run_dcf()` ahora calcula `gordon_tv` solo cuando
puede llegar a contar para el resultado: si hay un múltiplo de salida
disponible Y `gordon_weight > 0`, o si no hay múltiplo de salida (caso
en el que Gordon es la única fuente de valor terminal posible, sin
importar el peso). Con `gordon_weight=0` y múltiplo disponible,
`gordon_terminal_value` queda en `0.0` y ni se calcula ni se avisa.

**Verificado:** 4 tests de regresión nuevos — confirman que (1) un caso
que antes lanzaba `ValueError` con `gordon_weight=0` ahora funciona sin
error y usa el múltiplo de salida puro; (2) un caso con spread estrecho
y `gordon_weight=0` ya no emite el aviso (verificado forzando
`warnings.simplefilter("error")` para que cualquier aviso residual
falle el test); (3) sin múltiplo de salida, Gordon sigue calculándose
pese a `gordon_weight=0` (es la única fuente posible); (4) con
`gordon_weight>0`, Gordon se sigue calculando y contribuyendo al blend
normalmente. Revalorado AMZN (`gordon_weight=0.8`, camino no afectado
por el fix): precio implícito idéntico, **$98.00**, confirmando que no
cambia ningún resultado existente. 126 tests en total, todos en verde.
Servidor Streamlit reiniciado y verificado arrancando limpio.

### M5. Sin verificación de divisa de reporte — ✅ CORREGIDO (sesión 16)

Ninguna función comprueba `reportedCurrency` (Alpha Vantage) o el
equivalente en yfinance. Si un ticker reportara en una divisa distinta
de USD (no ocurre en los 9 tickers probados, pero sí en ADRs de
compañías extranjeras con reporte local), se mezclarían cifras en esa
divisa con un risk-free rate y una prima de riesgo en USD sin ningún
aviso — un error silencioso de escala completo, no solo un sesgo
pequeño.

**Cómo se corrigió:** `market_snapshot()` en ambos proveedores expone
ahora un campo `currency` — `overview.get("Currency")` en
`data_provider.py` (Alpha Vantage), `info.get("financialCurrency") or
info.get("currency")` en `yfinance_provider.py` (se prioriza la divisa
de los ESTADOS FINANCIEROS sobre la de cotización, porque un ADR puede
cotizar en USD con estados financieros en otra divisa — exactamente el
caso que hace falta detectar). `app/streamlit_app.py` bloquea la
valoración con un error explícito (`st.error` + `st.stop()`) si
`currency` viene informada y no es `"USD"` — deliberadamente un bloqueo,
no solo un aviso, porque el error resultante de no bloquear sería un
error de escala completo (WACC y precio implícito mal por un factor
arbitrario), no un matiz a comunicar. `currency=None` (dato no
reportado por el proveedor) NO bloquea, porque no hay evidencia de
problema, solo ausencia de dato. En modo "cualquier ticker", la
comprobación se hace ANTES de calcular el WACC (no después), para no
mostrar brevemente un WACC "válido" y su aviso habitual justo antes de
bloquear — una secuencia confusa detectada y corregida durante la
propia verificación de este arreglo.

**Verificado con datos reales, no solo con fixtures:** `TM` (Toyota
Motor, ADR que cotiza en USD) reporta `financialCurrency="JPY"` de
verdad vía yfinance — probado en la app real, bloquea con el mensaje
esperado y sin traceback. `AMZN` (USD) sigue funcionando exactamente
igual que antes. 4 tests de regresión nuevos (2 por proveedor).

---

### M6. Tipo impositivo: media histórica plana, incluso en el valor terminal a perpetuidad — ✅ DECISIÓN EXPLÍCITA E INVESTIGADA (sesión 17): se mantiene plano, con evidencia directa del Excel de referencia

**Qué es:** `default_assumptions_from_history()` fija `tax_rate` como la
media de los últimos `lookback_years` años, y ese valor se mantiene
plano durante todo el horizonte explícito Y dentro del valor terminal
Gordon Growth (que representa flujos a perpetuidad) — a diferencia de
margen/D&A/CapEx, `tax_rate` no tiene fade hacia ningún valor de largo
plazo distinto (documentado como decisión deliberada en el docstring:
"fade de tipo impositivo no es práctica estándar").

**Por qué podría importar:** el tipo impositivo EFECTIVO histórico de
Big Tech es muy volátil año a año y sistemáticamente inferior al tipo
estatutario/marginal de EE.UU. (~21% federal + estatal ≈ 24-27%) —
verificado con datos reales: AMZN 54.2%/19.0%/13.5%/19.7% en los
últimos 4 ejercicios, META 19.5%/17.6%/11.8%/29.6%. Estas oscilaciones
grandes reflejan ítems no recurrentes (beneficios fiscales de
stock-based comp, créditos I+D, arbitraje de tipo entre jurisdicciones)
que un banco de primer nivel no asumiría que se mantienen sin cambios
para siempre en la perpetuidad — sobre todo con el impuesto mínimo
global (Pilar Dos de la OCDE, en despliegue progresivo desde 2024)
apuntando específicamente a reducir ese tipo de arbitraje. Nuestra
media de `lookback_years` (típicamente 3) ya suaviza buena parte del
ruido año a año, pero sigue sin distinguir "nivel normalizado actual"
de "nivel sostenible a perpetuidad".

**Por qué NO se ha tocado todavía:** cambiar esto sin investigar
primero repetiría exactamente el error que el proyecto ya evitó una vez
con M2 (`gordon_weight`) — parecer una mejora obvia sobre el papel y
resultar, con datos reales, en un efecto distinto o incluso contrario
al esperado. Requiere el mismo tratamiento que M2: medir el impacto
real en los 8 tickers piloto (¿un fade hacia el tipo estatutario sube o
baja el precio? ¿en qué magnitud? ¿agrava o alivia la brecha ya
documentada con Big Tech?) antes de decidir, no una intuición sin
verificar.

**Investigado con datos reales, dos pasos:**

1. **Impacto de un fade hacia el 25% estatutario** (bump-and-reprice
   sobre los 5 tickers de Big Tech, mismo método que M2): baja el
   precio implícito entre **-8.5% (AAPL) y -19.0% (GOOGL)** —
   empeoraría la brecha ya documentada frente al mercado en Big Tech,
   no la mejora. Por sí solo, esto NO seria motivo suficiente para
   descartar el cambio (el proyecto nunca decide por "qué acerca más al
   precio de mercado") — pero exigía buscar evidencia independiente de
   qué es lo metodológicamente correcto, no solo el efecto.

2. **Evidencia directa del propio Excel de referencia** (la fuente de
   verdad del proyecto): se abrió `Advanced DCF.xlsx` y se leyeron las
   celdas reales de tipo impositivo proyectado para AMZN, hoja
   "Operating Model" (fila 52, "% tax rate") y "North America" (fila
   19) — el analista de JPM proyecta **17.75% (2024), 15.28% (2025),
   16.13% (2026), 17.03% (2027), 17.03% (2028), 16.64% (2029)**: una
   banda estrecha de ~15-18%, sin ninguna tendencia de convergencia
   hacia el ~25% estatutario/marginal, ni siquiera en los años más
   lejanos del horizonte. El propio banco de referencia ancla el tipo
   impositivo cerca del nivel reciente observado, no de un tipo
   normativo de largo plazo.

**Decisión: se mantiene el tipo histórico plano, sin fade.** No es una
intuición ni una constante heredada sin justificar (como se temía al
abrir este hallazgo) — es la misma práctica que usa literalmente "el
modelo a seguir" (principio del blueprint), verificada celda a celda,
no solo citada de memoria. Documentado en `docs/METHODOLOGY.md` sección
28.

---

### M7. `Debt/EBITDA` usa deuda bruta, no neta, sin aclararlo en la interfaz — ✅ CORREGIDO (sesión 17)

**Qué es:** `debt_to_ebitda()` usa deuda BRUTA (`total_debt`), no deuda
NETA (`total_debt - cash`) — y tanto el nombre de la función como la
etiqueta "Debt/EBITDA" en la pestaña "Fundamentales" no dejan claro
cuál de las dos versiones se está mostrando.

**Por qué importa:** Net Debt/EBITDA es, si acaso, más común que la
versión bruta en informes de crédito bancarios reales — precisamente
porque distingue a una empresa con caja neta positiva (deuda bruta alta
pero riesgo de crédito bajo, p.ej. AAPL en ciertos ejercicios) de una
genuinamente apalancada. No es un cálculo incorrecto (deuda bruta/EBITDA
es una métrica legítima y también de uso común), pero la ambigüedad de
la etiqueta sí es una brecha real de claridad frente al estándar de un
informe bancario, donde ambas versiones suelen aparecer explícitamente
diferenciadas.

**Cómo se corrigió:** `engine.ratios.net_debt_to_ebitda()` (nueva
función, mismo guard de EBITDA<=0 que `debt_to_ebitda()`, pero un valor
negativo aquí SÍ es interpretable — caja neta positiva). `RatioSnapshot`
expone ambos campos; la UI ahora muestra "Deuda bruta/EBITDA" y "Deuda
neta/EBITDA" como métricas separadas y explícitamente rotuladas, y el
memo usa la neta como cifra principal de apalancamiento. Verificado con
3 tests nuevos en `test_ratios.py` (cálculo, caja neta positiva da
negativo, EBITDA no positivo lanza `ValueError` en ambas versiones).

---

### M8. `historical_financials()` (yfinance) no reconocía la etiqueta "Depreciation Amortization Depletion" — ✅ CORREGIDO (sesión 17, evaluación del sector Utilities)

**Qué es:** al evaluar un grupo de comparables de utilities reguladas
(NEE, DUK, SO, D), **D (Dominion Energy)** hacía crashear la
construcción de la proyección con `ValueError: Sin ningún dato válido
de 'D&A % ventas'` — el 100% del histórico de `d_and_a` salía `None`.
Investigado contra el `cash_flow` crudo de yfinance: el dato SÍ existe
para D, pero bajo la etiqueta `"Depreciation Amortization Depletion"`
(convención contable de empresas con activos de extracción/depleción —
energía, utilities con generación, minería), no la etiqueta estándar
`"Depreciation And Amortization"` que `historical_financials()`
buscaba en exclusiva.

**Por qué importa doble:** (1) es un hueco de cobertura real y
alcanzable con un ticker de primera línea (Dominion Energy, componente
del S&P 500, no un caso de laboratorio); (2) el mensaje de error que sí
llega al usuario (protegido por el `try/except` de la sección de
cómputo compartida, mismo patrón que I9) es **engañoso** en este caso
concreto: apunta a "sectores con estados financieros no estándar
(bancos/financieras, REITs)" — pero D es una utility con estados
financieros perfectamente estándar; el problema real es una etiqueta
de yfinance no cubierta, no una incompatibilidad estructural de
sector. Un usuario que confiara en ese mensaje concluiría erróneamente
que las utilities, como grupo, no son valorables con la herramienta.

**Cómo se corrigió:** `historical_financials()` ahora hace fallback a
`"Depreciation Amortization Depletion"` cuando `"Depreciation And
Amortization"` no está disponible. Verificado con datos reales: D pasa
de un histórico 100% `None` a valores completos (p.ej. $2.68bn en el
último ejercicio), y el pipeline completo (WACC, comps, DCF,
escenarios, reverse DCF, sensibilidades, ratios) corre sin excepciones
para las 4 utilities del grupo.

**Nota relacionada, no corregida (alcance limitado a este hallazgo):**
el mensaje de error genérico de la sección 490-499 de
`app/streamlit_app.py` sigue atribuyendo cualquier fallo de
`default_assumptions_from_history()` a "bancos/REITs" — correcto para
I2/I11, pero potencialmente engañoso para futuros huecos de datos de
proveedor no relacionados con el sector. Queda como mejora de mensaje,
no como bug, fuera del alcance de esta corrección puntual.

---

### M9. `build_peer_wacc()` (modo "universo cacheado") sin guard de `interest_expense`/`tax_rate` vacíos, a diferencia del modo "cualquier ticker" — ✅ CORREGIDO (sesión 17, evaluación del grupo small/mid-cap)

**Qué es:** evaluando un grupo real de small/mid-caps de consumo
(MCRI, SHOO, BOOT, FIZZ, vía yfinance), **FIZZ** (National Beverage,
una compañía real y conocida por operar SIN deuda) hizo crashear un
script de evaluación con un `IndexError` crudo de pandas
(`hist["interest_expense"].dropna().iloc[-1]` sobre una Serie 100%
vacía). Investigado contra `app/streamlit_app.py`: el modo "cualquier
ticker" (línea 260+) YA tenía un guard explícito para este caso exacto
(`if interest_expense_series.empty: raise ValueError(...)`, con
mensaje claro) — pero `build_peer_wacc()` (línea 171, usado por el modo
"universo cacheado", el que usa cualquier visitante por defecto) NO lo
tenía, pese a resolver el mismo cálculo.

**Por qué importa aunque no sea explotable hoy:** los `CACHED_GROUPS`
actuales (Big Tech, Consumo defensivo: KO/PG/JNJ) no incluyen ninguna
empresa sin deuda, así que esta ruta concreta no es alcanzable por un
usuario real de la app HOY. Pero es una inconsistencia de robustez
real entre dos funciones que hacen literalmente el mismo cálculo: si
una futura sesión añade un grupo cacheado que incluya una empresa sin
deuda (defensivo/consumo básico es precisamente el tipo de sector
candidato), el usuario vería el texto interno de pandas
("single positional indexer is out-of-bounds") en vez de un mensaje
accionable — no un crash (el `try/except` del llamador ya lo cubre),
pero sí una regresión de calidad silenciosa frente al estándar ya
establecido por el otro modo.

**Cómo se corrigió:** `build_peer_wacc()` ahora comprueba
`tax_rate`/`interest_expense` vacíos ANTES de construir el WACC, con el
mismo mensaje específico y accionable que ya usaba el modo "cualquier
ticker" ("Sin dato de gasto financiero disponible para '{target}'.").

**Verificado:** 1 test de regresión nuevo en `test_app.py` (AMZN con
`interest_expense` vacío en el modo "universo cacheado" por defecto →
mensaje accionable, no traceback). 228 tests en total, todos en verde.

---

## Informativo (sin acción necesaria, pero documentado)

### N1. Tipo impositivo y ΔNWC: forma de fade verificada contra el Excel

Ampliando la comprobación de la sesión 13 (que cubrió margen/D&A/CapEx):
regresión lineal sobre las series del Excel da **tipo impositivo
R²=0.001** (sin tendencia real, pura variación año a año alrededor de
~16.6% — nuestro tratamiento plano es correcto) y **ΔNWC R²=0.425**
(tendencia débil, pero rango muy estrecho, -3.4% a -2.9%). Sin cambios
necesarios en ninguno de los dos.

### N2. Sin tests automatizados de `app/streamlit_app.py` — ✅ CORREGIDO (sesión 16)

Ningún test de `pytest` ejercitaba el script de Streamlit directamente
(era la práctica estándar de la industria para apps Streamlit, dado su
modelo de ejecución) — se verificaba cada sesión con smoke-tests
manuales (arranque del servidor + réplica de la ruta de cómputo exacta
con datos reales, más capturas de pantalla vía Playwright).

**Cerrado con `tests/test_app.py`**, usando `streamlit.testing.v1.AppTest`
(framework de test headless de Streamlit, sin navegador ni red). Detalle
no obvio, encontrado escribiendo la suite: `AppTest` re-ejecuta el
script COMPLETO desde cero en cada `.run()` (imita de verdad el modelo
de rerun de Streamlit) — parchear `app.streamlit_app.load_av_universe`
no intercepta nada, porque esa referencia queda obsoleta en cuanto el
script se re-ejecuta; hay que parchear en el módulo de ORIGEN
(`engine.data_provider`, `engine.yfinance_provider`), de donde el
script vuelve a importar en cada ejecución. Segundo detalle encontrado:
`@st.cache_data` sobrevive entre tests dentro del mismo proceso (por
diseño, para sobrevivir reruns) — sin limpiar el caché entre tests, un
test exitoso anterior deja el resultado cacheado y los tests de fallo
de API nunca vuelven a invocar la función parcheada.

9 tests que fijan como regresión automática lo que antes solo se
verificaba a mano: el camino feliz por defecto, el fix del delta
"Crea valor" (verde) de esta misma sesión, el manejo de errores de
Alpha Vantage en modo "universo cacheado" (ver el nuevo hallazgo de la
sección de Importantes más abajo), I3 (ticker inválido, beta ausente) y
M5 (divisa no USD, con contraprueba de que USD no bloquea).

### N3. Degradación silenciosa de la ventana de histórico

`default_assumptions_from_history()` no verifica que consiguió
exactamente `lookback_years + 1` puntos para el CAGR — si hay menos
(típico con yfinance, que solo da ~4 años), usa silenciosamente una
ventana más corta sin avisar. No es incorrecto (la lógica sigue siendo
válida con menos puntos), pero un usuario podría no darse cuenta de que
su `lookback_years=5` se convirtió en un CAGR de 3 años sin ningún
aviso en la interfaz.

---

## Fortalezas confirmadas (para que esta auditoría no sea solo una lista de problemas)

- **Motor de cálculo exacto contra el Excel real**, no aproximado:
  WACC/CAPM, FCFF, descuento mid-year+stub, TSM, valor terminal blended
  reproducen $216.41/acción al céntimo (`tests/test_valuation.py`).
- **3 bugs de datos/serialización reales encontrados y corregidos** en
  sesiones anteriores (`interest_expense=0` espurio, `np.bool_` en
  JSON, `Infinity` en JSON) — todos verificando de punta a punta con
  datos reales antes de dar una pieza por cerrada, no solo con tests
  sintéticos.
- **La forma del fade de crecimiento se corrigió tras comparar contra
  el Excel** (sesión 12) con mejora medible y honesta (54.8%→42.8% de
  desviación media), y la de margen/D&A se confirmó ya correcta con
  evidencia estadística (R²>0.97), no solo inspección visual.
- **El múltiplo de salida se corrigió** de "propio de la empresa" a
  "mediana de comparables" (sesión 14), con el efecto mixto reportado
  con honestidad en vez de maquillado.
- **225 tests, cero dependen de red** — toda la suite corre offline con
  fixtures fieles al formato real de las APIs, incluidos 12 tests de la
  app en sí (`streamlit.testing.v1.AppTest`, sesión 16-17) y CI en
  GitHub Actions corriéndolos en cada push. Complementado con una
  prueba de estrés puntual (11 tickers reales, red real, sesión 17) que
  la suite offline no puede replicar por diseño.
- **Capa generativa desacoplada del cálculo por diseño**, no como
  parche — el LLM nunca ve datos crudos, solo un paquete ya cerrado.
- **Auditoría matemática/financiera a fondo (sesión 17) confirmó
  correctos los puntos donde un DCF amateur suele fallar**: la
  convención mid-year+stub descuenta el valor terminal con el mismo
  periodo que el último flujo explícito (n-0.5, no n); Gordon Growth
  usa `FCFF_n×(1+g)/(WACC-g)`, no el error de "off-by-one" de omitir el
  `×(1+g)`; el WACC no tiene la circularidad clásica (usa market cap
  actual, no el equity value que el propio DCF produce); FCFF no cuenta
  dos veces el escudo fiscal de la deuda; el EBITDA es consistente
  entre `ratios.py`, `valuation.py` y `comps.py` (verificado con datos
  reales de 6 tickers, 0% de diferencia).

---

## Prioridad recomendada de arreglo

1. ~~**C1 (stub period)**~~ — ✅ corregido en esta sesión.
2. ~~**I3 (excepciones no controladas)**~~ — ✅ corregido en esta sesión
   (y encontrado/corregido un bug real de raíz de paso: `historical_financials()`
   crasheaba con `KeyError` para un ticker inexistente, en ambos proveedores).
3. ~~**I1 (risk-free rate en vivo)**~~ — ✅ corregido en esta sesión
   (risk-free rate vía `^TNX`/yfinance con fallback explícito; ERP
   convertida en slider ajustable, sin fuente en vivo fiable disponible).
4. ~~**M1 (pin de versiones)**~~ — ✅ corregido en esta sesión (10
   dependencias directas fijadas a la versión ya verificada en el venv).
5. ~~**M3 (validación de `wacc`/`gordon_weight` en `DCFInputs`)**~~ — ✅
   corregido en esta sesión (arreglo mecánico y defensivo, no cambia
   ningún resultado del pipeline real).
6. ~~**M4 (Gordon Growth se calcula/avisa aunque su peso sea 0)**~~ — ✅
   corregido en esta sesión (resultó más serio de lo previsto: podía
   bloquear con `ValueError` un DCF que `gordon_weight=0` debía esquivar).
7. ~~**M5 (verificación de divisa de reporte)**~~ — ✅ corregido (sesión
   16) — bloquea explícitamente si la divisa de los estados financieros
   no es USD, verificado con un ticker real (Toyota, JPY).
8. ~~**M2 (`gordon_weight=0.8` sin justificar)**~~ — ✅ decisión explícita
   (sesión 16) — investigado con datos reales, se mantiene 0.8 porque un
   blend por defecto protege contra la inestabilidad ya documentada de
   Gordon Growth puro; no se cambia por cambiar.
9. ~~**I2 (Treasury Stock Method sin conectar)**~~ y ~~**I4 (universo de
   comparables pequeño)**~~ — ✅ aceptados explícitamente como
   limitaciones estructurales (sesión 16), sin fuente de datos gratuita
   disponible para resolverlos de verdad — señalados ahora en la propia
   UI, no solo en este documento.
10. ~~**I5 (sin manejo de errores en modo "universo cacheado")**~~ — ✅
    corregido (sesión 16, tras cerrar la auditoría original) — hallazgo
    nuevo encontrado al pedir "que la herramienta sea perfecta" y seguir
    auditando; incluye también fijar un `ttl` en el caché de universo
    (antes vivía tanto como el proceso).
11. ~~**N2 (sin tests automatizados de la app)**~~ — ✅ corregido (sesión
    16) con `streamlit.testing.v1.AppTest` + CI en GitHub Actions.
12. ~~**I6 (`cost_of_debt()` sin proteger `total_debt<=0`)**~~ y
    ~~**I7 (`debt_to_ebitda()` sin proteger `EBITDA<=0`)**~~ — ✅
    corregidos (sesión 17) — dos crashes reales de división por cero
    encontrados en una auditoría técnica/matemática a fondo pedida
    explícitamente por el usuario, no disparados por el universo piloto
    actual pero alcanzables con inputs reales (empresa sin deuda, año
    de EBITDA nulo). I7 en particular podía saltarse el `except
    ValueError` ya existente en `app.py`, porque `ZeroDivisionError` no
    es su subclase.
13. **M6 (tipo impositivo plano en el valor terminal)** y **M7
    (Debt/EBITDA bruto sin aclarar)** — ⏳ abiertos (sesión 17),
    pendientes de investigar/decidir con el mismo rigor que M2.
14. ~~**I8 (`market_snapshot()` de yfinance usaba `info["totalDebt"]`/
    `["totalCash"]`, no fiables)**~~ — ✅ corregido (sesión 17) —
    encontrado investigando la viabilidad de un tercer proveedor (SEC
    EDGAR): `.info["totalDebt"]` y `.balance_sheet.loc["Total Debt"]`
    discrepaban hasta un 65% en AMZN (una inconsistencia real dentro de
    la propia librería yfinance, no una diferencia de definición entre
    proveedores) — `historical_financials()` ya usaba la fuente fiable,
    `market_snapshot()` no. Efecto medido en el universo piloto real:
    pequeño (KO/PG/JNJ no tienen mucho leasing), pero potencialmente
    grave en modo "cualquier ticker" con una empresa con mucho leasing
    (retail, aerolíneas).
15. ~~**I9 (`IndexError` sin capturar con partidas históricas vacías)**~~
    y ~~**I10 (valor terminal negativo/nulo sin aviso)**~~ — ✅
    corregidos (sesión 17) — encontrados en una prueba de estrés con 11
    tickers reales fuera del universo piloto (petición explícita del
    usuario: probar la herramienta "como si de verdad un banco fuese a
    usarla"). 3 de 11 tickers (XOM, PLD, JPM) crasheaban con
    `IndexError`; TSLA y BA mostraban valores terminales negativos
    (hasta -$184 mil millones) sin ningún aviso.
16. **I11 (DCF FCFF no encaja con bancos/REITs)** — ✅ documentado como
    limitación estructural (sesión 17), mismo criterio que I2/I4;
    mejorado con un aviso proactivo por sector en el Lote A (JPM/PLD
    verificados reales).
17. ~~**M7 (Debt/EBITDA bruto sin aclarar)**~~ — ✅ corregido (Lote A,
    sesión 17): `net_debt_to_ebitda()` nueva, UI con ambas versiones
    explícitamente rotuladas.
18. ~~**M6 (tipo impositivo plano en el valor terminal)**~~ — ✅
    decisión explícita e investigada (Lote B, sesión 17): evidencia
    directa del propio Excel de referencia (celdas reales de tipo
    impositivo proyectado para AMZN, 15-18% en 2024-2029, sin converger
    al 25% estatutario) confirma que el enfoque actual ya coincide con
    la práctica del banco de referencia.
19. ~~**I12 (hiper-crecimiento extremo)**~~ — ✅ corregido parcialmente
    (Lote B, sesión 17): aviso cuando el CAGR plano supera 50%/año,
    verificado con NVDA real (dispara), AMZN/MSFT/TSLA/BA (no).
20. ~~**I13 (precio de mercado de Alpha Vantage mal para GOOGL/META)**~~
    — ✅ corregido (Lote C, sesión 17) — encontrado construyendo el
    backtest walk-forward. yfinance pasa a ser la fuente preferida de
    cotización para todos los tickers. Corrige la desviación media del
    universo piloto de 41.82% a 34.21% — un bug real presente desde
    sesiones anteriores, no solo del backtest.

**Con esto, no queda ningún hallazgo crítico, importante o moderado
abierto** — todos corregidos, decididos explícitamente con evidencia, o
aceptados como limitación estructural documentada (I2, I4, I11). El
resto (N1/N3, informativos sin acción necesaria) no requiere más
trabajo. Próximos pasos del proyecto en `docs/PROGRESS_REVIEW.md` y
`estado.md` sección "Próximo paso inmediato".
