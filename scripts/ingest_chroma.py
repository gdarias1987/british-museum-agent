from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from british_museum_agent.config import get_settings  # noqa: E402
from british_museum_agent.retrieval.corpus import (  # noqa: E402
    corpus_fingerprint,
    load_spanish_corpus,
)
from british_museum_agent.retrieval.indexer import build_chroma_index  # noqa: E402
from british_museum_agent.retrieval.vector_store import (  # noqa: E402
    LocalSentenceTransformerEmbeddings,
)


def build_index(*, force: bool = False):
    settings = get_settings()
    raw_dir = _resolve(settings.raw_spanish_path)
    chroma_path = _resolve(settings.chroma_path)
    chunks = load_spanish_corpus(raw_dir, project_root=ROOT)
    fingerprint = corpus_fingerprint(
        raw_dir,
        embedding_model=settings.embedding_model_name,
    )
    result = build_chroma_index(
        chroma_path=chroma_path,
        collection_name=settings.chroma_collection_name,
        chunks=chunks,
        fingerprint=fingerprint,
        embedding_model=LocalSentenceTransformerEmbeddings(
            settings.embedding_model_name
        ),
        force=force,
    )
    action = "reconstruido" if result.rebuilt else "sin cambios"
    print(
        f"Índice Chroma {action}: colección={result.collection_name}, "
        f"chunks={result.chunk_count}, ruta={result.path}"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Indexa el corpus español en ChromaDB.")
    parser.add_argument("--force", action="store_true", help="Fuerza la reconstrucción del índice.")
    parser.add_argument(
        "--allow-failure",
        action="store_true",
        help="Informa el error y permite iniciar el backend con fallback léxico.",
    )
    args = parser.parse_args()
    try:
        build_index(force=args.force)
    except Exception as exc:
        message = (
            "Chroma no pudo prepararse "
            f"({type(exc).__name__}). El backend sólo podrá usar el fallback léxico."
        )
        if args.allow_failure:
            print(f"ADVERTENCIA: {message}", file=sys.stderr)
            return
        raise RuntimeError(message) from exc


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


if __name__ == "__main__":
    main()