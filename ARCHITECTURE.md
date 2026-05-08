# Architecture

## 1. High-Level Overview

This repository is a Python workspace for the EPFR pipeline — a set of Mistral AI Workflows that download and process dividend disclosure records from the Belarusian securities regulator API at `epfr.gov.by`. The system runs as a long-lived worker process that polls the Mistral Workflows runtime for jobs, executes durable pipeline activities, and writes structured output to the local filesystem.

The core purpose (Observed from `README.md`, `epfr-downloader/pyproject.toml`) is to automate extraction, normalization, and AI-powered distillation of dividend data from government disclosures. The pipeline ingests raw binary files (PDFs, Office documents, archives), converts them to Markdown, applies Mistral AI for structured data extraction, and produces JSON artifacts ready for database import.

The runtime code lives entirely under `epfr-downloader/`. The repository root provides shared tooling configuration (`ruff`, `ty`) and automation helpers (`Makefile`, CI).

**Evidence anchors:** `epfr-downloader/pyproject.toml` (runtime deps including `mistralai-workflows`), `epfr-downloader/src/discover.py` (worker bootstrap), `pyproject.toml` (root, linting/type tooling), `Makefile` (root), `README.md`.

## 2. System Architecture (Logical)

The project follows five logical layers:

```
[Mistral Workflows Runtime]   (external SaaS)
        |  dispatches jobs / receives results
        v
[Worker Process]  <- src/discover.py
   auto-discovers workflow classes, starts workflows.run_worker()
        |
        v
[Workflow Orchestration]  <- workflow.py, pdf_ocr_workflow.py,
                             ai_distiller_workflow.py, share_payout_exporter_workflow.py
   defines durable workflow classes; sequences activities
        |  calls
        v
[Activities]  <- @workflows.activity() decorated async functions
   stateless async functions; contain all I/O and env-var access
        |
        v
[Support Layer]  <- client.py, detector.py, extractor.py, converter.py,
                    pdf_ocr.py, ai_distiller.py, share_payout_exporter.py,
                    config.py, models.py, markdown_cleanup.py
   HTTP client, parsers/converters, OCR/AI integrations, schemas, config
```

### Component responsibilities

| Layer | Location | Responsibility |
|-------|----------|----------------|
| **Mistral Workflows Runtime** | External | Durable execution, retries, job dispatch |
| **Worker Process** | `discover.py` | Scans `src/workflows/` for `@workflows.workflow.define(...)` classes, starts `workflows.run_worker()` |
| **Workflow Orchestration** | `*_workflow.py` files | Top-level pipeline classes with `async def run()` entrypoints; sequences activity calls |
| **Activities** | `@workflows.activity()` | Retryable units of work; all env-var reads, network I/O, and filesystem writes |
| **Support Layer** | Pure helpers | API calls, file-type detection, archive extraction, format conversion, OCR, AI distillation, Pydantic models |

### Dependency direction

- Workflows depend on activities (inline in same file)
- Activities depend on support-layer modules
- Support-layer modules depend on `config.py` and `models.py`
- No circular imports; `config.py`, `models.py`, `detector.py`, `extractor.py`, `markdown_cleanup.py` are leaf modules

### Architectural boundaries

- **Workflow sandbox:** `os.environ` access is forbidden inside workflow classes — all env var reads must happen inside activity functions
- **Discovery contract:** Workflows must be decorated with `@workflows.workflow.define(...)` to be auto-discovered via `__workflows_workflow_def` attribute
- **Environment separation:** Root `.venv` contains tooling (`ruff`, `ty`); `epfr-downloader/.venv` contains runtime dependencies

## 3. Code Map (Physical)

