# Observabilidad

## Objetivo

El sistema separa **trazas**, **métricas operativas** y **señales de calidad**. No se presenta una señal heurística como una evaluación humana definitiva.

## Phoenix OSS

El backend inicializa Phoenix al arrancar cuando `PHOENIX_ENABLED=true`. En Docker Compose se ejecuta el servicio `phoenix` con almacenamiento persistente en `phoenix-data`:

- UI: `http://localhost:6006`
- OTLP/gRPC: `phoenix:4317` dentro de Compose
- `PHOENIX_PROJECT_NAME`: nombre del proyecto de trazas
- `PHOENIX_COLLECTOR_ENDPOINT`: endpoint OTLP; vacío en `.env` usa Phoenix local
- `PHOENIX_API_KEY`: vacío para el Phoenix local; se completa si se cambia a un collector cloud

La integración usa OpenTelemetry y `openinference-instrumentation-langchain`. Se instrumentan las ejecuciones LangChain y se crea una traza de conversación `chat.answer`.

Por privacidad, `OPENINFERENCE_HIDE_INPUTS=true` y `OPENINFERENCE_HIDE_OUTPUTS=true`. La traza conserva metadatos técnicos y señales agregadas, no el prompt ni la respuesta completa.

> Phoenix es la opción local y open source. Arize AX Cloud no queda activado porque requiere un endpoint y una API key de la cuenta. Para migrar, se reemplazan esas variables; no se cambia el flujo del agente.

## Métricas del backend

El backend expone:

- `GET /metrics`: formato Prometheus.
- `GET /api/v1/metrics/summary`: resumen JSON para inspección y tests.
- `GET /api/v1/health`: estado de retrieval, Chroma, reranker, Gemini, LangSmith, Phoenix, SQLite y MCP.

Métricas implementadas:

| Área | Métricas |
|---|---|
| Tráfico | requests totales, errores, clases de estado, latencia y máximo |
| RAG/chat | requests, fallos, respuestas sin evidencia, errores de tools |
| Calidad | confianza, `groundedness_proxy` y `hallucination_risk_proxy` |
| Recursos | CPU acumulada y memoria residente del proceso cuando el sistema operativo lo permite |

`groundedness_proxy` combina confianza y score de la mejor fuente. `hallucination_risk_proxy` es `1 - groundedness_proxy`; sirve para detectar tendencias, no reemplaza un conjunto de evaluación anotado.

## Que observar en una demo

1. Levantar Compose y abrir `http://localhost:6006`.
2. Consultar el agente desde `http://localhost:8501`.
3. Abrir `http://localhost:8000/api/v1/metrics/summary` y verificar que aumenten requests, latencia y señales de calidad.
4. Buscar en Phoenix el proyecto `british-museum-agent` y la traza `chat.answer`.
5. Consultar una pregunta sin evidencia y verificar `no_evidence_total` y la respuesta segura.

## Limitaciones declaradas

- No se incluye un servidor Prometheus/Grafana: el endpoint queda listo para conectarlo.
- No se incluye Arize AX Cloud sin credenciales reales.
- La calidad no es una verdad de terreno: para medirla formalmente falta un dataset de preguntas/respuestas esperadas y evaluación humana o automática.
- El collector local puede estar inactivo aunque el agente siga respondiendo; `/api/v1/health` informa `phoenix.active` sin ocultar esa degradación.