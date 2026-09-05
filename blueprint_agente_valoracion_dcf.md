# Blueprint — Agente de valoración DCF con IA
### Proyecto de portfolio para procesos de selección en Gestión de Activos / Mercados de Capitales

---

## 0. Decisión de alcance

**Idea elegida: #1 (Agente de valoración y análisis de estados financieros)**, con la sentiment-analysis de earnings calls (idea #3) como extensión de Fase 2.

**Idea #2 (stress testing de cartera) descartada para este proyecto** por dos motivos:
- Requiere matrices de correlación/covarianza entre activos con series históricas largas y limpias — más difícil de justificar con rigor sin acceso a datos institucionales.
- Encaja mejor con el perfil de Gestión de Patrimonios (tu interés #2), mientras que la idea #1 encaja directamente con Gestión de Activos y Mercados de Capitales (tu interés #1). Prioriza la que más pondera tu objetivo declarado.

**Nota de continuidad:** ya tienes un proyecto pausado con el mismo espíritu (tracker de tesis de inversión, multi-tesis, con Supabase/auth). Se pausó por complejidad. Este proyecto es deliberadamente más pequeño: **una sola pasada analítica por empresa, sin persistencia multiusuario, sin auth.** Si en el futuro retomas el tracker, esta pieza se convierte en su motor de valoración ya validado — no se tira el trabajo.

---

## 1. Arquitectura y stack tecnológico

| Capa | Herramienta | Por qué |
|---|---|---|
| Datos fundamentales | **Alpha Vantage** (ya conectado en tu cuenta) — `INCOME_STATEMENT`, `BALANCE_SHEET`, `CASH_FLOW`, `EARNINGS`, `COMPANY_OVERVIEW` | Estados financieros ya normalizados. Evita construir un parser de PDFs 10-K en el MVP — ahorro de semanas. |
| Datos de mercado | **yfinance** | Precios, betas, capitalización — gratuito y sin límite práctico de peticiones (Alpha Vantage free tier limita a 25/día). |
| Motor de cálculo | **Python puro** (pandas, numpy) | Sin dependencias de IA. Cada fórmula es código auditable y testeable. |
| Capa generativa | **API de Anthropic directa** (sin framework) | Para un MVP con 3-4 llamadas bien definidas, LangChain/LlamaIndex añaden una capa de abstracción que no aporta valor y complica la explicación en entrevista. |
| RAG sobre 10-Ks (Fase 2) | **LlamaIndex** | Si más adelante añades extracción cualitativa de riesgos/MD&A desde PDFs reales, LlamaIndex está especializado en indexación/retrieval; LangChain es orquestación general, más pesado para este caso. |
| Interfaz | **Streamlit** | Estándar de facto en la industria quant/fintech para demos analíticas. Sin necesidad de auth ni base de datos — Claude Code lo genera en una sesión. |
| Despliegue | **Streamlit Community Cloud** | Gratuito, un clic desde GitHub, URL pública para el CV. |

**Principio de arquitectura (el argumento de rigor para la entrevista):**
El motor de valoración (Python determinista) y la capa generativa (LLM) están **desacoplados**. El LLM nunca calcula cifras — solo redacta el memo a partir de outputs numéricos ya generados por código. Esto elimina el riesgo de alucinación numérica y es exactamente el punto que un entrevistador de Mercados de Capitales va a presionar. Tenerlo resuelto de diseño, no como parche, es la diferencia entre un proyecto "con IA" y un proyecto que demuestra criterio financiero.

---

## 2. Metodología financiera (motor determinista)

Usa **tu propia plantilla DCF** como fuente de verdad — el objetivo es traducirla a funciones Python exactas, no dejar que la IA reinvente el modelo.

### DCF (Discounted Cash Flow)

```
FCFF = EBIT × (1 − t) + D&A − CapEx − ΔNWC

Re (CAPM) = Rf + β × (Rm − Rf)
WACC = (E / (D+E)) × Re + (D / (D+E)) × Rd × (1 − t)

Valor terminal (Gordon Growth): TVₙ = FCFFₙ × (1+g) / (WACC − g)

Enterprise Value = Σ [FCFFₜ / (1+WACC)ᵗ]  para t=1..n   +   TVₙ / (1+WACC)ⁿ

Equity Value = Enterprise Value − Deuda Neta (Deuda total − Caja y equivalentes)

Precio objetivo por acción = Equity Value / Acciones diluidas en circulación
```

### Ratios y comparables

| Categoría | Métrica | Fórmula |
|---|---|---|
| Rentabilidad | ROE (Dupont) | (Beneficio neto / Ventas) × (Ventas / Activos) × (Activos / Equity) |
| Creación de valor | ROIC vs WACC | ROIC = NOPAT / Capital invertido → crea valor si ROIC > WACC |
| Apalancamiento | Debt/EBITDA, Interest Coverage | Deuda total / EBITDA ; EBIT / Gastos financieros |
| Liquidez | Current Ratio | Activo corriente / Pasivo corriente |
| Múltiplos (comps) | EV/EBITDA, EV/Sales, P/E, P/B | Frente a un set de comparables del mismo sector |

### Output visual clave: matriz de sensibilidad

Tabla 2D con **WACC (filas) × crecimiento terminal g (columnas)**, mostrando el precio objetivo resultante en cada celda. Es el gráfico que más valoran en entrevistas de equity research porque demuestra que entiendes la fragilidad de los supuestos del modelo, no solo el resultado puntual.

---

## 3. Plan de ejecución (MVP → despliegue)

| Fase | Entregable | Notas |
|---|---|---|
| 0 — Alcance | Universo piloto: 3-5 tickers de un mismo sector | Facilita comparar comps y validar contra consenso real |
| 1 — Motor de datos | Script que llama Alpha Vantage + yfinance y normaliza en un DataFrame limpio | Cachear localmente para no agotar el rate limit gratuito |
| 2 — Motor de valoración | Traducir tu plantilla DCF a funciones Python (`valuation.py`) | Pide a Claude Code que replique exactamente tus fórmulas, no que "haga un DCF genérico" |
| 3 — Motor de ratios y comps | `ratios.py`, `comps.py` | Tabla de comparables con peers del mismo sector |
| 4 — Tests unitarios | `test_valuation.py` validando el motor contra un caso conocido a mano | Argumento de rigor de ingeniería para el repo |
| 5 — Capa generativa | Prompt estructurado que recibe SOLO los outputs numéricos ya calculados y devuelve el Investment Memo (Executive Summary, Tesis, Valoración, Riesgos, Recomendación) | Nunca pases datos crudos al LLM para que "calcule" — solo para que redacte |
| 6 — Interfaz | App Streamlit: input de ticker, outputs numéricos, memo, gráfico de sensibilidad | Claude Code genera la UI en una sesión sobre el motor ya testeado |
| 7 — Validación | Contrastar valoraciones de 3-5 empresas frente a consenso de analistas público | Necesario para poder hablar de precisión real en el CV, no una cifra inventada |
| 8 — Despliegue | Streamlit Community Cloud + repo público en GitHub | |
| 9 (extensión) — Sentiment en earnings calls | Módulo que usa `EARNINGS_CALL_TRANSCRIPT` de Alpha Vantage (ya trae señales de sentimiento) para detectar cambios de tono trimestre a trimestre | Combina idea #1 e idea #3 sin duplicar infraestructura |

---

## 4. Redacción para CV y GitHub

**Regla de honestidad:** los corchetes `[ ]` de las viñetas son placeholders. Rellénalos solo con cifras que midas de verdad en la Fase 7 (validación). No inventes porcentajes de precisión ni tiempos ahorrados — un entrevistador técnico de finanzas puede pedirte que expliques cualquier cifra del CV.

### Viñetas de CV

1. Diseñé y desarrollé un agente de valoración de equities en Python que automatiza la extracción de estados financieros (Alpha Vantage API) y la construcción de modelos DCF con capa generativa (API de Anthropic) para redactar Investment Memos estructurados.
2. Implementé un motor de valoración determinista (CAPM, WACC, FCFF, Gordon Growth Model) desacoplado de la capa de IA generativa, garantizando trazabilidad total de cifras — validado frente a [N] empresas del sector [X] con una desviación media de [Y%] frente al consenso de mercado.
3. Desplegué la herramienta como aplicación interactiva de código abierto (Streamlit), documentada con metodología financiera explícita y cubierta por tests unitarios sobre el motor de cálculo.

### Estructura de repositorio

```
/equity-valuation-agent
├── data/                  # cache de datos crudos y procesados
├── engine/
│   ├── valuation.py       # DCF: FCFF, WACC, CAPM, Gordon Growth
│   ├── ratios.py
│   └── comps.py
├── ai/
│   ├── memo_generator.py  # llamadas a la API de Anthropic
│   └── prompts/           # prompts versionados
├── app/
│   └── streamlit_app.py
├── tests/
│   └── test_valuation.py  # validación del motor contra casos conocidos
├── docs/
│   └── METHODOLOGY.md     # fórmulas, supuestos y fuentes documentados
├── README.md               # incluye disclaimer: herramienta educativa, no asesoramiento regulado
└── requirements.txt
```

---

## Próximo paso inmediato

Empieza por la Fase 1 (motor de datos) con un único ticker piloto. No toques Streamlit ni el LLM hasta que el motor de valoración (Fase 2-4) esté calculando cifras correctas y testeadas — construir la interfaz antes de validar el cálculo es el error que probablemente hizo pesado el proyecto anterior.
