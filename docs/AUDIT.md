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

**1 hallazgo crítico (✅ corregido), 13 importantes (10 ✅ corregidos —
I6/I7/I8 de la auditoría matemática/financiera a fondo, I9/I10 de una
prueba de estrés con 11 tickers reales ("como si un banco fuese a
usarla"), I12 (aviso de hiper-crecimiento extremo, corrección parcial:
avisa, no ajusta el número), I13 (precio de mercado de Alpha Vantage
mal para GOOGL/META, corrige la desviación media del universo piloto de
41.82% a 34.21%) —, 3 ✅ aceptados como limitación documentada — I2, I4,
I11: DCF FCFF no encaja con bancos/REITs), 7 moderados (**todos
cerrados**: 6 ✅ corregidos incluido M7 en el Lote A, 1 ✅ decisión
explícita investigada — M2, y M6 recién investigado con evidencia
directa del Excel de referencia y también cerrado), 3 informativos (1
✅ corregido — N2 —, 2 sin acción necesaria). Con esto, **no queda
ningún hallazgo moderado o importante abierto** — I12 se corrigió
parcialmente (aviso, sin umbral de ajuste automático por falta de
evidencia objetiva, mismo criterio que el resto del proyecto).**
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
