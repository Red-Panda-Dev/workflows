# AGENTS.md

## Scope

The EPFR Files Downloader — a set of Mistral Workflows that fetch paginated disclosure records from the `epfr.gov.by` REST API, download each record's raw file content, extract archives, convert office documents to Markdown, OCR PDFs via Mistral, AI-distill structured dividend data, export share payouts, and produce JSON mappings organized by company UNP.

Four separate workflows, each independently invocable:

1. **`epfr-files-downloader`** — fetch pages → download files → extract archives → convert documents → produce `unp_file_mapping.json`
2. **`epfr-pdf-ocr-converter`** — OCR PDFs in mapped UNP folders → update mapping entries to point at Markdown files
3. **`epfr-ai-distiller`** — AI-extract structured dividend data from mapped Markdown files → produce `ai_distilled_dividends.json`
4. **`epfr-share-payout-exporter`** — join distilled dividends with share reference CSV → produce `share_payouts_by_unp.json`

## What lives here

```text
src/
├── discover.py                      # Scans workflows/ for workflow classes, starts Mistral worker
└── workflows/
    ├── __init__.py                  # Empty package marker (auto-discovery entry point)
    ├── start.py                     # CLI to trigger workflow execution via Mistral client
    └── epfr/
        ├── config.py                # Constants: API URLs, concurrency, retry, pagination, OCR, AI defaults
        ├── models.py                # Pydantic models: EpfrRecord, EpfrApiResponse, workflow I/O, AI extraction models
        ├── client.py                # aiohttp API client (fetch pages, download files by record ID)
        ├── detector.py              # Magic-byte file extension detection (API returns raw bytes, no names)
        ├── extractor.py             # Archive extraction (ZIP, TAR, GZ) with OOXML detection
        ├── converter.py             # Document-to-Markdown: docx/doc/xls/xlsx via Python libs
        ├── markdown_cleanup.py      # Token-heavy markdown artifact removal (images, excessive blanks)
        ├── pdf_ocr.py               # PDF OCR conversion via Mistral OCR plugin, mapping update
        ├── pdf_ocr_workflow.py      # epfr-pdf-ocr-converter workflow + activity
        ├── ai_distiller.py          # AI distillation: Mistral Large structured extraction of dividend data
        ├── ai_distiller_workflow.py # epfr-ai-distiller workflow + activity
        ├── share_payout_exporter.py # Join distilled dividends with share reference CSV
        ├── share_payout_exporter_workflow.py # epfr-share-payout-exporter workflow + activity
        ├── workflow.py              # epfr-files-downloader workflow + 5 activities (main pipeline)
        ├── prompts/                 # Prompt templates for AI distillation
        │   └── dividends_parsing.md
        └── tests/                   # Unit tests (7 modules)
            ├── test_client.py       # Client and download logic tests
            ├── test_converter.py    # Document conversion tests
            ├── test_detector.py     # Magic-byte detection tests
            ├── test_extractor.py    # Archive extraction and OOXML detection tests
            ├── test_models.py       # Pydantic model parsing tests
            ├── test_pdf_ocr.py      # PDF OCR conversion tests (skipped by default)
            └── test_ai_distiller.py # AI distillation tests
output/                              # Downloaded files, mapping JSON, distilled JSON (gitignored)
Dockerfile                           # Container image for worker deployment
generate_sql.py                      # Standalone: share_payouts_by_unp.json → SQL INSERT statements
```

## Local boundaries and invariants

