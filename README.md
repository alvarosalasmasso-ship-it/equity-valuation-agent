# Agente de valoración DCF

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

**Disclaimer:** herramienta educativa / de portfolio, no constituye
asesoramiento de inversión regulado.
