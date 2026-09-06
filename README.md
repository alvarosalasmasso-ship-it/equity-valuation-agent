# Agente de valoración DCF

**App en vivo:** [equity-valuation-agent.streamlit.app](https://equity-valuation-agent.streamlit.app/)

Agente de valoración de equities: extrae estados financieros
automáticamente, ejecuta un motor de DCF determinista (traducido de un
modelo profesional real de banca de inversión) y usa un LLM únicamente
para redactar el Investment Memo a partir de las cifras ya calculadas.

Ver [`blueprint_agente_valoracion_dcf.md`](blueprint_agente_valoracion_dcf.md)
para el diseño completo y [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)
para la metodología financiera y su trazabilidad contra el modelo de
referencia.

Progreso y contexto de trabajo: ver [`estado.md`](estado.md).

## Estructura

```
engine/       # Motor de valoración determinista (sin IA): DCF, WACC, ratios, comps
ai/           # Capa generativa: llamadas al LLM para redactar el memo
app/          # Interfaz Streamlit
tests/        # Tests del motor de valoración
docs/         # Metodología y documentación
data/         # Cache de datos crudos y procesados (no versionado)
```

## Setup

```
pip install -r requirements.txt
pytest tests/ -v
```

## Ejecutar la app

```
streamlit run app/streamlit_app.py
```

Funciona sin `ANTHROPIC_API_KEY` (muestra el prompt del Investment Memo
para copiar y pegar manualmente en Claude.ai en vez de generarlo en
vivo). Requiere `ALPHA_VANTAGE_API_KEY` en `.env` solo para el grupo de
comparables "Big Tech / Cloud"; el grupo "Consumo defensivo" y cualquier
ticker arbitrario usan `yfinance`, sin API key.

**Disclaimer:** herramienta educativa / de portfolio, no constituye
asesoramiento de inversión regulado.