```text
workflows/                              # Repository root
├── pyproject.toml                      # Root project: ruff + ty config (tooling-only deps)
├── Makefile                            # lint/refactor/type-check for epfr-downloader/
├── AGENTS.md                           # Repo-level contributor guide and change rules
├── ARCHITECTURE.md                     # This file
├── README.md                           # Setup and usage quick reference
├── shares_source_data.csv              # Share reference data for payout export
├── .github/                            # CI workflows and repo automations
│
└── epfr-downloader/                    # EPFR pipeline: download → OCR → AI distill → export
    ├── pyproject.toml                  # Runtime deps: mistralai-workflows, aiohttp, pydantic, etc.
    ├── Makefile                        # start-worker, execute, execute-share-payout-exporter, lint, test
    ├── Dockerfile                      # Container image for worker deployment
    ├── AGENTS.md                       # Project-local module map and invariants
    ├── generate_sql.py                 # Standalone: share_payouts_by_unp.json → SQL INSERT statements
    ├── output/                         # Downloaded files, mapping JSON, distilled JSON (gitignored)
    └── src/
        ├── discover.py                 # Worker entrypoint: auto-discovers + starts Mistral worker
        └── workflows/
            ├── __init__.py             # Package marker for auto-discovery
            ├── start.py                # CLI trigger for workflow execution
            └── epfr/                   # EPFR pipeline package (4 workflows)
                ├── AGENTS.md           # Pipeline internals, data contracts, activity boundaries
                ├── workflow.py                      # epfr-files-downloader (5 activities)
                ├── pdf_ocr_workflow.py              # epfr-pdf-ocr-converter (1 activity)
                ├── ai_distiller_workflow.py         # epfr-ai-distiller (1 activity)
                ├── share_payout_exporter_workflow.py # epfr-share-payout-exporter (1 activity)
                ├── client.py                        # aiohttp client for epfr.gov.by REST API
                ├── detector.py                      # Magic-byte file type detection
                ├── extractor.py                     # Archive extraction + OOXML detection
                ├── converter.py                     # docx/doc/xls/xlsx → Markdown conversion
                ├── markdown_cleanup.py              # Token-heavy markdown artifact removal
                ├── pdf_ocr.py                       # PDF OCR via Mistral OCR plugin
                ├── ai_distiller.py                  # Mistral Large structured extraction
                ├── share_payout_exporter.py         # CSV join + payout export
                ├── config.py                        # Constants and tuning knobs
                ├── models.py                        # Pydantic data models (all workflows)
                ├── prompts/
                │   └── dividends_parsing.md         # AI prompt template for dividend extraction
                └── tests/                           # Unit tests (7 modules)
```

### Where is X?

| What | Location |
|------|----------|
| Worker bootstrap | `epfr-downloader/src/discover.py` |
| Workflow trigger CLI | `epfr-downloader/src/workflows/start.py` |
| Main download pipeline | `epfr-downloader/src/workflows/epfr/workflow.py` |
| PDF OCR workflow | `epfr-downloader/src/workflows/epfr/pdf_ocr_workflow.py` |
| AI distillation workflow | `epfr-downloader/src/workflows/epfr/ai_distiller_workflow.py` |
| Share payout export workflow | `epfr-downloader/src/workflows/epfr/share_payout_exporter_workflow.py` |
| API constants / tuning | `epfr-downloader/src/workflows/epfr/config.py` |
| All Pydantic models | `epfr-downloader/src/workflows/epfr/models.py` |
| Ruff / ty configuration | `pyproject.toml` (root) |
| Docker image definition | `epfr-downloader/Dockerfile` |
| SQL generation script | `epfr-downloader/generate_sql.py` |
| Share reference CSV | `shares_source_data.csv` (root) |

## 4. Life of a Request / Primary Data Flow

### Worker startup

```
make start-worker (from epfr-downloader/)
  → uv run python src/discover.py
       - load_dotenv()            # reads .env for MISTRAL_API_KEY
       - discover_workflows()     # scans src/workflows/ for __workflows_workflow_def
       - workflows.run_worker()   # long-running: polls Mistral runtime for jobs
```

### Triggering a workflow run

```
make execute input='{"max_pages": 5}'
  → uv run python src/workflows/start.py --workflow <name> --input '{...}'
       → submits execution request to Mistral Workflows runtime
```

### Four EPFR pipelines

**Workflow 1 — `epfr-files-downloader`** (entry: `workflow.py`)
```
EpfrFilesDownloader.run()
  → fetch_all_pages()            # paginate epfr.gov.by REST API → list[EpfrRecord]
  → download_all_epfr_files()    # HTTP GET each file → output/<UNP>/
  → extract_all_epfr_archives()  # unpack ZIP/TAR/GZ, detect OOXML-in-ZIP
  → convert_all_epfr_files()     # docx/doc/xls/xlsx → Markdown
  → save_unp_mapping()           # write unp_file_mapping.json (atomic)
```

**Workflow 2 — `epfr-pdf-ocr-converter`** (entry: `pdf_ocr_workflow.py`)
```
EpfrPdfOcrConverter.run()
  → ocr_epfr_mapping_pdfs()      # PDF → Markdown via Mistral OCR, update mapping
```

**Workflow 3 — `epfr-ai-distiller`** (entry: `ai_distiller_workflow.py`)
```
EpfrAiDistillerWorkflow.run()
  → distill_epfr_dividends()     # Mistral Large structured extraction → ai_distilled_dividends.json
```

**Workflow 4 — `epfr-share-payout-exporter`** (entry: `share_payout_exporter_workflow.py`)
```
EpfrSharePayoutExporterWorkflow.run()
  → export_share_payouts()       # join distilled JSON with CSV → share_payouts_by_unp.json
```

