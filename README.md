# British Museum Agent

> **Autor:** Guillermo Daniel Arias
> **Lugar y fecha:** Buenos Aires, Argentina — 26 de julio de 2026
> **Entrega final:** Unidad 1 · Sistema de IA en Producción

Asistente RAG en español para visitantes y personal del British Museum.

## Capturas del sistema

### Consulta RAG con respuesta fundamentada

![Respuesta del British Museum Agent sobre la Piedra de Rosetta](docs/images/ui-rag-answer.png)

La interfaz muestra la respuesta en español, confianza, fuentes recuperadas y trazabilidad de la ejecución.

### Observabilidad con Phoenix

![Trazas del proyecto British Museum Agent en Phoenix](docs/images/phoenix-traces.png)

Phoenix centraliza spans, latencia, tokens y costo estimado de las ejecuciones instrumentadas.

### Trazabilidad con LangSmith

![Trazas del British Museum Agent en LangSmith](docs/images/langsmith-traces.png)

LangSmith registra la ejecución completa de LangChain y LangGraph, con latencia, tokens, costo, tags y metadata del runtime.

![Detalle de una ejecución del British Museum Agent en LangSmith](docs/images/langsmith-trace-detail.png)

## Arquitectura ejecutable

```text
Streamlit -> FastAPI -> LangGraph
                         |-> ChromaDB -> embeddings locales -> cross-encoder
                         |-> Gemini
                         |-> MCP server -> SQLite
                         |-> LangSmith
```

Componentes:

- **Corpus:** Markdown UTF-8 en `data/raw/spanish`.
- **Recuperación:** ChromaDB persistente en `data/chroma`.
- **Embeddings:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
- **Reranking:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`.
- **Generación:** Gemini mediante `langchain-google-genai`.
- **Orquestación:** LangGraph.
- **Operaciones:** tools MCP respaldadas por SQLite.
- **Interfaz:** Streamlit.
- **Trazabilidad:** LangSmith con variables estándar.

La respuesta de `POST /api/v1/chat` incluye un bloque `runtime` que informa qué componentes estuvieron realmente activos. La readiness devuelve HTTP 503 con `status=degraded` si SQLite no tiene el esquema y seed mínimos, MCP no está disponible o el backend de retrieval requerido no está listo.

## Configuración

```powershell
copy .env.example .env
```

Completá sin publicar secretos:

```dotenv
GOOGLE_API_KEY=
# Alternativa si GOOGLE_API_KEY está vacía:
GEMINI_API_KEY=

LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=british-museum-agent

# Generá dos valores aleatorios, largos y distintos.
JWT_SECRET=
MCP_INTERNAL_TOKEN=
STAFF_DEMO_USERNAME=staff@example.com
STAFF_DEMO_PASSWORD=
JWT_EXPIRATION_MINUTES=60
```

`GOOGLE_API_KEY` tiene prioridad sobre `GEMINI_API_KEY`. Las claves se leen en runtime desde `.env`; no se copian al Dockerfile ni a la imagen. `JWT_SECRET` firma tokens staff con HS256. `MCP_INTERNAL_TOKEN` se envía como header de transporte `X-MCP-Internal-Token`; nunca forma parte de los argumentos de una tool. `STAFF_DEMO_PASSWORD` es obligatorio para el bootstrap, se guarda únicamente como hash bcrypt y no tiene valor público predeterminado.

## Ejecución local

Python 3.11 recomendado:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
$env:PYTHONPATH="src"
python scripts/seed_db.py
python scripts/ingest.py
python scripts/ingest_chroma.py
uvicorn british_museum_agent.api.main:app --reload
```

En otra terminal:

```powershell
.venv\Scripts\activate
$env:PYTHONPATH="src"
python -m british_museum_agent.adapters_mcp.server
```

Y en una tercera:

```powershell
.venv\Scripts\activate
streamlit run src/british_museum_agent/interfaces/streamlit/app.py
```

- UI: http://localhost:8501
- API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs
- Health API: http://localhost:8000/api/v1/health
- Health MCP: http://localhost:8001/health

Los modelos de embeddings y reranking se descargan si no están en caché y se calientan durante el startup de FastAPI. El primer chat no inicia esa carga. Si Chroma o el warmup fallan mientras `RETRIEVAL_BACKEND=chroma`, la API queda degradada y su health responde 503.

## Usar como personal

1. Abrí .env y ubicá STAFF_DEMO_USERNAME y STAFF_DEMO_PASSWORD.
2. Arrancá los servicios y abrí Streamlit.
3. Elegí **Personal**, copiá ambas variables en el formulario e iniciá sesión.
4. Usá el panel **Registrar incidente** para enviar un incidente autenticado.
## Docker Compose

```powershell
copy .env.example .env
docker compose up --build
```

Compose levanta:

- `ui`, que recibe únicamente `STREAMLIT_BACKEND_URL`.
- `backend`, que puede leer `.env`.
- `mcp-server`, que recibe únicamente variables SQLite/MCP y credenciales de bootstrap staff.
- `phoenix`, que recibe trazas OTLP/OpenInference y persiste su base local en el volumen `phoenix-data`.

El backend espera a que el healthcheck MCP confirme SQLite y autenticación interna. Luego ejecuta las ingestas. `scripts/ingest_chroma.py` compara el fingerprint del corpus y del modelo; si no hubo cambios, abre el índice existente sin regenerar embeddings.

