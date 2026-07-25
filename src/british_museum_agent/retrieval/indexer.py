from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from british_museum_agent.retrieval.corpus import CorpusChunk
from british_museum_agent.retrieval.vector_store import EmbeddingModel


@dataclass(frozen=True)
class IndexBuildResult:
    collection_name: str
    chunk_count: int
    fingerprint: str
    rebuilt: bool
    path: str


def build_chroma_index(
    *,
    chroma_path: Path,
    collection_name: str,
    chunks: list[CorpusChunk],
    fingerprint: str,
    embedding_model: EmbeddingModel,
    force: bool = False,
    client: Any | None = None,
) -> IndexBuildResult:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    chroma_path.mkdir(parents=True, exist_ok=True)
    manifest_path = chroma_path / "index_manifest.json"
    client = client or chromadb.PersistentClient(
        path=str(chroma_path),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    current = _get_collection(client, collection_name)
    manifest = _read_manifest(manifest_path)
    if (
        not force
        and current is not None
        and current.count() == len(chunks)
        and manifest.get("fingerprint") == fingerprint
        and manifest.get("embedding_model") == embedding_model.model_name
    ):
        return IndexBuildResult(
            collection_name=collection_name,
            chunk_count=len(chunks),
            fingerprint=fingerprint,
            rebuilt=False,
            path=str(chroma_path),
        )

    if not chunks:
        raise ValueError("El corpus español no contiene chunks indexables.")

    building_name = f"{collection_name}__building"
    backup_name = f"{collection_name}__backup"
    _delete_if_exists(client, building_name)
    _delete_if_exists(client, backup_name)
    building = client.create_collection(
        name=building_name,
        metadata={
            "hnsw:space": "cosine",
            "embedding_model": embedding_model.model_name,
            "corpus_fingerprint": fingerprint,
            "language": "es",
        },
    )
    try:
        for start in range(0, len(chunks), 32):
            batch = chunks[start : start + 32]
            texts = [chunk.text for chunk in batch]
            building.add(
                ids=[chunk.chunk_id for chunk in batch],
                documents=texts,
                metadatas=[chunk.metadata for chunk in batch],
                embeddings=embedding_model.embed_documents(texts),
            )
    except Exception:
        _delete_if_exists(client, building_name)
        raise

    old_collection = _get_collection(client, collection_name)
    if old_collection is not None:
        old_collection.modify(name=backup_name)
    try:
        building.modify(name=collection_name)
    except Exception:
        if old_collection is not None:
            old_collection.modify(name=collection_name)
        raise
    _delete_if_exists(client, backup_name)

    result = IndexBuildResult(
        collection_name=collection_name,
        chunk_count=len(chunks),
        fingerprint=fingerprint,
        rebuilt=True,
        path=str(chroma_path),
    )
    _write_manifest(
        manifest_path,
        {
            **asdict(result),
            "embedding_model": embedding_model.model_name,
            "language": "es",
        },
    )
    return result


def _get_collection(client, name: str):
    try:
        return client.get_collection(name)
    except Exception:
        return None


def _delete_if_exists(client, name: str) -> None:
    if _get_collection(client, name) is not None:
        client.delete_collection(name)


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)