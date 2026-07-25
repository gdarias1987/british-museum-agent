from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "official_download"
PAGES = OUT / "pages"
PDFS = OUT / "pdf"
META = OUT / "metadata"

SEED_URLS = [
    "https://www.britishmuseum.org/visit",
    "https://www.britishmuseum.org/visit/museum-map",
    "https://www.britishmuseum.org/visit/museum-map/text-alternative-museum-map",
    "https://www.britishmuseum.org/visit/accessibility-museum",
    "https://www.britishmuseum.org/collection/galleries",
    "https://www.britishmuseum.org/our-work/departments/egypt-and-sudan",
    "https://www.britishmuseum.org/our-work/departments/middle-east",
    "https://www.britishmuseum.org/collection/egypt/explore-rosetta-stone",
    "https://www.britishmuseum.org/collection/object/Y_EA24",
    "https://www.britishmuseum.org/collection/galleries/mesopotamia",
    "https://www.britishmuseum.org/collection/galleries/mesopotamia-1500-539-bc",
    "https://www.britishmuseum.org/collection/galleries/assyria-nimrud",
]

RELEVANT_PATH_MARKERS = (
    "/visit",
    "/collection/galleries",
    "/collection/egypt",
    "/collection/object/",
    "/our-work/departments/egypt-and-sudan",
    "/our-work/departments/middle-east",
    "/learn/schools/ages-7-11/ancient-egypt",
    "/learn/schools/ages-7-11/middle-east-and-asia",
)

MAX_PAGES = 120
REQUEST_TIMEOUT = 30
SLEEP_SECONDS = 0.35
HEADERS = {"User-Agent": "BritishMuseumAgentCourseProject/1.0 (+local educational corpus)"}


class TextAndLinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.parts: list[str] = []
        self.links: list[str] = []
        self.assets: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): v for k, v in attrs if v}
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a" and attrs_dict.get("href"):
            self.links.append(attrs_dict["href"])
        if tag in {"img", "source"}:
            for key in ("src", "data-src", "srcset"):
                if attrs_dict.get(key):
                    self.assets.append(attrs_dict[key])
        if tag in {"h1", "h2", "h3", "p", "li", "dt", "dd"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"h1", "h2", "h3", "p", "li", "dt", "dd"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._in_title:
            self.title += text
        self.parts.append(text + " ")


def slugify(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "home"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-").lower()
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def normalize_url(url: str, base: str) -> str | None:
    absolute = urljoin(base, url)
    absolute, _ = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc != "www.britishmuseum.org":
        return None
    return absolute


def is_relevant(url: str) -> bool:
    path = urlparse(url).path
    return any(marker in path for marker in RELEVANT_PATH_MARKERS)


def clean_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    deduped: list[str] = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return "\n".join(deduped)


def write_manifest(records: list[dict], assets: list[dict]) -> None:
    META.mkdir(parents=True, exist_ok=True)
    with (META / "manifest.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (META / "assets.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["source_page", "asset_url", "asset_type"])
        writer.writeheader()
        writer.writerows(assets)


def download_pdf(url: str, source_page: str) -> dict | None:
    PDFS.mkdir(parents=True, exist_ok=True)
    target = PDFS / f"{slugify(url)}.pdf"
    if target.exists():
        return {"url": url, "source_page": source_page, "file": str(target.relative_to(OUT)), "size_bytes": target.stat().st_size}
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        if "pdf" not in response.headers.get("content-type", "").lower() and not url.lower().endswith(".pdf"):
            return None
        target.write_bytes(response.content)
        return {"url": url, "source_page": source_page, "file": str(target.relative_to(OUT)), "size_bytes": target.stat().st_size}
    except requests.RequestException as exc:
        return {"url": url, "source_page": source_page, "error": str(exc)}


def main() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    META.mkdir(parents=True, exist_ok=True)

    queue = deque(SEED_URLS)
    seen: set[str] = set()
    records: list[dict] = []
    asset_rows: list[dict] = []

    while queue and len(seen) < MAX_PAGES:
        url = queue.popleft()
        if url in seen or not is_relevant(url):
            continue
        seen.add(url)
        print(f"GET {len(seen):03d} {url}")
        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            records.append({"url": url, "error": str(exc)})
            continue

        content_type = response.headers.get("content-type", "")
        if "pdf" in content_type or url.lower().endswith(".pdf"):
            pdf_record = download_pdf(url, url)
            if pdf_record:
                records.append({"url": url, **pdf_record})
            continue
        if "html" not in content_type:
            continue

        parser = TextAndLinkExtractor()
        parser.feed(response.text)
        text = clean_text("".join(parser.parts))
        title = clean_text(parser.title) or url
        filename = f"{slugify(url)}.md"
        target = PAGES / filename
        target.write_text(
            "---\n"
            f"title: {json.dumps(title, ensure_ascii=False)}\n"
            f"source_url: {json.dumps(url, ensure_ascii=False)}\n"
            f"content_type: official_html_snapshot\n"
            "language: en\n"
            "---\n\n"
            f"# {title}\n\n{text}\n",
            encoding="utf-8",
        )
        records.append({
            "url": url,
            "title": title,
            "file": str(target.relative_to(OUT)),
            "size_bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })

        for raw_asset in parser.assets:
            first_asset = raw_asset.split(",")[0].split()[0]
            asset_url = normalize_url(first_asset, url)
            if asset_url:
                asset_rows.append({"source_page": url, "asset_url": asset_url, "asset_type": "image"})

        for raw_link in parser.links:
            next_url = normalize_url(raw_link, url)
            if not next_url:
                continue
            if next_url.lower().endswith(".pdf"):
                pdf_record = download_pdf(next_url, url)
                if pdf_record:
                    records.append({"url": next_url, **pdf_record})
                asset_rows.append({"source_page": url, "asset_url": next_url, "asset_type": "pdf"})
                continue
            if is_relevant(next_url) and next_url not in seen:
                queue.append(next_url)
        time.sleep(SLEEP_SECONDS)

    write_manifest(records, asset_rows)
    print(f"Downloaded page snapshots: {sum(1 for r in records if str(r.get('file', '')).startswith('pages/'))}")
    print(f"Downloaded/registered pdf records: {sum(1 for r in records if str(r.get('file', '')).startswith('pdf/'))}")
    print(f"Registered assets: {len(asset_rows)}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