Persistencia:

- `./data/chroma:/app/data/chroma`
- `./data/sqlite:/app/data/sqlite`
- volumen `huggingface-cache` para los modelos
- PyTorch CPU en la imagen backend; no descarga CUDA

## Observabilidad

El backend expone `GET /metrics` en formato Prometheus y `GET /api/v1/metrics/summary` en JSON. Phoenix OSS corre en `http://localhost:6006` y recibe trazas OTLP por `4317`; los inputs y outputs se ocultan por defecto. LangSmith y Phoenix están habilitados y verificados en el despliegue Kubernetes. LangSmith recibe las trazas de LangChain/LangGraph y Phoenix conserva la instrumentación OTLP/OpenInference local.

Ver detalle en `docs/observability.md`.

## Kubernetes y despliegue

La ruta preparada es Kubernetes con Kustomize:

- `deploy/base` y `deploy/overlays/dev`: Deployments, Services, PVC, probes, NetworkPolicy y HPA.
- `scripts/validate_k8s.py`: 177 checks estáticos; la validación completa del overlay ejecuta 179 checks.
- `scripts/deploy_k8s.ps1`: validate, render, dry-run, apply, status y rollback.
- `scripts/manage_k8s.ps1`: ciclo operativo completo `start`, `stop` y `status`, incluida la carga segura de `.env`, images, rollout y port-forwards.
- `docs/deployment.md`: persistencia, Secret, metrics server, ReadWriteOnce y límites de escalado.

La UI escala de 2 a 5 réplicas. El backend queda en una réplica hasta migrar SQLite/Chroma a almacenamiento apto para múltiples escritores. Serverless no es la ruta elegida porque el sistema necesita persistencia y warm-up de embeddings.

Inicio y detención reproducibles en Docker Desktop:

```powershell
.\scripts\manage_k8s.ps1 start
.\scripts\manage_k8s.ps1 status
.\scripts\manage_k8s.ps1 stop
```

`start` publica UI en `http://localhost:18501`, API en `http://localhost:18000/docs` y Phoenix en `http://localhost:16006`. `stop` elimina HPA, escala los deployments a cero y conserva los PVC.
## API principal

### `POST /api/v1/auth/login`

Valida contra el hash bcrypt de la credencial staff configurada y devuelve un JWT HS256 con `sub`, `role`, `iat` y `exp`.

### `POST /api/v1/incidents`

Requiere `Authorization: Bearer <JWT>`. La API ignora cualquier identidad del cliente: `reported_by` se obtiene de `sub` y la escritura se ejecuta mediante MCP.

```json
{
  "gallery_id": "room-4",
  "category": "señalización",
  "description": "Falta una etiqueta accesible junto a la vitrina.",
  "priority": "medium"
}
```

### `POST /api/v1/chat`

```json
{
  "message": "¿Qué puedo ver en la Sala 4?",
  "user_role": "visitor",
  "session_id": "demo",
  "language": "es",
  "location_hint": "Sala 4"
}
```

La respuesta contiene texto, fuentes, scores posteriores al reranking, tools ejecutadas, confianza, `trace_id`, notas y estado real de cada componente.

### MCP

Servidor: `src/british_museum_agent/adapters_mcp/server.py`

Tools:

- `get_gallery_status`
- `get_accessibility_info`
- `create_incident` (protegida por el header interno `X-MCP-Internal-Token`)

El backend no accede a SQLite para crear incidentes; usa el contrato MCP.

## Consideraciones de producción

Los modelos Pydantic limitan el tamaño de mensajes, sesiones, metadatos y credenciales. El login aplica SlowAPI con dos cuotas configurables: 10 intentos por cuenta normalizada y 120 por origen directo cada minuto. No se confía en `X-Forwarded-For`, porque el backend puede exponerse directamente y ese header sería falsificable. El almacenamiento en memoria coincide con la única réplica del backend; si se habilitan varias réplicas, `RATE_LIMIT_STORAGE_URI` debe apuntar a Redis u otro backend compartido. Para una publicación abierta también conviene sumar throttling en el proxy o plataforma de hosting. La validación estructurada automática de citas de Gemini queda como mejora posterior; el sistema actual exige citas por prompt y conserva las fuentes recuperadas en la respuesta.

## Pruebas sin servicios externos

```powershell
python -m pytest -q
```

Las pruebas usan SQLite bajo `tmp_path`, dependency overrides y variables de entorno de test. No llaman Gemini, LangSmith, Hugging Face ni MCP por red.


## Verificación actual

- `pytest -q`: 125 passed.
- `scripts/validate_k8s.py --kubectl-dry-run skip`: 177 checks estáticos; `deploy_k8s.ps1 -Action validate -Overlay dev`: 179 checks, ambos sin warnings. Phoenix externo está fijado a `19.6.0`.
- Docker Compose verificado con UI, backend, MCP y Phoenix saludables.
- Kubernetes de Docker Desktop verificado end-to-end: deployments, PVC, HPA, NetworkPolicies, Chroma (117 chunks), reranker, Gemini y trazas Phoenix.
- LangSmith está activo y verificado en Kubernetes junto con Phoenix OSS; una ejecución real registró latencia, tokens, costo, tags y metadata.
