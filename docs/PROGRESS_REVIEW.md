# Auditoría de progreso — sesión 16

**Fecha:** 2026-09-06. **Objetivo:** a diferencia de `docs/AUDIT.md`
(catálogo de hallazgos técnicos puntuales, sesión 15), este documento
responde una pregunta más amplia: **¿qué hace la herramienta hoy, qué
no hace, qué hace bien, qué hace mal, y cómo debería continuar el
desarrollo?** Se ha releído el código fuente completo (no solo la
documentación previa) y se ha vuelto a ejecutar el pipeline de
validación end-to-end con datos reales para esta revisión — no es un
resumen de memoria de sesiones anteriores.

---

## 1. Estado de las fases del blueprint

| Fase | Entregable | Estado |
|---|---|---|
| 0 — Alcance | Universo piloto | ✅ Completo — Big Tech/Cloud (AMZN, MSFT, GOOGL, META, AAPL, vía Alpha Vantage) + Consumo defensivo (KO, PG, JNJ, vía yfinance) |
| 1 — Motor de datos | `data_provider.py` / `yfinance_provider.py` | ✅ Completo — cache en disco, ambos proveedores intercambiables (mismo esquema de columnas), robustos a tickers inválidos |
| 2 — Motor de valoración | `valuation.py` | ✅ Completo y **validado exacto** contra el Excel de referencia (WACC, FCFF, descuento, TSM, valor terminal blended reproducen $216.41 al céntimo) |
| 3 — Ratios y comps | `ratios.py`, `comps.py` | ✅ Completo — conectados al memo y a la interfaz |
| 4 — Tests unitarios | `tests/` | ✅ Completo, por encima del pedido — 126 tests, 100% offline (sin red), cubren el motor entero incluyendo casos límite reales encontrados con datos en vivo |
| 5 — Capa generativa | `ai/memo_generator.py` | ⚠️ **Construida pero nunca probada con la API real** — sin `ANTHROPIC_API_KEY` configurada, el botón "Generar memo" del código nunca se ha ejecutado de verdad; solo se ha verificado el ensamblado del payload y del prompt |
| 6 — Interfaz | `app/streamlit_app.py` | ✅ Completo y verificado repetidamente (arranque limpio, sin tracebacks) — dos modos de datos, tabla de comparables, ratios, matriz de sensibilidad, gráfico de escenarios |
| 7 — Validación contra consenso | — | ⚠️ **Hecha, pero con una conclusión que hay que decir con claridad** (sección 2 de este documento) |
| 8 — Despliegue | GitHub + Streamlit Cloud | ❌ **No iniciado** — repo sin remoto configurado (`git remote -v` vacío), nunca subido a GitHub, no desplegado en ningún sitio |
| 9 — Sentiment (extensión) | `EARNINGS_CALL_TRANSCRIPT` | ❌ No iniciado (extensión opcional, prioridad más baja por diseño del blueprint) |

**21 commits**, 126 tests, ~2.360 líneas de Python en `engine/`+`ai/`+`app/`.

---

## 2. La pregunta que más importa: ¿es precisa la herramienta?

Se ha vuelto a ejecutar `engine.validation.validate_universe()` HOY,
con el código actual (todos los arreglos de la auditoría de sesión 15
ya aplicados: stub period real, risk-free rate en vivo, validaciones de
`DCFInputs`, Gordon Growth condicional) y con el risk-free rate real de
hoy (Treasury 10Y = 4.784%, vía `^TNX`). Esto es una medición fresca,
no una cifra recordada de una sesión anterior.

### Resultado agregado, los 8 tickers piloto

| Momento | Desv. media abs. vs. mercado | Qué incluye |
|---|---|---|
| Antes de la auditoría sesión 15 (línea base, `docs/METHODOLOGY.md` sección 16) | **46.5%** | Fade de crecimiento correcto + múltiplo de peers, SIN stub period real ni risk-free rate en vivo |
| **Hoy** (después de C1, I1, M3, M4) | **41.8%** | + stub period real + risk-free rate en vivo (4.784%) + validaciones defensivas |

**Mejora real de ~4.7 puntos porcentuales — pero no uniforme, y el
mecanismo importa más que el número agregado:**

| Grupo | Antes | Hoy | Qué pasó |
|---|---|---|---|
| Big Tech/Cloud (AMZN, MSFT, GOOGL, META, AAPL) | 43.4% | 46.9% | **Empeora.** El risk-free rate en vivo (4.784%) es más alto que la constante congelada (3.909%), lo que sube el WACC ~0.8pp y baja el precio implícito de compañías ya infravaloradas por el motor — las aleja más del precio de mercado. |
| Consumo defensivo (KO, PG, JNJ) | 51.6% | 33.3% | **Mejora mucho.** Estas 3 compañías tienen spread WACC-g estrecho (`MIN_PRUDENT_WACC_GROWTH_SPREAD`, sección 9 de METHODOLOGY.md) — el mismo WACC más alto ENSANCHA ese spread, estabilizando una fórmula de Gordon Growth que antes estaba en una zona de alta sensibilidad y sobrevaloraba mucho (PG +92%, JNJ +63%). |

