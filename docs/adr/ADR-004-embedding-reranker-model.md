# ADR-004: Modelos de Embedding y Reranker Multilingües Locales

- **Fecha**: 2026-07-25
- **Estado**: Aceptado
- **Contexto**: El sistema requiere codificar el corpus de conocimiento (en español) a vectores para búsqueda semántica, y luego rerankear los resultados candidate para mejorar la precisión de los top-k entregados al LLM. Los modelos deben: (a) correr localmente sin llamadas a APIs externas, (b) tener buen rendimiento en español y otros idiomas del corpus del British Museum, (c) ser livianos para correr en CPU, (d) descargarse una sola vez y cachearse con Hugging Face. El reranker debe ejecutarse después de la recuperación vectorial inicial para refinar el orden con un cross-encoder, que es más costoso pero más preciso que la similitud coseno de los embeddings.
- **Decisión**: Se eligieron dos modelos de `sentence-transformers`:
  - **Embeddings**: `paraphrase-multilingual-MiniLM-L12-v2` — modelo ligero (≈470MB) con soporte para 50+ idiomas, ideal para recuperación semántica multilingüe. Genera embeddings de 384 dimensiones con `normalize_embeddings=True`.
  - **Reranker**: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` — cross-encoder multilingüe basado en MiniLM, entrenado sobre MS MARCO passage ranking. Opera como reranker: dado un par (query, pasaje) devuelve un score de relevancia.
  
  Ambos se descargan bajo demanda si no están en caché de Hugging Face, y se calientan durante el startup del backend (warmup en `lifespan`). El reranker no se usa en el fallback lexical. La combinación final usa una fusión ponderada: `0.25 × vector_score + 0.75 × reranker_score`, aplicando sigmoide a los scores crudos del cross-encoder.
- **Consecuencias**:
  - **Positivas**:
    - Sin dependencia de APIs externas de embedding: todo corre localmente, sin costos recurrentes ni latencia de red.
    - Modelos multilingües: cubren español, inglés y otros idiomas presentes en el corpus del museo.
    - Tamaño moderado (≈470MB + ≈450MB): descargables en segundos y cacheables con volúmenes Docker.
    - El warmup en startup evita que el primer chat sufra la descarga del modelo.
    - El reranker mejora significativamente la precisión de los top-4 frente a solo distancia coseno.
    - La fusión ponderada da más peso al cross-encoder, que es más preciso.
  - **Negativas**:
    - Cold start en la primera ejecución (descarga de modelos desde Hugging Face).
    - Ambos modelos cargan en RAM del backend: ≈1GB combinado, además de Chroma.
    - El reranker añade latencia por query (~100-300ms en CPU para 10 candidates).
    - Sin GPU, el throughput está limitado a ~3-5 queries/segundo para el pipeline completo de embedding + reranking.
    - Modelos no actualizables sin regenerar el índice Chroma (el fingerprint del embedding model se almacena en metadatos de la colección).
- **Alternativas consideradas**:
  - **OpenAI Embeddings API (`text-embedding-3-small`)**: Se descartó por dependencia externa, costos recurrentes, latencia de red, y falta de control sobre la disponibilidad.
  - **Modelo solo con embeddings sin reranker**: Se descartó porque la distancia coseno sola no es suficientemente precisa para el ranking final; el cross-encoder corrige falsos positivos de la recuperación vectorial.
  - **`all-MiniLM-L6-v2`**: Excelente para inglés pero sin soporte multilingüe. Inservible para un corpus principalmente en español.
  - **`multilingual-e5-small`**: Alternativa viable pero con mayor tamaño y menor comunidad de adopción que `paraphrase-multilingual-MiniLM`.
  - **Cohere Reranker API**: Se descartó por dependencia externa y costos.
  - **BGE-M3 (BAAI)**: Modelo más grande y preciso, pero requiere ≈1.5GB adicionales y el beneficio marginal no justifica el costo de recursos para el volumen actual.
  - **BM25 como única recuperación**: Se descartó porque no captura semántica; el fallback lexical existe solo como respaldo.
- **Referencias**:
  - `src/british_museum_agent/retrieval/vector_store.py` — `LocalSentenceTransformerEmbeddings`, `MultilingualCrossEncoderReranker`, `VectorKnowledgeBase.search()`
  - `src/british_museum_agent/config.py` — `embedding_model_name`, `reranker_model_name`, `retrieval_candidate_k`
  - `docker-compose.yml` — volumen `huggingface-cache` para persistencia de modelos
  - `pyproject.toml` — dependencia `sentence-transformers==5.6.0`
