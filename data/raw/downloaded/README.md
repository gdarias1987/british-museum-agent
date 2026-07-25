# British Museum - Mesopotamia corpus inicial

Corpus inicial para RAG, preparado el 2026-07-19 a partir de fuentes oficiales del British Museum.

## Contenido

- `pdf/mesopotamia_official_snapshot.pdf`: snapshot textual de la guía oficial educativa y contexto de las salas.
- `pages/*.md`: documentos normalizados por sala, objeto y colección.
- `metadata/manifest.jsonl`: índice de documentos y fuentes.
- `metadata/assets_to_download.csv`: URLs directas de PDFs e imágenes oficiales.
- `scripts/download_assets.py`: descargador reproducible para ejecutar en una máquina con acceso a Internet.

## Nota

El sitio del British Museum permitió consultar y extraer contenido mediante navegación web, pero bloqueó la descarga binaria directa desde este entorno. Por eso el ZIP contiene snapshots textuales utilizables y un script con las URLs exactas para bajar los binarios originales localmente.