**Esto no es una coincidencia ni un error de cálculo: es el mecanismo
correcto operando en direcciones opuestas según el régimen de cada
compañía.** Subir el risk-free rate perjudica la precisión donde el
motor ya infravaloraba por una brecha de crecimiento/múltiplo (Big
Tech) y mejora la precisión donde el problema era inestabilidad
numérica de Gordon Growth (staples). Ninguno de los dos efectos es un
bug — ambos son consecuencia directa y explicable de usar un dato real
en vez de uno congelado, que es justo lo que la auditoría corrigió.

### La conclusión honesta que ya se le debe al usuario

**Técnicamente, el motor es exacto** (reproduce el Excel al céntimo) y
**metodológicamente es defendible** (reversión a la media es una
postura de modelado estándar, documentada, no una elección arbitraria).
Pero **como predictor del precio de mercado, con los supuestos por
defecto, la herramienta se equivoca por una media de ~42% en el
universo piloto actual** — y ninguna de las 6 correcciones técnicas de
esta sesión (todas legítimas y necesarias) ha cerrado esa brecha de
forma sustancial, porque **la brecha no es un bug**: es la diferencia
entre "reversión a la media" y "el mercado paga hoy por crecimiento
futuro que el motor, por diseño, no extrapola sin evidencia".

Esto ya se identificó y se documentó honestamente en sesiones
anteriores (`docs/METHODOLOGY.md` secciones 5-9), y sigue siendo cierto
hoy. La respuesta a "¿es útil esta herramienta?" (pregunta que el
usuario ya hizo en una sesión anterior) sigue siendo: **útil como marco
de sensibilidad y trazabilidad de supuestos para analizar una empresa
con rigor, no como una calculadora de "precio justo" que vaya a
coincidir con el mercado.** Esa distinción debería ser explícita en
cualquier README/CV/demo de la herramienta, y ahora mismo no lo es de
forma suficientemente visible en la propia interfaz.

---

## 3. Qué hace bien

- **El motor de cálculo no tiene errores conocidos.** Cada fórmula
  (CAPM, WACC, FCFF, TSM, Gordon Growth, blend con múltiplo de salida)
  está validada línea a línea contra el Excel de un profesional real, no
  contra una implementación genérica de DCF.
- **Arquitectura LLM/cálculo desacoplada, no como parche.** El LLM
  jamás ve datos crudos, solo un paquete ya cerrado (`MemoInput`), y el
  prompt prohíbe explícitamente inventar cifras. Esto es correcto de
  diseño desde la Fase 5, no una corrección posterior.
- **126 tests, 100% offline y deterministas**, con fixtures fieles al
  formato real de las dos APIs — la suite entera corre en <1 segundo
  sin depender de red ni de cuota.
- **Escenarios explícitos en vez de un único número falso-preciso**
  (`engine/scenarios.py`): conservador/mantener actual/alcista, cada
  uno con su propia justificación razonada, no una heurística inventada
  para "cubrir todos los casos".
