# British Museum Agent

Asistente RAG en espaÃ±ol para visitantes y personal del British Museum.

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
- **RecuperaciÃ³n:** ChromaDB persistente en `data/chroma`.
- **Embeddings:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
- **Reranking:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`.
- **GeneraciÃ³n:** Gemini mediante `langchain-google-genai`.
- **OrquestaciÃ³n:** LangGraph.
- **Operaciones:** tools MCP respaldadas por SQLite.
- **Interfaz:** Streamlit.
- **Trazabilidad:** LangSmith con variables estÃ¡ndar.

La respuesta de `POST /api/v1/chat` incluye un bloque `runtime` que informa quÃ© componentes estuvieron realmente activos. La readiness devuelve HTTP 503 con `status=degraded` si SQLite no tiene el esquema y seed mÃ­nimos, MCP no estÃ¡ disponible o el backend de retrieval requerido no estÃ¡ listo.

## ConfiguraciÃ³n

```powershell
copy .env.example .env
```

CompletÃ¡ sin publicar secretos:

```dotenv
GOOGLE_API_KEY=
# Alternativa si GOOGLE_API_KEY estÃ¡ vacÃ­a:
GEMINI_API_KEY=

LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=british-museum-agent

# GenerÃ¡ dos valores aleatorios, largos y distintos.
JWT_SECRET=
MCP_INTERNAL_TOKEN=
STAFF_DEMO_USERNAME=staff@example.com
STAFF_DEMO_PASSWORD=
JWT_EXPIRATION_MINUTES=60
```

`GOOGLE_API_KEY` tiene prioridad sobre `GEMINI_API_KEY`. Las claves se leen en runtime desde `.env`; no se copian al Dockerfile ni a la imagen. `JWT_SECRET` firma tokens staff con HS256. `MCP_INTERNAL_TOKEN` se envÃ­a como header de transporte `X-MCP-Internal-Token`; nunca forma parte de los argumentos de una tool. `STAFF_DEMO_PASSWORD` es obligatorio para el bootstrap, se guarda Ãºnicamente como hash bcrypt y no tiene valor pÃºblico predeterminado.

## EjecuciÃ³n local

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

Los modelos de embeddings y reranking se descargan si no estÃ¡n en cachÃ© y se calientan durante el startup de FastAPI. El primer chat no inicia esa carga. Si Chroma o el warmup fallan mientras `RETRIEVAL_BACKEND=chroma`, la API queda degradada y su health responde 503.

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

- `ui`, que recibe Ãºnicamente `STREAMLIT_BACKEND_URL`.
- `backend`, que puede leer `.env`.
- `mcp-server`, que recibe Ãºnicamente variables SQLite/MCP y las credenciales necesarias para el bootstrap staff; no recibe claves Gemini ni LangSmith.

El backend espera a que el healthcheck MCP confirme SQLite y autenticaciÃ³n interna. Luego ejecuta las ingestas. `scripts/ingest_chroma.py` compara el fingerprint del corpus y del modelo; si no hubo cambios, abre el Ã­ndice existente sin regenerar embeddings.

Persistencia:

- `./data/chroma:/app/data/chroma`
- `./data/sqlite:/app/data/sqlite`
- volumen `huggingface-cache` para los modelos
- PyTorch CPU en la imagen backend; no descarga CUDA

## API principal

### `POST /api/v1/auth/login`

Valida contra el hash bcrypt de la credencial staff configurada y devuelve un JWT HS256 con `sub`, `role`, `iat` y `exp`.

### `POST /api/v1/incidents`

Requiere `Authorization: Bearer <JWT>`. La API ignora cualquier identidad del cliente: `reported_by` se obtiene de `sub` y la escritura se ejecuta mediante MCP.

```json
{
  "gallery_id": "room-4",
  "category": "seÃ±alizaciÃ³n",
  "description": "Falta una etiqueta accesible junto a la vitrina.",
  "priority": "medium"
}
```

### `POST /api/v1/chat`

```json
{
  "message": "Â¿QuÃ© puedo ver en la Sala 4?",
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

## Consideraciones de producciÃ³n

Los modelos Pydantic limitan el tamaÃ±o de mensajes, sesiones, metadatos y credenciales. Para una publicaciÃ³n abierta se debe sumar throttling por IP/sesiÃ³n en el proxy o plataforma de hosting. La validaciÃ³n estructurada automÃ¡tica de citas de Gemini queda como mejora posterior; el sistema actual exige citas por prompt y conserva las fuentes recuperadas en la respuesta.

## Pruebas sin servicios externos

```powershell
python -m pytest -q
```

Las pruebas usan SQLite bajo `tmp_path`, dependency overrides y variables de entorno de test. No llaman Gemini, LangSmith, Hugging Face ni MCP por red.

