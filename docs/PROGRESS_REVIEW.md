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
| 8 — Despliegue | GitHub + Streamlit Cloud | ✅ **Completo** (sesión 16, continuación) — repo público en [GitHub](https://github.com/alvarosalasmasso-ship-it/equity-valuation-agent), desplegado y verificado en vivo en [equity-valuation-agent.streamlit.app](https://equity-valuation-agent.streamlit.app/) |
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
- ~~**Fase 8 completamente sin empezar.**~~ ✅ Resuelto (sesión 16,
  continuación): repo público en GitHub y app desplegada y verificada en
  vivo en Streamlit Community Cloud. Ver sección 7.
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

1. ~~**Hacer explícita en la interfaz la limitación de la sección 2.**~~
   ✅ **Hecho, y con más rigor del que este roadmap pedía** (sesión 16,
   continuación) — en vez de solo un texto explicativo fijo, se
   construyó un **reverse DCF** completo (`engine/reverse_dcf.py`):
   dado el precio de mercado o el consenso, resuelve qué crecimiento de
   ingresos y qué tasa de crecimiento terminal tendrían que cumplirse
   para justificarlo, y lo compara contra lo que asume el escenario
   conservador. Ver `docs/METHODOLOGY.md` sección 20 — la desviación
   deja de ser un porcentaje sin explicación y pasa a ser una prima de
   crecimiento cuantificada (AMZN: el mercado paga hoy un 31.8% de
   crecimiento de ingresos implícito frente al 11.7% asumido). Integrado
   en la app (nueva sección) y en el Investment Memo (nuevo campo en
   `MemoInput`, nueva sección del prompt de sistema). Este fue el
   resultado directo de pararse a responder "¿para qué sirve un DCF de
   verdad, y qué información aporta?" antes de seguir desarrollando —
   ver el intercambio que lo motivó, resumido en la sección 6.
2. ~~**Desplegar (Fase 8).**~~ ✅ **Hecho** (sesión 16, continuación) —
   repo público en [GitHub](https://github.com/alvarosalasmasso-ship-it/equity-valuation-agent),
   desplegado y verificado en vivo (captura de pantalla real) en
   [equity-valuation-agent.streamlit.app](https://equity-valuation-agent.streamlit.app/).
   Ver sección 7 para el detalle del proceso (incluyó depurar un problema
   real de permisos de la GitHub App de Streamlit sobre el repo).
3. **Probar la capa generativa con una llamada real** (aunque sea una
   sola vez, con una `ANTHROPIC_API_KEY` de prueba) — verificar que el
   memo (ahora con la sección de expectativas implícitas) se genera
   correctamente end-to-end, no solo que el payload se ensambla bien.
   Puede probarse directamente en la app ya desplegada.
4. ~~**Crear `scripts/validate_universe.py`**~~ ✅ **Hecho** (sesión 16,
   continuación) — corre `value_ticker()` sobre ambos universos piloto
   con el risk-free rate en vivo, aislando fallos por ticker, y guarda
   un snapshot fechado en `data/validation_history/<fecha>.json`
   (versionado en el repo, a diferencia de `data/cache/`). Incluye las
   expectativas implícitas del reverse DCF de cada ticker, no solo la
   desviación. Verificado con datos reales: reproduce exactamente el
   41.82% de desviación media combinada medido a mano en la sección 2.
5. **Decidir M2 como una decisión de producto, no dejarlo pendiente
   indefinidamente:** o se justifica `gordon_weight=0.8` como default
   razonado (p. ej. documentando por qué es razonable para el caso
   general, no solo heredado del Excel), o se cambia a un valor neutro
   (p. ej. 0.5) con su propia justificación. Cualquiera de las dos
   opciones cierra el hallazgo; dejarlo abierto indefinidamente no.
6. **Fase 9 (sentiment en earnings calls)** — extensión opcional, solo
   después de lo anterior.

**Principio de fondo, sin cambios respecto a sesiones anteriores:**
seguir sin ajustar supuestos para acercar el precio implícito al de
mercado. La brecha de la sección 2 se comunica y se contextualiza, no
se maquilla cambiando `gordon_weight`, `terminal_growth_rate` o
`lookback_years` hasta que el número agregado mejore.

---

## 6. Addendum — el reverse DCF (sesión 16, continuación)

Tras publicar este documento, el usuario hizo la pregunta que debería
haber enmarcado la sección 2 desde el principio: **¿para qué se usa un
DCF de verdad, y qué información aporta?** Un DCF hacia delante no está
diseñado para predecir el precio de mercado — esa es la lectura que
esta misma revisión (sección 2) había estado usando implícitamente al
hablar de "desviación" como si fuera un error. Los usos reales de un
DCF profesional incluyen, además de la valoración intrínseca: hacer
explícitas las hipótesis de una tesis, descomponer de dónde viene una
diferencia de precio, y —el que faltaba aquí— **calcular expectativas
implícitas (reverse DCF)**: dado el precio que ya cotiza el mercado,
¿qué tendría que ser cierto para justificarlo?

Comparando esto contra lo que la herramienta aportaba en ese momento
(escenarios, sensibilidad WACC×g, ratios, comparables — pero ningún
reverse DCF), quedó claro que la pieza que faltaba no era "más
precisión" sino la función que convierte la brecha ya medida en
información aprovechable. Se construyó en la misma sesión: ver
`docs/METHODOLOGY.md` sección 20 para el diseño técnico completo
(bisección pura sobre `run_dcf()`, sin segunda metodología) y la
verificación con datos reales. El roadmap de la sección 5 se actualizó
para reflejar esto como hecho — item 1, no ya pendiente.

---

## 7. Addendum — Despliegue (Fase 8, sesión 16, continuación)

Sin `gh` CLI instalado en el entorno, así que primero se instaló
(`winget install GitHub.cli`) y se autenticó vía flujo de device-code
en el navegador del usuario. Con `gh` autenticado:

1. **Repo creado y código subido:** `gh repo create equity-valuation-agent
   --private --source=. --remote=origin --push` (privado en primera
   instancia, por elección explícita del usuario). `gh auth setup-git`
   fue necesario para que el `git push` en sí usara las credenciales de
   `gh` en vez de fallar por falta de terminal interactiva para el
   prompt de credenciales de Windows.
2. **Verificación de higiene antes y después de cualquier cambio de
   visibilidad:** `git log --all --diff-filter=A --name-only` sobre
   TODO el historial (no solo el HEAD actual) para confirmar que nunca
   se commiteó `.env`, ninguna clave ni credencial — limpio.
3. **Bloqueo real encontrado, no anticipado:** Streamlit Community
   Cloud no encontraba el repo al desplegar. Diagnóstico por descarte
   guiado (cuenta de GitHub correcta confirmada, la GitHub App de
   Streamlit sí estaba instalada, pero su página de "Configure" no
   ofrecía selector de repos, solo revocar acceso) — consistente con
   que el repo, al ser privado, no estaba en la lista de acceso de la
   instalación y esa cuenta/app en concreto no exponía la UI esperada
   para añadirlo. Se resolvió cambiando el repo a **público**
   (`gh repo edit --visibility public --accept-visibility-change-consequences`,
   tras repetir la comprobación de higiene del historial completo) en
   vez de seguir depurando permisos de la GitHub App — más rápido y
   igual de correcto para un proyecto de portfolio que de todas formas
   debía ser público a medio plazo (ver pregunta 2 del blueprint).
4. **Desplegado y verificado en vivo:** el usuario completó el
   formulario de deploy en share.streamlit.io (repo, rama `master`,
   `app/streamlit_app.py`, secret `ALPHA_VANTAGE_API_KEY` vía TOML).
   Verificado con una captura de pantalla real (Playwright, sin sesión
   ni cookies) contra `https://equity-valuation-agent.streamlit.app/`:
   la app carga completa, con los mismos datos y cifras que la versión
   local (AMZN, WACC 9.10%, risk-free rate en vivo, escenarios) — no
   solo un "no ha dado error", una verificación visual real de que el
   contenido es correcto.

**Fase 8 completa.** README.md actualizado con el enlace a la app en
vivo. Repositorio: https://github.com/alvarosalasmasso-ship-it/equity-valuation-agent
(público). App: https://equity-valuation-agent.streamlit.app/
