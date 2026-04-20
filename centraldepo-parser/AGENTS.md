# AGENTS.md

## Scope

The CentralDepo Dividend Parser — a Mistral Workflow that scrapes paginated dividend disclosure records from `centraldepo.by` via the Cloudflare Browser Rendering API, groups them by company, downloads archive files (ZIP/TAR/GZ), extracts their contents, converts documents to Markdown (non-PDF locally, PDF via Mistral OCR after R2 upload), and saves structured results to JSON.

## What lives here

```text
src/
├── discover.py                # Scans workflows/ for workflow classes, starts Mistral worker
├── dev_worker.py              # watchdog-based auto-reload wrapper around discover.py
└── workflows/
    ├── __init__.py            # Empty package marker (auto-discovery entry point)
    ├── start.py               # CLI to trigger workflow execution via Mistral client
    └── centraldepo/           # Core workflow package
        ├── config.py          # Constants: BASE_URL, SCRAPE_API, SELECTOR, retry/concurrency defaults
        ├── models.py          # Pydantic models: DividendRecord, ScrapeResult, WorkflowInput/Output
        ├── parser.py          # HTML parsing: extract records, group by company
        ├── client.py          # Cloudflare Browser Rendering HTTP client with retries
        ├── downloader.py      # Concurrent file download with retry/backoff per company
        ├── extractor.py       # Archive extraction (ZIP, TAR, GZ, TGZ) with atomic writes
        ├── converter.py       # Document-to-Markdown: docx/doc/xls via Python libs, PDF via Mistral OCR
        ├── r2_storage.py      # Cloudflare R2 upload (S3-compatible) for PDF → Mistral OCR pipeline
        └── workflow.py        # Mistral workflow + activities (orchestration entry point)
example.py                     # Standalone async scraper (non-workflow version of same logic)
output/                        # JSON output + downloaded/extracted/converted files (gitignored)
```

## Local boundaries and invariants

- **Workflow sandbox:** The Mistral workflow runtime restricts `os.environ` access inside the workflow class. All env var reads must happen inside `@workflows.activity()` functions. See `get_credentials()` in `workflow.py`.
- **Discovery contract:** New workflows must be placed in a subpackage under `src/workflows/`. The class must be decorated with `@workflows.workflow.define(...)` — the discoverer scans for `__workflows_workflow_def`.
- **Atomic writes:** `workflow.py` (`save_results`), `downloader.py`, and `extractor.py` all use temp-file-then-rename for atomic output. Keep this pattern if adding new output paths.
- **Pagination convention:** Page 1 uses the base URL with no query params. Page 2+ appends `?PAGEN_1=<n>`. Hardcoded in `config.py:BASE_URL` and `_build_page_url()`.
- **CSS selector:** `.news-item` finds dividend entries. If the target site changes markup, update `config.py:SELECTOR` and the Cloudflare API payload in `client.py`.
- **Concurrency limits:** `downloader.py` and `extractor.py` each have their own concurrency limit (`MAX_CONCURRENT_DOWNLOADS`, `MAX_CONCURRENT_EXTRACTS`) defined in `config.py`. Conversion concurrency is `MAX_CONCURRENT_CONVERSIONS` in `config.py`.
- **Conversion split:** Non-PDF files (docx, doc, xls) are converted locally via `python-docx`, `docx2txt`, `xlrd`. PDF files are uploaded to Cloudflare R2 via `r2_storage.py`, then the public R2 URL is passed to Mistral OCR (`mistralai_ocr`) which requires `MISTRAL_API_KEY`.
- **R2 upload contract:** `r2_storage.py` uses `aioboto3` for async S3-compatible uploads. Requires `AWS_S3_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` env vars. Bucket defaults to `tokenbel`, region defaults to `auto`. Returns a public URL on the `pub-...r2.dev` domain.

## Safe change rules

- **Adding a new workflow:** Create a new package under `src/workflows/<name>/` with a class decorated with `@workflows.workflow.define(...)`. It will be auto-discovered by `discover.py`.
- **Modifying parsing logic:** Edit `parser.py`. If you also need the standalone scraper to reflect the change, update `example.py` as well.
- **Changing API interaction:** Edit `client.py`. Do not change retry/timeout constants directly — use `config.py`.
- **Changing download behavior:** Edit `downloader.py`. Concurrency and retry tuning lives in `config.py`.
- **Changing extraction behavior:** Edit `extractor.py`. Supports ZIP, TAR, GZ, TGZ, TAR.GZ. New archive types should be added to the detection logic here.
- **Changing conversion behavior:** Edit `converter.py`. Non-PDF types are handled in `convert_to_markdown()`. PDF OCR uses `mistralai_ocr` from the Mistral plugin — PDFs are read as base64 data URIs and passed directly to the OCR API. New file types should be added to the extension dispatch in `convert_to_markdown()`.
- **Changing output schema:** Edit `models.py` first, then update `workflow.py` (serializes `WorkflowOutput`), `parser.py` (produces `CompanyResult`), and any downstream consumers.
- **Do not edit `.agents/`** — those are read-only Mistral SDK reference materials.

## Validation

```bash
# Install dependencies
uv sync

# Lint
uv run ruff check src/
uv run ruff format src/ --check

# Auto-fix
uv run ruff check --fix src/
uv run ruff format src/

# Start dev worker (auto-reloads on .py changes)
make start-worker

# Execute workflow
make execute workflow=centraldepo-parser input='{"max_pages": 2}'
```

## Nearby docs

- `README.md` — project setup, commands, data model, troubleshooting
- `.agents/skills/workflows/SKILL.md` — Mistral Workflows SDK reference
- Root `AGENTS.md` — workspace-wide conventions and ruff config
- Root `ARCHITECTURE.md` — full code map, logical layers, data flow, invariants
