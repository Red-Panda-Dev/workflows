# AGENTS.md — EPFR Downloader (project)

## Scope and inheritance

Applies to: `epfr-downloader/` (the runtime project). Inherits repo-wide guidance from the root `AGENTS.md`: two-env layout, ruff/ty config, validation commands, and the architectural invariants (workflow sandbox rule, auto-discovery contract, **workflow isolation — no cross-workflow imports, communicate via `output/` artifacts only**). This file adds only the project-local module map and per-area change rules; it contains no parent-rule overrides.

The EPFR Files Downloader — Mistral Workflows that fetch paginated disclosure records from the `epfr.gov.by` REST API, download each record's raw file content, extract archives, convert office documents to Markdown, OCR PDFs/images, AI-distill structured dividend data, export share payouts, and produce JSON mappings organized by company UNP.

## The four workflows

Each is independently invocable and produces a filesystem artifact consumed by the next:

1. **`epfr-files-downloader`** (`workflow.py`, 5 activities) → writes `output/unp_file_mapping.json`
2. **`epfr-ocr-converter`** (`pdf_ocr_workflow.py`, 3 activities) → OCRs PDF/PNG/JPG/JPEG entries; updates mapping to point at `.md` files
3. **`epfr-ai-distiller`** (`ai_distiller_workflow.py`, 3 activities) → writes `output/ai_distilled_dividends.json`
4. **`epfr-share-payout-exporter`** (`share_payout_exporter_workflow.py`, 4 activities) → writes `output/share_payouts_by_unp.json` and `output/share_dividends_insert.sql` (SQL generation is the in-workflow 4th activity)

Pipeline chaining order: **downloader → (OCR converter, AI distiller) → share payout exporter.** Each workflow reads the previous one's output file; they never import each other.

## What lives here

```text
src/
├── discover.py                      # Scans workflows/ for workflow classes, starts Mistral worker
├── tests/                           # Unit tests (18 modules) + fixtures/
│   ├── conftest.py                  # Shared fixtures
│   ├── test_<module>.py             # Mirror the source modules one-to-one
│   └── test_*_workflow.py           # Per-workflow activity tests
└── workflows/
    ├── __init__.py                  # Empty package marker (auto-discovery entry point)
    ├── start.py                     # CLI to trigger workflow execution via Mistral client
    └── epfr/                        # See src/workflows/epfr/AGENTS.md for internals
        ├── config.py                # All constants & tuning knobs (EPFR_* env-overridable)
        ├── models.py                # All Pydantic models for the 4 workflows + API + AI extraction
        ├── client.py                # aiohttp API client (fetch pages, download files by record ID)
        ├── detector.py              # Magic-byte file extension detection (API returns raw bytes)
        ├── extractor.py             # Archive extraction (ZIP/TAR/GZ) with OOXML detection
        ├── converter.py             # Document→Markdown: docx/doc/xls/xlsx
        ├── markdown_cleanup.py      # Token-heavy markdown artifact removal
        ├── pdf_ocr.py               # OCR conversion (PDF/PNG/JPG/JPEG) + mapping update
        ├── *_workflow.py            # The 4 workflow entry modules
        └── prompts/dividends_parsing.md  # AI distillation prompt template
output/                              # Downloaded files, mapping/distilled/payout JSON, payout SQL (gitignored)
Dockerfile                           # Container image for worker deployment
```

## Local boundaries and invariants

These are project-specific implementation rules; the cross-cutting architectural invariants (sandbox, discovery, workflow isolation) are inherited from the root `AGENTS.md`.

- **No filenames from the API.** The EPFR API returns raw binary content with no filename. `detector.py` inspects the first bytes (magic bytes) and files are saved as `<record_id><detected_ext>` (e.g. `141278.pdf`). Archives, by contrast, contain real filenames.
- **UNP-based folder layout.** Files are organized by the holder's UNP (tax ID string) under `output/<UNP>/`. UNP comes from `rec.holder.unp` if non-empty, otherwise falls back to `rec.organization.unp`. Records missing a `holder.id` are skipped in the mapping but logged.
- **Pagination is 0-based.** `FIRST_PAGE_NO = 0` — do not adjust to 1.
- **Early termination.** Fetching stops when the API response has `last=True`, before reaching `max_pages`.
- **Atomic writes.** All JSON/SQL output uses `tempfile.mkstemp()` → write → `os.replace()`. Keep this pattern for any new output file.
- **OOXML detection in extractor.** ZIP files that are actually OOXML documents (docx/xlsx/pptx) are detected by internal markers (`[Content_Types].xml`, etc.) and renamed, not extracted.
- **File lineage tracking.** The mapping JSON carries `extracted_from` (archive → extracted file) and `converted_from` (source → `.md`) so each entry knows its full transformation chain.
- **PDFs are not converted inline.** PDF/PNG/JPG/JPEG files are left for the separate OCR workflow; the converter handles office documents only.
- **Config is env-overridable.** Every tuning knob in `config.py` can be overridden via an `EPFR_*` env var (e.g. `EPFR_MAX_CONCURRENT_OCR`, `EPFR_AI_MODEL`), falling back to `EPFR_DEFAULTS`. Do not hardcode these values in workflow modules.

## Safe change rules

- **API request params** → `client.py` (`fetch_page`/`build_page_url`). Constants in `config.py`.
- **Download behavior / retry / concurrency** → `client.py` (`download_file`, `download_all_files`). Tuning in `config.py`.
- **Magic-byte signatures** → `detector.py:SIGNATURES` (ordered list — first match wins; keep PDF before overlapping signatures).
- **Archive extraction / OOXML markers** → `extractor.py` (`ARCHIVE_EXTENSIONS`, OOXML marker maps).
- **Document conversion (new office types)** → `converter.py` extension dispatch.
- **Markdown cleanup** → `markdown_cleanup.py` (affects both converter and OCR pipelines).
- **OCR behavior / model / size limits / supported extensions** → `pdf_ocr.py` + `config.py` (`OCR_MODEL`, `MAX_CONCURRENT_OCR`, `MAX_PDF_SIZE_BYTES`, `OCR_SUPPORTED_EXTENSIONS`).
- **AI distillation / model / retry / prompt** → `ai_distiller.py` + `config.py` (`AI_MODEL`, `AI_MAX_RETRIES`, `AI_FILE_DELAY`) + `prompts/dividends_parsing.md`.
- **Share payout export / CSV matching** → `share_payout_exporter.py`. Config constants in `config.py`.
- **Output / data schema** → edit `models.py` first, then update every affected workflow and consumer module (and its test).
- **Do not edit `.agents/`** — read-only Mistral SDK references.

## Validation

```bash
# From inside epfr-downloader/
uv sync                                   # install runtime deps
make lint                                 # ruff format --check + ruff check
make refactor                             # auto-fix + format
make test                                 # coverage run; test_pdf_ocr.py skipped by default
make start-worker                         # auto-discover workflows, start Mistral worker
make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'
make execute-share-payout-exporter input='{"output_dir": "output"}'
make docker-build && make docker-run
```

## Nearby docs

- `src/workflows/epfr/AGENTS.md` — pipeline internals: module map, activity boundaries, data contracts, per-file editing rules
- Root `AGENTS.md` — workspace conventions, two-env layout, ruff config, architectural invariants
- Root `ARCHITECTURE.md` — full system code map, logical layers, dependency direction, data flow
- `README.md` — setup and usage
