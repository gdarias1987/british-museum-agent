from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from british_museum_agent.retrieval.corpus import load_spanish_corpus  # noqa: E402

RAW_DIR = ROOT / "data" / "raw" / "spanish"
INDEX_PATH = ROOT / "data" / "processed" / "knowledge_index.json"


def main() -> None:
    chunks = load_spanish_corpus(RAW_DIR, project_root=ROOT)
    documents = [
        {
            "chunk_id": chunk.chunk_id,
            "title": chunk.title,
            "source": chunk.source,
            "url": chunk.url,
            "text": chunk.text,
            "tags": list(chunk.tags),
        }
        for chunk in chunks
    ]
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(documents, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Índice léxico de fallback: {len(documents)} chunks -> {INDEX_PATH}")


if __name__ == "__main__":
    main()