- **Disciplina de verificación real, no solo tests sintéticos.** Cada
  uno de los 6 arreglos de la auditoría de sesión 15 se validó con una
  cifra de impacto medida contra datos reales (no solo "los tests
  pasan") antes de darse por cerrado — incluyendo el hallazgo de esta
  misma revisión (sección 2), que no habría salido a la luz sin repetir
  la medición completa.
- **Manejo honesto de resultados que no mejoran el agregado.** El fix
  del múltiplo de peers (sección 16) y ahora el risk-free rate en vivo
  (sección 2 de este documento) tuvieron efecto mixto o incluso
  negativo en Big Tech — y se documentó así, en vez de revertir un
  cambio metodológicamente correcto para maquillar el número.
- **Casos límite reales resueltos, no solo teóricos:** ticker inválido
  (`ZZZZINVALID`), `interest_expense=0` espurio, `gordon_weight=0` que
  antes podía bloquear el DCF entero — todos encontrados y corregidos
  probando con datos/casos reales, no solo imaginando escenarios.

## 4. Qué hace mal / limitaciones que siguen abiertas

- **Brecha de precisión práctica grande y sin plan de cierre** (sección
  2). No es un defecto de ejecución, pero si el objetivo final incluye
  "validar frente a consenso con una desviación razonable" (blueprint,
  Fase 7), ese objetivo tal como está planteado no se puede cumplir sin
  cambiar la filosofía de proyección — y cambiarla sin criterio sería
  sobreajustar. Recomendación en la sección 5.
- **La capa generativa nunca se ha probado con la API real.** Todo lo
  que existe (`generate_memo()`, el prompt, el ensamblado del payload)
  está testeado con dobles de prueba, no con una llamada real a
  Claude. Es razonable no gastar cuota/dinero en cada sesión de
  desarrollo, pero **cero llamadas reales en toda la vida del proyecto**
  es un riesgo no verificado antes del despliegue (Fase 8): un error de
  formato del prompt, un límite de `max_tokens` insuficiente, o un
  fallo de la propia API nunca se ha visto en la práctica.
- **Fase 8 completamente sin empezar.** Sin repo remoto en GitHub
  siquiera (`git remote -v` no devuelve nada) — el proyecto vive solo
  en este equipo. Para un proyecto de portfolio, esto es más urgente
  que seguir puliendo hallazgos moderados: sin una URL pública, no hay
  nada que enseñar en una entrevista o en el CV.
- **Sin script de validación reutilizable.** La cifra de desviación
  agregada (sección 2) se ha tenido que reconstruir a mano con Python
  interactivo en cada sesión que la necesita — no hay un
  `scripts/validate_universe.py` versionado que la regenere de forma
  reproducible y la guarde con fecha. Esto es un riesgo de
  "reproducibilidad de las cifras del CV": si en una entrevista piden
  repetir la medición, hoy habría que reconstruirla desde cero otra vez.
- **Universo de comparables pequeño (I4, ya documentado) y Treasury
  Stock Method sin usar en el pipeline real (I2, ya documentado)** —
  limitaciones estructurales por disponibilidad de datos gratuitos, no
  bugs con arreglo evidente.
- **`gordon_weight=0.8` sigue siendo un valor heredado del Excel sin
  razonamiento propio para el caso general (M2)**, y **no hay
  verificación de divisa de reporte (M5)** — ambos documentados en
  `docs/AUDIT.md`, sin corregir, correctamente priorizados por debajo
  de lo demás.
- **Sin tests automatizados de `app/streamlit_app.py`** (N2, ya
  documentado y aceptado como práctica estándar para Streamlit) — la
  única red de seguridad es el smoke-test manual de cada sesión.

---

## 5. Cómo continuar — roadmap recomendado

En orden de impacto para un proyecto de portfolio, no de dificultad
técnica:

1. **Desplegar (Fase 8).** Crear el repo en GitHub (público, con el
   Excel ya incluido porque el autor permite su uso libre) y desplegar
   en Streamlit Community Cloud. Sin esto, nada de lo construido es
   demostrable con una URL — es el paso de mayor retorno por esfuerzo
   invertido ahora mismo.
2. **Probar la capa generativa con una llamada real** (aunque sea una
   sola vez, con una `ANTHROPIC_API_KEY` de prueba) antes de desplegar
   — verificar que el memo se genera correctamente end-to-end, no solo
   que el payload se ensambla bien.
3. **Hacer explícita en la interfaz la limitación de la sección 2.**
   Un usuario que abra la app y vea "-42% vs. mercado" sin contexto
   puede concluir que la herramienta está simplemente mal, en vez de
   entender que es una postura de modelado deliberada. Un texto corto
   y fijo en la UI (no generado por IA) explicando esto sería más
   honesto que dejar que el usuario lo infiera.
4. **Crear `scripts/validate_universe.py`** que reproduzca la
   medición de la sección 2 de forma reproducible y la guarde con
   fecha (p. ej. `data/validation_history/2026-09-06.json`) — para que
   la cifra de "desviación media" del CV se pueda regenerar y
   defender en cualquier momento, no reconstruir a mano.
5. **Decidir M2 como una decisión de producto, no dejarlo pendiente
   indefinidamente:** o se justifica `gordon_weight=0.8` como default
   razonado (p. ej. documentando por qué es razonable para el caso
   general, no solo heredado del Excel), o se cambia a un valor neutro
   (p. ej. 0.5) con su propia justificación. Cualquiera de las dos
   opciones cierra el hallazgo; dejarlo abierto indefinidamente no.
6. **Fase 9 (sentiment en earnings calls)** — extensión opcional, solo
   después de lo anterior. Añade superficie nueva a un proyecto que
   todavía no está desplegado ni con su capa generativa verificada en
   vivo; no es la prioridad correcta ahora mismo.

**Principio de fondo, sin cambios respecto a sesiones anteriores:**
seguir sin ajustar supuestos para acercar el precio implícito al de
mercado. La brecha de la sección 2 se comunica y se contextualiza, no
se maquilla cambiando `gordon_weight`, `terminal_growth_rate` o
`lookback_years` hasta que el número agregado mejore.
