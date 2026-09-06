# Auditoría técnica — Agente de valoración DCF

**Fecha:** 2026-09-05 (sesión 15). **Alcance:** todo `engine/`, `ai/`,
`app/`, `tests/`, comparado sistemáticamente contra `Advanced DCF.xlsx`
(el modelo profesional de referencia). Metodología: relectura completa
del código (no solo memoria de sesiones anteriores) + verificación
puntual de cada hallazgo con datos reales antes de reportarlo — mismo
estándar que se ha aplicado en toda la sesión.

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

**1 hallazgo crítico (✅ corregido), 4 importantes (2 ✅ corregidos), 5
moderados (3 ✅ corregidos), 3 informativos.** Se está corrigiendo uno
por uno, en el orden de prioridad de la sección final — este documento
se actualiza a medida que cada uno se cierra.

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

### I2. El Treasury Stock Method está construido y validado, pero nunca se usa

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
hacer salvo documentarlo como limitación conocida (ya lo está, mejor
esta sesión) y usar `shares_outstanding` como aproximación razonable —
que es lo que ya se hace, solo que sin decirlo tan explícitamente en el
código como en la documentación.

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

### I4. Universo de comparables pequeño y no siempre homogéneo

Ya identificado y documentado en la sesión 14 (`docs/METHODOLOGY.md`
sección 16): con solo 3-5 comparables por grupo, un ticker con un
perfil de negocio distinto al resto del grupo (AAPL dentro de "Big
Tech", que en realidad es hardware premium frente a cloud/software)
recibe un múltiplo de salida poco representativo. Se incluye aquí
formalmente como hallazgo de auditoría, no solo nota de sesión — está
verificado con el impacto cuantitativo real ya medido (AAPL empeoró de
-56.1% a -63.0% de desviación tras el fix del múltiplo de peers).

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

### M2. `gordon_weight` por defecto (0.8) es una constante heredada, no justificada para el caso general

El Excel usa 80% Gordon / 20% múltiplo específicamente para el segmento
North America (maduro, bajo crecimiento) — un peso pensado para ESE
segmento, no una regla general de la industria. Nuestro motor lo usa
como valor por defecto para cualquier compañía, en cualquier sector,
sin ninguna justificación propia más allá de "es lo que traía el
Excel". No es necesariamente incorrecto, pero sí es una constante sin
razonar explícitamente, cuando el proyecto se ha esforzado en razonar
cada supuesto (ver secciones 5-16 de `docs/METHODOLOGY.md`). Al menos
merece una nota explícita de que es una elección arbitraria heredada,
no derivada.

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

### M5. Sin verificación de divisa de reporte

Ninguna función comprueba `reportedCurrency` (Alpha Vantage) o el
equivalente en yfinance. Si un ticker reportara en una divisa distinta
de USD (no ocurre en los 9 tickers probados, pero sí en ADRs de
compañías extranjeras con reporte local), se mezclarían cifras en esa
divisa con un risk-free rate y una prima de riesgo en USD sin ningún
aviso — un error silencioso de escala completo, no solo un sesgo
pequeño.

---

## Informativo (sin acción necesaria, pero documentado)

### N1. Tipo impositivo y ΔNWC: forma de fade verificada contra el Excel

Ampliando la comprobación de la sesión 13 (que cubrió margen/D&A/CapEx):
regresión lineal sobre las series del Excel da **tipo impositivo
R²=0.001** (sin tendencia real, pura variación año a año alrededor de
~16.6% — nuestro tratamiento plano es correcto) y **ΔNWC R²=0.425**
(tendencia débil, pero rango muy estrecho, -3.4% a -2.9%). Sin cambios
necesarios en ninguno de los dos.

### N2. Sin tests automatizados de `app/streamlit_app.py`

Ningún test de `pytest` ejercita el script de Streamlit directamente
(es la práctica estándar de la industria para apps Streamlit, dado su
modelo de ejecución) — se ha verificado cada sesión con smoke-tests
manuales (arranque del servidor + réplica de la ruta de cómputo exacta
con datos reales). Aceptable, pero merece constar como límite conocido
del enfoque de testing.

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
- **126 tests, cero dependen de red** — toda la suite corre offline con
  fixtures fieles al formato real de las APIs.
- **Capa generativa desacoplada del cálculo por diseño**, no como
  parche — el LLM nunca ve datos crudos, solo un paquete ya cerrado.

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
7. Resto, según interés — I2 y M5 son limitaciones más estructurales
   (dependen de datos que no tenemos fácilmente) que bugs a corregir.
