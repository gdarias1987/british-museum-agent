from __future__ import annotations
import csv
import pathlib
import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "metadata" / "assets_to_download.csv"


def main() -> None:
    headers = {"User-Agent": "Mozilla/5.0 British-Museum-RAG-Corpus/1.0"}
    with CSV_PATH.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            folder = ROOT / ("pdf_originals" if row["asset_type"] == "pdf" else "images")
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / row["file_name"]
            try:
                with requests.get(row["source_url"], headers=headers, timeout=120, stream=True) as response:
                    response.raise_for_status()
                    with target.open("wb") as out:
                        for chunk in response.iter_content(1024 * 1024):
                            if chunk:
                                out.write(chunk)
                print(f"OK  {target}")
            except requests.RequestException as exc:
                print(f"ERR {row['source_url']}: {exc}")


if __name__ == "__main__":
    main()
