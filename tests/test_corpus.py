from pathlib import Path

from british_museum_agent.retrieval.corpus import load_spanish_corpus


def test_spanish_corpus_parses_utf8_frontmatter_and_excludes_manifest():
    root = Path(__file__).resolve().parents[1]
    chunks = load_spanish_corpus(
        root / "data" / "raw" / "spanish",
        project_root=root,
    )

    assert len(chunks) >= 12
    assert all(chunk.metadata.get("language") == "es" for chunk in chunks)
    assert all("manifest" not in chunk.source for chunk in chunks)
    assert any("Piedra de Rosetta" in chunk.title for chunk in chunks)
    pdf_chunks = [chunk for chunk in chunks if chunk.source.endswith(".pdf")]
    assert pdf_chunks
    assert any("Mesopotamia" in chunk.title for chunk in pdf_chunks)
    assert any("escritura cuneiforme" in chunk.text.lower() for chunk in pdf_chunks)
    assert all(chunk.metadata["language"] == "es" for chunk in pdf_chunks)
    assert all(isinstance(chunk.metadata["page"], int) for chunk in pdf_chunks)
    assert all(len(chunk.text) <= 900 for chunk in pdf_chunks)
    assert not any("Ã" in chunk.text or "\ufffd" in chunk.text for chunk in chunks)