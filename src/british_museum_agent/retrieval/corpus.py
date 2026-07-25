from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader


@dataclass(frozen=True)
class CorpusChunk:
    chunk_id: str
    title: str
    source: str
    text: str
    url: str | None
    tags: tuple[str, ...]
    metadata: dict[str, str | int | float | bool]


def load_spanish_corpus(
    raw_dir: Path,
    *,
    project_root: Path | None = None,
    max_chars: int = 900,
) -> list[CorpusChunk]:
    chunks: list[CorpusChunk] = []
    for path in sorted(raw_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter, body = parse_markdown_document(text)
        title = str(frontmatter.get("title") or _extract_heading(body) or path.stem)
        source = _relative_source(path, project_root)
        url = _optional_string(frontmatter.get("source_url"))
        tags = _build_tags(frontmatter)

        for position, chunk_text in enumerate(chunk_markdown(body, title, max_chars=max_chars), start=1):
            chunk_id = f"{path.stem}-{position}"
            metadata = _to_chroma_metadata(
                {
                    **frontmatter,
                    "chunk_id": chunk_id,
                    "title": title,
                    "source": source,
                    "source_url": url or "",
                    "tags": list(tags),
                }
            )
            chunks.append(
                CorpusChunk(
                    chunk_id=chunk_id,
                    title=title,
                    source=source,
                    text=chunk_text,
                    url=url,
                    tags=tags,
                    metadata=metadata,
                )
            )
    for path in sorted(raw_dir.rglob("*.pdf")):
        chunks.extend(
            _load_pdf_chunks(
                path,
                project_root=project_root,
                max_chars=max_chars,
            )
        )
    return chunks


def _load_pdf_chunks(
    path: Path,
    *,
    project_root: Path | None,
    max_chars: int,
) -> list[CorpusChunk]:
    reader = PdfReader(path)
    title = _pdf_title(reader, path)
    source = _relative_source(path, project_root)
    page_count = len(reader.pages)
    chunks: list[CorpusChunk] = []

    for page_number, page in enumerate(reader.pages, start=1):
        page_text = _clean_pdf_text(page.extract_text() or "")
        if not page_text:
            continue
        page_title = f"{title} - p\u00e1gina {page_number}"
        for position, chunk_text in enumerate(
            chunk_markdown(page_text, page_title, max_chars=max_chars),
            start=1,
        ):
            chunk_id = f"{path.stem}-p{page_number}-{position}"
            metadata = _to_chroma_metadata(
                {
                    "chunk_id": chunk_id,
                    "title": page_title,
                    "source": source,
                    "source_url": "",
                    "language": "es",
                    "content_type": "application/pdf",
                    "page": page_number,
                    "page_count": page_count,
                    "tags": ["es", "pdf"],
                }
            )
            chunks.append(
                CorpusChunk(
                    chunk_id=chunk_id,
                    title=page_title,
                    source=source,
                    text=chunk_text,
                    url=None,
                    tags=("es", "pdf"),
                    metadata=metadata,
                )
            )
    return chunks


def parse_markdown_document(text: str) -> tuple[dict[str, Any], str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized.strip()
    closing = normalized.find("\n---\n", 4)
    if closing < 0:
        return {}, normalized.strip()

    metadata: dict[str, Any] = {}
    for line in normalized[4:closing].splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        metadata[key.strip()] = _parse_frontmatter_value(raw_value.strip())
    return metadata, normalized[closing + 5 :].strip()


def chunk_markdown(body: str, title: str, *, max_chars: int = 900) -> list[str]:
    cleaned = re.sub(r"^#\s+.+$", "", body, count=1, flags=re.MULTILINE).strip()
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", cleaned)
        if paragraph.strip()
    ]
    if not paragraphs:
        return []

    prefix = f"# {title}\n\n"
    body_limit = max(1, max_chars - len(prefix))
    segments = [
        segment
        for paragraph in paragraphs
        for segment in _split_oversized_text(paragraph, body_limit)
    ]
    chunks: list[str] = []
    current: list[str] = []
    current_length = len(prefix)
    for segment in segments:
        added_length = len(segment) + (2 if current else 0)
        if current and current_length + added_length > max_chars:
            chunks.append(prefix + "\n\n".join(current))
            current = [segment]
            current_length = len(prefix) + len(segment)
        else:
            current.append(segment)
            current_length += added_length
    if current:
        chunks.append(prefix + "\n\n".join(current))
    return chunks


def _split_oversized_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    units = [line.strip() for line in text.splitlines() if line.strip()]
    if len(units) == 1:
        units = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", text)
            if sentence.strip()
        ]

    segments: list[str] = []
    current = ""
    for unit in units:
        pieces = _split_by_words(unit, max_chars) if len(unit) > max_chars else [unit]
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > max_chars:
                segments.append(current)
                current = piece
            else:
                current = candidate
    if current:
        segments.append(current)
    return segments


def _split_by_words(text: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            pieces.append(current)
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def corpus_fingerprint(raw_dir: Path, *, embedding_model: str, max_chars: int = 900) -> str:
    digest = hashlib.sha256()
    digest.update(f"embedding={embedding_model}\nmax_chars={max_chars}\n".encode())
    corpus_files = sorted(
        (
            path
            for path in raw_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".pdf"}
        ),
        key=lambda path: path.as_posix(),
    )
    for path in corpus_files:
        digest.update(path.relative_to(raw_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _pdf_title(reader: PdfReader, path: Path) -> str:
    metadata_title = str(getattr(reader.metadata, "title", "") or "").strip()
    if metadata_title:
        return metadata_title

    for page in reader.pages:
        text = _clean_pdf_text(page.extract_text() or "")
        if text:
            return text.splitlines()[0][:160]
    return path.stem.replace("_", " ").replace("-", " ").strip().title()


def _clean_pdf_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\uf0b7", "-")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _parse_frontmatter_value(value: str) -> Any:
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip("\"'")


def _extract_heading(body: str) -> str | None:
    match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    return match.group(1).strip() if match else None


def _relative_source(path: Path, project_root: Path | None) -> str:
    if project_root is None:
        return path.as_posix()
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_tags(metadata: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("entity_type", "room", "rooms", "museum_number", "language"):
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value not in (None, ""):
            values.append(str(value))
    return tuple(dict.fromkeys(values))


def _to_chroma_metadata(values: dict[str, Any]) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            result[key] = value
        else:
            result[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return result