### Data artifact flow

```
epfr.gov.by API
      ↓
output/<UNP>/*.pdf, *.docx, ...   (raw files)
      ↓
output/unp_file_mapping.json      (file registry with lineage)
      ↓
output/<UNP>/*.md                 (after OCR/conversion)
      ↓
output/ai_distilled_dividends.json (structured dividend data)
      ↓
output/share_payouts_by_unp.json  (DB-ready payout records)
      ↓
generate_sql.py → share_dividends_insert.sql (optional SQL export)
```

## 5. Architectural Invariants & Constraints

- **Rule:** All `os.environ` / `.env` reads must occur inside `@workflows.activity()` functions, never inside workflow class methods.
  - **Rationale:** The Mistral Workflows runtime sandboxes workflow classes and restricts direct environment access.
  - **Enforcement / Signals (Observed):** Runtime failure if violated; `discover.py` calls `load_dotenv()` before starting worker.

- **Rule:** New workflow classes must be decorated with `@workflows.workflow.define(...)` and located in a package under `src/workflows/`.
  - **Rationale:** `discover.py` registers workflows by scanning for `__workflows_workflow_def` attribute.
  - **Enforcement / Signals (Observed):** `discover.py:scan_package()` recursively imports and inspects classes.

- **Rule:** Output JSON writes must remain atomic using `tempfile.mkstemp()` → write → `os.replace()`.
  - **Rationale:** Prevents partially written artifacts on failures.
  - **Enforcement / Signals (Observed):** Pattern used in `workflow.py`, `pdf_ocr.py`, `ai_distiller.py`, `share_payout_exporter.py`.

- **Rule:** Use correct virtual environments — root `.venv` for tooling, `epfr-downloader/.venv` for runtime.
  - **Rationale:** They have separate dependency sets (`ruff`/`ty` vs `mistralai-workflows`/runtime libs).
  - **Enforcement / Signals (Observed):** Separate `pyproject.toml` and `uv.lock` files at each level.

- **Rule:** EPFR changes must pass `make lint` and `make test` in `epfr-downloader/`.
  - **Rationale:** Tests cover client, detector, extractor, converter, models, AI distiller.
  - **Enforcement / Signals (Observed):** Makefile targets; CI checks.

- **Rule:** All configuration constants belong in `config.py`, not hardcoded in modules.
  - **Rationale:** Single source of truth for API URLs, retry limits, concurrency, model names.
  - **Enforcement / Signals (Inferred):** Convention observed; no linter enforces this.

- **Rule:** Data schema changes require updating `models.py` first, then affected workflow and consumer modules.
  - **Rationale:** Pydantic models define all I/O contracts; breaking changes ripple to AI prompts and tests.
  - **Enforcement / Signals (Inferred):** Convention per `epfr-downloader/AGENTS.md`.

- **Rule:** Pagination in EPFR API client starts at page 0, not page 1.
  - **Rationale:** EPFR API uses 0-based page numbers.
  - **Enforcement / Signals (Observed):** `FIRST_PAGE_NO = 0` in `config.py`.

- **Rule:** Files are organized by UNP (tax ID) in `output/<UNP>/` folders.
  - **Rationale:** UNP is the stable company identifier from the API.
  - **Enforcement / Signals (Observed):** `_get_unp()` in `client.py`; folder structure in all workflows.

- **Rule:** PDF OCR, AI distillation, and share export are separate workflows from the main download pipeline.
  - **Rationale:** Allows independent execution, different retry policies, and phased processing.
  - **Enforcement / Signals (Observed):** Four distinct workflow classes with separate entry modules.

## 6. Documentation Strategy

`ARCHITECTURE.md` (this file) is the global map: repository structure, logical layers, dependency direction, data flow, and cross-cutting invariants.

### Local documentation hierarchy

| Path | Scope |
|------|-------|
| `AGENTS.md` (root) | Repo-level contributor guide, validation commands, change rules |
| `epfr-downloader/AGENTS.md` | Project-local module map, invariants, make targets |
| `epfr-downloader/src/workflows/epfr/AGENTS.md` | Pipeline internals, activity boundaries, data contracts, per-file editing rules |
| `README.md` | Setup and usage quick reference |

### What belongs where

- **Global architecture docs:** Cross-project boundaries, layer definitions, invariants that span multiple modules
- **Local AGENTS.md / README.md:** Module-specific APIs, internal data contracts, safe change rules, test coverage notes
- **Code comments / docstrings:** Function-level behavior, parameter semantics, edge cases
