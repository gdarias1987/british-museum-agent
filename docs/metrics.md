# Observabilidad — British Museum Agent

## Tabla de contenidos

1. [Métricas clave](#1-métricas-clave)
2. [Endpoints de métricas](#2-endpoints-de-métricas)
3. [Trazabilidad distribuida](#3-trazabilidad-distribuida)
4. [Health check](#4-health-check)
5. [Métricas personalizadas — ServiceMetrics](#5-métricas-personalizadas--servicemetrics)
6. [Dashboard / visualización sugerida](#6-dashboard--visualización-sugerida)

---

## 1. Métricas clave

El sistema expone un conjunto de métricas operativas agrupadas en tres dominios: HTTP, chat (respuestas del agente) y proceso (uso de recursos).

### 1.1 Latencia

- **Métrica Prometheus**: `british_museum_http_request_duration_seconds` (histograma)
- **Buckets**: 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0 (segundos)
- **Etiquetas**: `method`, `route`
- **Endpoint JSON**: `latency_seconds` dentro de `http` incluye `count`, `sum`, `average`, `max`

Se registra la latencia de cada request HTTP entrante (incluyendo los requests a `/api/v1/chat`). El resumen consolidado se expone tanto en el formato Prometheus como en el summary JSON.

### 1.2 Groundedness proxy

- **Métrica Prometheus**: `british_museum_chat_groundedness_proxy_ratio` (histograma)
- **Buckets**: 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0
- **Definición**: estimación de cuán respaldada está una respuesta por las fuentes recuperadas. Se calcula como `(confidence + max(source_scores)) / 2`. El valor es un número entre 0 y 1.

Si no hay fuentes (sources vacío) este proxy no se actualiza. Las respuestas sin evidencia se contabilizan por separado con la métrica `british_museum_chat_no_evidence_total`.

### 1.3 Hallucination risk proxy

- **Métrica Prometheus**: `british_museum_chat_hallucination_risk_proxy_ratio` (histograma)
- **Buckets**: idénticos a groundedness (0.1–1.0)
- **Definición**: `1.0 - groundedness_proxy`. Un valor alto indica mayor riesgo de que la respuesta no esté respaldada por las fuentes.

### 1.4 Confidence

- **Métrica Prometheus**: `british_museum_chat_confidence_ratio` (histograma)
- **Buckets**: 0.1–1.0
- **Origen**: proviene directamente del campo `confidence` en la respuesta del LLM.

### 1.5 Tool errors

- **Métrica Prometheus**: `british_museum_chat_tool_errors_total` (contador)
- **Etiquetas**: `tool` (nombre de la herramienta normalizado)
- **Definición**: cada llamada a herramienta cuyo `status` no sea `"success"` incrementa este contador para la herramienta correspondiente.

Las herramientas disponibles del agente se definen en el módulo MCP. Los nombres se normalizan (solo caracteres `[a-zA-Z0-9_.:-]`, truncados a 64 caracteres) para garantizar compatibilidad con Prometheus.

### 1.6 Uso de recursos

- **Métrica Prometheus**: `process_cpu_seconds_total` (contador) — tiempo de CPU acumulado (user + system) vía `process_time()`.
- **Métrica Prometheus**: `process_resident_memory_bytes` (gauge) — memoria residente en bytes, obtenida desde `/proc/self/statm` en Linux o `getrusage()` en macOS. Puede ser `null` si no se pudo determinar.

---

## 2. Endpoints de métricas

Endpoints expuestos por FastAPI.

### 2.1 `GET /metrics`

- **Propósito**: endpoint Prometheus compatible (text/plain; version=0.0.4).
- **Oculto del schema OpenAPI** (`include_in_schema=False`).
- **Formato**: salida de `ServiceMetrics.render_prometheus()`. Incluye HELP, TYPE y los valores actuales de todos los histogramas, contadores y gauges.
- **Uso típico**: configurar como target de scraping en Prometheus o consumir con `curl` / `wget`.

```bash
curl http://localhost:8000/metrics
```

### 2.2 `GET /api/v1/metrics/summary`

- **Propósito**: endpoint JSON estructurado para consumo programático.
- **Formato**: diccionario anidado con las secciones `http`, `chat` y `process`.

```json
{
  "http": {
    "requests_total": 150,
    "errors_total": 3,
    "latency_seconds": {
      "count": 150,
      "sum": 45.2,
      "average": 0.301,
      "max": 3.1
    },
    "status_classes": {
      "2xx": 147,
      "4xx": 2,
      "5xx": 1
    }
  },
  "chat": {
    "requests_total": 42,
    "failures_total": 0,
    "no_evidence_total": 2,
    "tool_errors_total": 5,
    "confidence": { "count": 42, "sum": 35.7, ... },
    "groundedness_proxy": { ... },
    "hallucination_risk_proxy": { ... }
  },
  "process": {
    "cpu_seconds_total": 12.34,
    "resident_memory_bytes": 157286400
  }
}
```

---

## 3. Trazabilidad distribuida

El sistema soporta dos providers de tracing que operan de forma independiente:

### 3.1 LangSmith

- **Configuración**: variables de entorno `LANGSMITH_API_KEY` y `LANGCHAIN_TRACING`, más un project name configurable desde `Settings`.
- **Activación**: se pasa `tracing_enabled=True` y el `langsmith_project` al `ChatService` y al agente (`StateGraph`).
- **Visualización**: los traces se envían a la plataforma [LangSmith](https://smith.langchain.com) para inspección de ejecuciones del grafo LangGraph, tiempos por nodo, inputs/outputs y errores.

### 3.2 Arize Phoenix (OTLP + OpenInference)

- **Configuración**: bloque `[phoenix]` en las opciones del sistema.
- **Mecanismo**: configura un `TracerProvider` de OpenTelemetry con un `OTLPSpanExporter` que apunta a un collector de Arize Phoenix. Instrumenta automáticamente LangChain mediante `LangChainInstrumentor` de OpenInference.
- **Atributos de span**: cada span `chat.answer` registra:
  - `chat.trace_id`
  - `chat.confidence`
  - `chat.sources.count`
  - `chat.no_evidence`
  - `chat.tool_errors.count`
  - `chat.groundedness_proxy`
  - `chat.hallucination_risk_proxy`
  - `error.type` (en caso de excepción)
- **Seguridad**: por defecto `hide_inputs=True`, `hide_outputs=True` para no capturar contenido de prompts ni respuestas.
- **Estado**: se puede consultar en tiempo real vía `get_phoenix_status()` (usado en el health check).

### 3.3 Comparativa

| Provider   | Transporte         | Instrumentación        | Visibilidad                  |
|------------|--------------------|------------------------|------------------------------|
| LangSmith  | API REST (HTTP)    | Callbacks de LangChain | Trazas completas del grafo   |
| Arize Phoenix | OTLP gRPC      | OpenInference          | Trazas OTel estándar + métricas personalizadas |

Ambos pueden habilitarse simultáneamente, ya que operan en capas distintas.

---

## 4. Health check

### 4.1 `GET /api/v1/health`

Endpoint que devuelve el estado de cada componente del sistema. Responde `200 OK` si todos los componentes están listos, o `503 Service Unavailable` si alguno falla.

```json
{
  "status": "ok",
  "app": "british-museum-agent",
  "environment": "production",
  "components": {
    "retrieval": {
      "ready": true,
      "required_backend": "chroma",
      "active_backend": "chroma",
      "detail": "..."
    },
    "chroma": {
      "configured": true,
      "index_ready": true,
      "path": "/data/chroma",
      "detail": "..."
    },
    "reranker": {
      "active": true,
      "mode": "cross-encoder",
      "detail": "..."
    },
    "lexical_fallback": {
      "index_ready": true
    },
    "gemini": {
      "configured": true,
      "model": "gemini-2.0-flash"
    },
    "langsmith": {
      "configured": true,
      "project": "british-museum-agent"
    },
    "phoenix": {
      "configured": true,
      "active": true,
      "project": "british-museum-agent",
      "detail": "Phoenix OTLP/OpenInference tracing is active..."
    },
    "sqlite": {
      "ready": true
    },
    "mcp": {
      "ready": true,
      "detail": "El servicio MCP respondió correctamente."
    }
  }
}
```

**Componentes verificados**:

| Componente      | ¿Qué valida?                                           |
|-----------------|--------------------------------------------------------|
| `retrieval`     | Que el motor de recuperación esté activo y el backend coincida con el configurado (chroma, reranker activo). |
| `chroma`        | Si `retrieval_backend=chroma`, verifica que el índice de Chroma esté listo. |
| `reranker`      | Que el reranker (cross-encoder) esté activo.          |
| `lexical_fallback` | Que exista el archivo de índice léxico (`index_path`). |
| `gemini`        | Que el proveedor LLM esté correctamente configurado.   |
| `langsmith`     | Que LangSmith esté habilitado (si corresponde).        |
| `phoenix`       | Que Phoenix OTLP esté configurado y activo.            |
| `sqlite`        | Que la base de datos SQLite esté accesible y lista.    |
| `mcp`           | Que el servidor MCP responda a su propio health check. |

### 4.2 Health check interno (MCP)

El adaptador MCP expone su propio endpoint `GET /health` (custom route de MCP) que el health check principal consume para determinar la disponibilidad del servicio de herramientas.

---

## 5. Métricas personalizadas — ServiceMetrics

`ServiceMetrics` es un registro en proceso thread-safe que implementa un subconjunto del modelo de datos de Prometheus:

### 5.1 _Distribution

Clase interna que implementa un histograma acumulativo con buckets configurables. Operaciones:

- `observe(value)`: registra una observación, actualiza contadores, total, máximo y bucket correspondiente.
- `as_summary()`: retorna `count`, `sum`, `average`, `max`.

### 5.2 ServiceMetrics — API pública

| Método                            | Efecto                                              |
|-----------------------------------|-----------------------------------------------------|
| `record_http_request(...)`        | Registra método, ruta, status code y latencia.      |
| `record_chat_started()`           | Incrementa el contador de requests de chat.         |
| `record_chat_failure()`           | Incrementa el contador de fallos de chat.           |
| `record_chat_response(response)`  | Procesa una respuesta: confidence, tool errors, calidad. |
| `summary()`                       | Retorna diccionario JSON con todas las métricas.    |
| `render_prometheus()`             | Retorna string en formato Prometheus text/plain.    |

### 5.3 Histogramas

El sistema usa dos familias de buckets:

- **Latencia HTTP**: 11 buckets desde 5 ms hasta 10 s. Cubre desde requests rápidos (health check, estáticos) hasta llamadas costosas (chat con LLM).
- **Ratios (confidence, groundedness, hallucination risk)**: 10 buckets lineales de 0.1 a 1.0. Permiten observar distribuciones de calidad de respuesta.

### 5.4 Contadores

- `british_museum_http_requests_total`: etiquetado por método, ruta y status code.
- `british_museum_http_request_errors_total`: mismo esquema de etiquetas, solo status >= 400.
- `british_museum_chat_requests_total`: contador global.
- `british_museum_chat_failures_total`: respuestas que lanzaron excepción antes de completarse.
- `british_museum_chat_no_evidence_total`: respuestas sin fuentes recuperadas.
- `british_museum_chat_tool_errors_total`: etiquetado por herramienta.

### 5.5 Gauges

- `process_resident_memory_bytes`: memoria residente del proceso (solo disponible en Linux/macOS).

### 5.6 Thread safety

Todas las operaciones sobre el registro usan un `Lock` (`threading.Lock`) para garantizar consistencia bajo concurrencia. Tanto `render_prometheus()` como `summary()` toman una snapshot bajo el lock antes de liberarlo.

### 5.7 Registry singleton

```python
from british_museum_agent.observability.metrics import get_service_metrics

metrics = get_service_metrics()         # singleton LRU-cacheado
metrics.record_chat_started()
```

`reset_service_metrics()` reemplaza el registro — útil para tests deterministas.

---

## 6. Dashboard / visualización sugerida

### 6.1 Prometheus + Grafana

La exposición en formato Prometheus permite integrar con cualquier stack de monitoreo. Paneles sugeridos:

#### Panel: Latencia de API

- **Métrica**: `british_museum_http_request_duration_seconds`
- **Gráfico**: percentiles p50, p95, p99 por ruta. Heatmap.
- **Alerta**: p95 > 5 s sostenido por 5 minutos.

#### Panel: Tasa de errores HTTP

- **Métrica**: `rate(british_museum_http_request_errors_total[5m]) / rate(british_museum_http_requests_total[5m])`
- **Gráfico**: ratio por status class y ruta.
- **Alerta**: error rate > 5 % sostenido.

#### Panel: Calidad de respuestas

- **Métricas**:
  - `british_museum_chat_confidence_ratio_bucket` (p50, p95)
  - `british_museum_chat_groundedness_proxy_ratio_bucket`
  - `british_museum_chat_hallucination_risk_proxy_ratio_bucket`
- **Gráfico**: distribuciones superpuestas.
- **Alerta**: groundedness proxy promedio < 0.5 en la última hora.

#### Panel: Uso de herramientas

- **Métrica**: `rate(british_museum_chat_tool_errors_total[5m])`
- **Gráfico**: stacked bar por herramienta.
- **Alerta**: cualquier herramienta con > 10 errores/minuto.

#### Panel: Recursos del proceso

- **Métricas**:
  - `process_cpu_seconds_total` (tasa por seg)
  - `process_resident_memory_bytes`
- **Gráfico**: uso de CPU y memoria en el tiempo.

### 6.2 Phoenix + Grafana (OTLP)

Si se usa Arize Phoenix, los spans de OpenTelemetry pueden consultarse directamente desde su UI o integrarse con Grafana Tempo para trazabilidad distribuida.

### 6.3 LangSmith

LangSmith ofrece dashboards nativos para trazas de LangChain/LangGraph, incluyendo:
- Árbol de ejecución por sesión de chat.
- Latencia por nodo del grafo.
- Frecuencia de errores por herramienta.

Combinar Prometheus (métricas agregadas) con LangSmith (trazas individuales) y Phoenix (OTLP) da una cobertura de observabilidad completa: qué pasa, cuándo pasa y por qué pasa.

---

## Apéndice: variables de entorno relevantes

| Variable                         | Efecto                                         |
|----------------------------------|------------------------------------------------|
| `LANGSMITH_API_KEY`              | Habilita tracing en LangSmith.                 |
| `LANGCHAIN_TRACING`              | Flag booleano para activar tracing.            |
| `PHOENIX_API_KEY`                | API key para Arize Phoenix collector.          |
| `PHOENIX_COLLECTOR_ENDPOINT`     | Endpoint OTLP gRPC del collector.              |
| `PHOENIX_ENABLED`                | Habilita/deshabilita tracing con Phoenix.      |
| `PHOENIX_PROJECT_NAME`           | Nombre del proyecto en Phoenix.                |
