# AGENTS.md

## Scope

The CentralDepo Dividend Parser — a Mistral Workflow that scrapes paginated dividend disclosure records from `centraldepo.by` using direct HTTP requests with aiohttp, groups them by company, downloads archive files (ZIP/TAR/GZ), extracts their contents, converts documents to Markdown (non-PDF locally, PDF via Mistral OCR as base64 data URI), runs AI distillation with Mistral Large to extract structured dividend data, and saves results to JSON.

## What lives here

```text
src/
├── discover.py                # Scans workflows/ for workflow classes, starts Mistral worker
└── workflows/
    ├── __init__.py            # Empty package marker (auto-discovery entry point)
    ├── start.py               # CLI to trigger workflow execution via Mistral client
    └── centraldepo/           # Core workflow package
        ├── config.py          # Constants: BASE_URL, SELECTOR, retry/concurrency/AI defaults
        ├── models.py          # Pydantic models: DividendRecord, ScrapeResult, WorkflowInput/Output, AI models
        ├── parser.py          # HTML parsing: extract records, group by company
        ├── client.py          # HTTP client with aiohttp, BeautifulSoup, retries and circuit breaker
        ├── downloader.py      # Concurrent file download with retry/backoff per company
        ├── extractor.py       # Archive extraction (ZIP, TAR, GZ, TGZ) with atomic writes
        ├── converter.py       # Document-to-Markdown: docx/doc/xls via Python libs, PDF via Mistral OCR
        ├── ai_distiller.py    # AI distillation: Mistral Large structured extraction of dividend data
        ├── common.py          # Shared orchestration helpers (URL building, atomic writes, result loading)
        ├── activities.py      # Shared @workflows.activity() decorated pipeline steps
        ├── prompts/           # Prompt templates for AI distillation
        │   └── dividends_parsing.md  # Main extraction prompt (BYN currency rules, schema definition)
        └── workflow.py        # Mistral workflow + activities (orchestration entry point)
output/                        # JSON output + downloaded/extracted/converted files (gitignored)
```

## Local boundaries and invariants

- **Workflow sandbox:** The Mistral workflow runtime restricts `os.environ` access inside the workflow class. All env var reads must happen inside `@workflows.activity()` functions.
- **Discovery contract:** New workflows must be placed in a subpackage under `src/workflows/`. The class must be decorated with `@workflows.workflow.define(...)` — the discoverer scans for `__workflows_workflow_def`.
- **Atomic writes:** `workflow.py` (`save_results`), `downloader.py`, and `extractor.py` all use temp-file-then-rename for atomic output. Keep this pattern if adding new output paths.
- **Pagination convention:** Page 1 uses the base URL with no query params. Page 2+ appends `?PAGEN_1=<n>`. Hardcoded in `config.py:BASE_URL` and `_build_page_url()`.
- **CSS selector:** `.news-item` finds dividend entries. If the target site changes markup, update `config.py:SELECTOR`.
- **Concurrency limits:** Each pipeline stage has its own concurrency limit (`MAX_CONCURRENT_DOWNLOADS`, `MAX_CONCURRENT_EXTRACTS`, `MAX_CONCURRENT_CONVERSIONS`) defined in `config.py`.
- **Conversion split:** Non-PDF files (docx, doc, xls) are converted locally. PDF files are read as base64 data URIs and passed to Mistral OCR, which requires `MISTRAL_API_KEY`. No intermediate storage or R2 upload.
- **AI distillation:** All MD files are processed through `ai_distiller.py` using Mistral Large structured parsing (`chat.parse_async` with `DividendData` Pydantic model). Prompt template at `prompts/dividends_parsing.md`. Sequential processing to avoid rate limits.

## Safe change rules

- **Adding a new workflow:** Create a new package under `src/workflows/<name>/` with a class decorated with `@workflows.workflow.define(...)`. It will be auto-discovered by `discover.py`.
- **Modifying parsing logic:** Edit `parser.py`.
- **Changing HTTP scraping:** Edit `client.py`. Do not change retry/timeout constants directly — use `config.py`.
- **Changing download behavior:** Edit `downloader.py`. Concurrency and retry tuning lives in `config.py`.
- **Changing extraction behavior:** Edit `extractor.py`. Supports ZIP, TAR, GZ, TGZ, TAR.GZ. New archive types should be added to the detection logic here.
- **Changing conversion behavior:** Edit `converter.py`. Non-PDF types are handled in `convert_to_markdown()`. PDF OCR uses `mistralai_ocr` from the Mistral plugin. New file types should be added to the extension dispatch in `convert_to_markdown()`.
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

# Type-check (from repo root)
make type-check

# Start dev worker (auto-reloads on .py changes)
make start-worker

# Execute workflows
make execute-collect-assets input='{"max_pages": 2}'
make execute-distill-dividends input='{"input_path": "output/centraldepo_dividends.json"}'
```

## Nearby docs

- `README.md` — project setup, commands, data model, troubleshooting
- `src/workflows/centraldepo/AGENTS.md` — pipeline internals, data contracts, activity boundaries, per-file editing rules
- Root `AGENTS.md` — workspace-wide conventions and ruff config
- Root `ARCHITECTURE.md` — full code map, logical layers, data flow, invariants