- **Workflow sandbox:** `os.environ` access is forbidden inside workflow classes. All env var reads happen inside `@workflows.activity()` functions.
- **Discovery contract:** Each workflow class is decorated with `@workflows.workflow.define(name="...", ...)`. The discoverer in `discover.py` scans for `__workflows_workflow_def`.
- **No filenames from API:** The EPFR API returns raw binary content with no filename. `detector.py` inspects the first bytes (magic bytes) and maps them to an extension. Files are saved as `<record_id><detected_ext>` (e.g. `141278.pdf`).
- **UNP-based folder layout:** Files are organized by the holder's UNP (tax ID string). UNP comes from `rec.holder.unp` if non-empty, otherwise falls back to `rec.organization.unp`. Records missing a `holder.id` are skipped in the mapping but logged.
- **Pagination starts at 0:** `FIRST_PAGE_NO = 0`. The EPFR API uses 0-based page numbers — do not adjust to 1.
- **Early termination:** Fetching stops when the API response has `last=True`, before reaching `max_pages`.
- **Atomic writes:** All JSON output uses `tempfile.mkstemp()` → write → `os.replace()`. Keep this pattern for any new output files.
- **OOXML detection in extractor:** ZIP files that are actually OOXML documents (docx, xlsx, pptx) are detected by internal markers (`[Content_Types].xml`, etc.) and renamed instead of extracted.
- **PDF OCR split:** PDF conversion is a separate workflow (`epfr-pdf-ocr-converter`), not part of the main download pipeline. It reads the mapping JSON, OCRs PDF entries, and updates mapping entries to point at the resulting `.md` files.
- **AI distillation is separate:** AI extraction runs as its own workflow (`epfr-ai-distiller`), consuming the mapping JSON output from the download pipeline. It does not modify the mapping — it produces `ai_distilled_dividends.json`.
- **Share payout export is separate:** Export runs as its own workflow (`epfr-share-payout-exporter`), joining `ai_distilled_dividends.json` with a share reference CSV. It produces `share_payouts_by_unp.json`.
- **File lineage tracking:** The mapping JSON tracks `extracted_from` and `converted_from` fields so each entry knows its transformation chain (archive → extracted file → converted .md).

## Safe change rules

- **Changing API request params:** Edit `client.py` (`fetch_page`). Constants live in `config.py`.
- **Changing download behavior:** Edit `client.py` (`download_all_files`, `download_file`). Retry/concurrency tuning in `config.py`.
- **Adding new magic-byte signatures:** Edit `detector.py:SIGNATURES`. Keep the list ordered — first match wins.
- **Changing archive extraction:** Edit `extractor.py`. New archive extensions go in `ARCHIVE_EXTENSIONS`. New OOXML markers go in `OOXML_MARKERS` / `OOXML_EXTENSION_MAP`.
- **Changing document conversion:** Edit `converter.py`. Supports docx, doc, xls, xlsx. New types go in the extension dispatch.
- **Changing markdown cleanup:** Edit `markdown_cleanup.py`. Affects both converter and OCR pipelines.
- **Changing PDF OCR behavior:** Edit `pdf_ocr.py`. OCR model and concurrency in `config.py` (`OCR_MODEL`, `MAX_CONCURRENT_OCR`, `MAX_PDF_SIZE_BYTES`).
- **Changing AI distillation:** Edit `ai_distiller.py`. Model and retry tuning in `config.py` (`AI_MODEL`, `AI_MAX_RETRIES`, etc.). Prompt template at `prompts/dividends_parsing.md`.
- **Changing share payout export:** Edit `share_payout_exporter.py`. Config constants in `config.py`.
- **Changing output schema:** Edit `models.py` first, then update affected workflow and consumer modules.
- **Do not edit `.agents/`** — read-only Mistral SDK references.

## Validation

```bash
# From inside epfr-downloader/

# Install dependencies
uv sync

# Lint
make lint

# Auto-fix
make refactor

# Run tests (test_pdf_ocr.py skipped by default)
make test

# Start worker
make start-worker

# Trigger workflows
make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'
make execute-share-payout-exporter input='{"output_dir": "output"}'

# Docker
make docker-build
make docker-run
```

## Nearby docs

- `src/workflows/epfr/AGENTS.md` — pipeline internals, data contracts, activity boundaries, per-file editing rules
- Root `AGENTS.md` — workspace conventions, two-env layout, ruff config
- Root `ARCHITECTURE.md` — full system code map and invariants
