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

**1 hallazgo crítico (✅ corregido), 7 importantes (5 ✅ corregidos —
I6/I7 añadidos en sesión 17, dos crashes reales de división por cero
encontrados en la auditoría técnica/matemática a fondo del motor—, 2 ✅
aceptados como limitación documentada), 7 moderados (4 ✅ corregidos, 1
✅ decisión explícita investigada y mantenida, 2 ⏳ abiertos — M6/M7,
sesión 17, pendientes de investigar/decidir), 3 informativos (1 ✅
corregido — N2, tests automatizados de la app —, 2 sin acción
necesaria).**
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
(`cost_of_debt(0.0, 0.0) == 0.0`); suite completa (180 tests) y
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

### M6. Tipo impositivo: media histórica plana, incluso en el valor terminal a perpetuidad — ⏳ ABIERTO (sesión 17), pendiente de investigar y decidir

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

**Pendiente:** investigar con datos reales y decidir explícitamente —
mantener el tipo plano con motivo justificado, o introducir un fade
hacia el tipo marginal/estatutario para el tramo de largo plazo,
documentando el impacto medido en cualquiera de los dos casos.

---

### M7. `Debt/EBITDA` usa deuda bruta, no neta, sin aclararlo en la interfaz — ⏳ ABIERTO (sesión 17)

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

**Pendiente:** renombrar la métrica existente a "Deuda bruta/EBITDA" (o
similar) para eliminar la ambigüedad, y evaluar añadir "Deuda
neta/EBITDA" como métrica adicional — el dato (`cash`) ya está
disponible en el mismo snapshot, coste de implementación bajo.

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
- **180 tests, cero dependen de red** — toda la suite corre offline con
  fixtures fieles al formato real de las APIs, incluidos 11 tests de la
  app en sí (`streamlit.testing.v1.AppTest`, sesión 16-17) y CI en
  GitHub Actions corriéndolos en cada push.
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

**Quedan dos hallazgos moderados abiertos a propósito** (M6, M7) —
requieren investigación con datos reales antes de decidir, no una
intuición sin verificar, mismo estándar que M2. El resto de esta
auditoría (más allá de N1/N3, informativos sin acción necesaria) tiene
un estado cerrado. Próximos pasos del proyecto en
`docs/PROGRESS_REVIEW.md` y `estado.md` sección "Próximo paso
inmediato".
