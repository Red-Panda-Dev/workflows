# Architecture

## 1. High-Level Overview

This repository is a Python workspace containing a data-pipeline project that automates downloading and processing dividend disclosure records from the Belarusian securities regulator (`epfr.gov.by`). The pipeline fetches paginated records from a REST API, downloads and normalizes binary file content (archives, office documents, PDFs), OCRs PDFs via Mistral AI, AI-distills structured dividend data, and exports share-payout records matched against a reference CSV.

The system is built on the **Mistral AI Workflows SDK** (`mistralai-workflows`). Each pipeline stage is an independently deployable workflow — a durable, activity-based unit of work managed by the Mistral runtime. Workflows are auto-discovered at worker startup and invoked via a CLI client or externally.

**Evidence:** `pyproject.toml` (root, `epfr-downloader/`), `epfr-downloader/src/discover.py`, `epfr-downloader/src/workflows/epfr/workflow.py`, `epfr-downloader/Dockerfile`, `README.md`.

- **Business purpose** (`Inferred` from `epfr-downloader/pyproject.toml` description and `epfr.gov.by` API usage): automate retrieval and enrichment of dividend disclosure filings from the Belarusian EPFR regulator.
- **Stack**: Python 3.14.3, `uv` package manager, `mistralai-workflows` runtime, `aiohttp` HTTP client, `pydantic` models, Docker deployment.
- **Two `uv` environments**: root `.venv` holds linting/type-check tooling only; `epfr-downloader/.venv` holds runtime dependencies (`Observed` in root `pyproject.toml` and `epfr-downloader/pyproject.toml`).

## 2. System Architecture (Logical)

Five logical components, arranged as a linear pipeline with sequential data-flow dependencies:

```
┌─────────────┐    ┌──────────────┐    ┌───────────┐    ┌──────────────┐    ┌──────────────────┐
│  API Client  │───>│ File         │───>│ PDF OCR   │───>│ AI Distiller │───>│ Share Payout     │
│  + Detector  │    │ Processing   │    │ Converter │    │              │    │ Exporter         │
└─────────────┘    └──────────────┘    └───────────┘    └──────────────┘    └──────────────────┘
       │                  │                                                          │
       v                  v                                                          v
  epfr.gov.by       output/<UNP>/       Mistral OCR        Mistral Large      shares_source_data.csv
  REST API          unp_file_mapping.json  API              chat API            share_payouts_by_unp.json
                                          ai_distilled_dividends.json
```

### Components

1. **API Client & Discovery** — Fetches paginated records from `epfr.gov.by`, downloads raw binary files, detects file types by magic bytes. Entrypoint: `client.py`, `detector.py`.
2. **File Processing Pipeline** (`epfr-files-downloader` workflow) — Extracts archives, converts office documents to Markdown, produces `unp_file_mapping.json`. Entrypoint: `workflow.py`.
3. **PDF OCR Converter** (`epfr-pdf-ocr-converter` workflow) — OCRs PDF entries from the mapping via Mistral OCR, updates mapping to reference `.md` files. Entrypoint: `pdf_ocr_workflow.py`.
4. **AI Distiller** (`epfr-ai-distiller` workflow) — Extracts structured dividend records from Markdown via Mistral Large, produces `ai_distilled_dividends.json`. Entrypoint: `ai_distiller_workflow.py`.
5. **Share Payout Exporter** (`epfr-share-payout-exporter` workflow) — Joins distilled dividends with a share reference CSV, produces `share_payouts_by_unp.json`. Entrypoint: `share_payout_exporter_workflow.py`.

### Boundaries

- **Each workflow is independently invocable.** They communicate only through filesystem artifacts (JSON mapping files, Markdown files) — not through shared memory or direct calls (`Observed` in code: no cross-workflow imports).
- **The Mistral runtime sandbox** restricts `os.environ` access inside workflow classes. All env var reads must happen inside `@workflows.activity()` functions (`Observed` in `epfr-downloader/AGENTS.md` and `discover.py`).
- **No external state stores.** All pipeline state lives in the filesystem under `output/` and in JSON artifacts. No database, no message queue (`Observed` — no persistence deps in `epfr-downloader/pyproject.toml`).
- **Tooling is separated from runtime.** Root `pyproject.toml` has only `ruff` + `ty`; all runtime deps live in `epfr-downloader/pyproject.toml` (`Observed`).

### Dependency direction

```
workflow modules → config.py, models.py (leaf modules)
workflow modules → Mistral SDK, aiohttp, pydantic
No workflow module imports another workflow module
```

Leaf modules with no inbound business imports: `config.py`, `models.py`, `detector.py`, `extractor.py`, `markdown_cleanup.py` (`Observed` in `epfr-downloader/src/workflows/epfr/AGENTS.md`).

## 3. Code Map (Physical)

```text
workflows/                              # Repository root
├── pyproject.toml                      # Root project: ruff + ty tooling config only
├── Makefile                            # lint, refactor, type-check (delegates to epfr-downloader)
├── shares_source_data.csv              # Share reference input for payout exporter
├── ARCHITECTURE.md                     # This file
├── AGENTS.md                           # Workspace-level contributor guidance
├── README.md                           # Setup, commands, workflow overview
│
├── .github/workflows/tests.yml         # CI: install, lint, test, coverage upload
├── .github/dependabot.yml              # Weekly pip + GH Actions dependency updates
│
└── epfr-downloader/                    # EPFR pipeline project (own uv env)
    ├── pyproject.toml                  # Runtime deps: mistralai-workflows, aiohttp, pydantic, etc.
    ├── Makefile                        # start-worker, execute, lint, test, docker-build/run
    ├── Dockerfile                      # Multi-stage build; CMD = discover.py
    ├── generate_sql.py                 # Standalone: share_payouts JSON → SQL INSERTs
    ├── AGENTS.md                       # Project-local module map and change rules
    │
    └── src/
        ├── discover.py                 # Auto-discovers workflow classes, starts Mistral worker
        ├── tests/                      # Unit tests (13+ modules + fixtures/)
        └── workflows/
            ├── __init__.py             # Package marker (discovery entry point)
            ├── start.py                # CLI to trigger workflow execution via Mistral client
            └── epfr/                   # All 4 workflow definitions + shared modules
                ├── AGENTS.md           # Pipeline internals, data contracts, activity boundaries
                ├── config.py           # All constants: API URLs, concurrency, retry, model names
                ├── models.py           # Pydantic models for all 4 workflows
                ├── client.py           # aiohttp API client (fetch pages, download files)
                ├── detector.py         # Magic-byte file extension detection
                ├── extractor.py        # Archive extraction (ZIP, TAR, GZ) + OOXML detection
                ├── converter.py        # Document-to-Markdown conversion (docx/doc/xls/xlsx)
                ├── markdown_cleanup.py # Token-heavy markdown artifact removal
                ├── workflow.py         # epfr-files-downloader workflow (5 activities)
                ├── pdf_ocr.py          # PDF OCR via Mistral plugin
                ├── pdf_ocr_workflow.py # epfr-pdf-ocr-converter workflow (3 activities)
                ├── ai_distiller.py     # AI structured extraction logic
                ├── ai_distiller_workflow.py  # epfr-ai-distiller workflow (4 activities)
                ├── share_payout_exporter.py  # CSV join + export logic
                ├── share_payout_exporter_workflow.py  # epfr-share-payout-exporter workflow (4 activities)
                └── prompts/
                    └── dividends_parsing.md  # Prompt template for AI distillation
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
| Unit tests | `epfr-downloader/src/tests/` |
| Ruff / ty configuration | `pyproject.toml` (root) |
| Docker image definition | `epfr-downloader/Dockerfile` |
| SQL generation script | `epfr-downloader/generate_sql.py` |
| Share reference CSV | `shares_source_data.csv` (root) |

## 4. Life of a Request / Primary Data Flow

This is a **data pipeline** (worker/event-processor shape), not a web service. The primary execution path:

### Worker startup (always-on)

```
make start-worker (from epfr-downloader/)
  → uv run python src/discover.py
       - load_dotenv()            # reads .env for MISTRAL_API_KEY
       - discover_workflows()     # scans src/workflows/ for __workflows_workflow_def
       - workflows.run_worker()   # long-running: polls Mistral runtime for jobs
```

### Trigger

```
make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'
  → start.py → Mistral runtime → EpfrFilesDownloader.run()
```

### Main pipeline: `epfr-files-downloader`

```
start.py → Mistral runtime → EpfrFilesDownloader.run()
  → fetch_all_pages          (paginate epfr.gov.by API → list[EpfrRecord])
  → download_all_epfr_files  (download binary files → output/<UNP>/)
  → extract_all_epfr_archives (extract ZIP/TAR/GZ, detect OOXML-in-ZIP)
  → convert_all_epfr_files    (docx/doc/xls/xlsx → .md, cleanup source)
  → save_unp_mapping          (write output/unp_file_mapping.json, atomic)
```

### Downstream pipelines (invoked separately)

```
epfr-pdf-ocr-converter (3 activities):
  scan_pdf_entries → process_pdf_ocr → finalize_ocr_mapping
  read unp_file_mapping.json → OCR each PDF via Mistral → update mapping to .md

epfr-ai-distiller (4 activities):
  scan_ai_distiller_files → process_ai_distillation → finalize_ai_distillation + retry_failed
  read unp_file_mapping.json → Mistral Large structured extraction → ai_distilled_dividends.json

epfr-share-payout-exporter (4 activities):
  scan_share_payout_export → process_share_payout_matching → finalize_share_payout_export + retry_failed
  read ai_distilled_dividends.json + shares_source_data.csv → share_payouts_by_unp.json
```

### Standalone post-processing

```
generate_sql.py: share_payouts_by_unp.json → share_dividends_insert.sql
```

### Data artifact flow

```
epfr.gov.by API → output/<UNP>/*.{pdf,docx,...}
                → output/unp_file_mapping.json
                → output/<UNP>/*.md
                → output/ai_distilled_dividends.json
                → output/share_payouts_by_unp.json
                → output/share_dividends_insert.sql (via generate_sql.py)
```

All JSON outputs use atomic writes (`tempfile.mkstemp()` → `os.replace()`) (`Observed` in `workflow.py`, `pdf_ocr_workflow.py`, `ai_distiller_workflow.py`, `share_payout_exporter_workflow.py`).

## 5. Architectural Invariants & Constraints

- **Rule:** All `os.environ` / `.env` reads must occur inside `@workflows.activity()` functions, never inside workflow class methods.
  - **Rationale:** The Mistral Workflows runtime sandboxes workflow classes and restricts direct environment access.
  - **Enforcement / Signals (Observed):** Runtime failure if violated; `discover.py` calls `load_dotenv()` before starting worker.

- **Rule:** New workflow classes must be decorated with `@workflows.workflow.define(...)` and located in a package under `src/workflows/`.
  - **Rationale:** `discover.py` registers workflows by scanning for `__workflows_workflow_def` attribute.
  - **Enforcement / Signals (Observed):** `discover.py:scan_package()` recursively imports and inspects classes.

- **Rule:** Output JSON writes must remain atomic using `tempfile.mkstemp()` → write → `os.replace()`.
  - **Rationale:** Prevents partially written artifacts on failures.
  - **Enforcement / Signals (Observed):** Pattern used in all four workflow entry modules.

- **Rule:** Use correct virtual environments — root `.venv` for tooling, `epfr-downloader/.venv` for runtime.
  - **Rationale:** Separate dependency sets (`ruff`/`ty` vs `mistralai-workflows`/runtime libs).
  - **Enforcement / Signals (Observed):** Separate `pyproject.toml` and `uv.lock` files at each level.

- **Rule:** Workflows communicate only through filesystem artifacts (JSON/Markdown files in `output/`). No workflow module imports another workflow module.
  - **Rationale:** Each workflow is independently deployable and restartable.
  - **Enforcement / Signals (Observed):** No cross-workflow imports in the codebase; data contracts are JSON files on disk.

- **Rule:** All configuration constants belong in `config.py`, not hardcoded in modules.
  - **Rationale:** Single source of truth for API URLs, retry limits, concurrency, model names.
  - **Enforcement / Signals (Inferred):** Convention observed across all modules; no linter enforces this.

- **Rule:** Data schema changes require updating `models.py` first, then affected workflow and consumer modules.
  - **Rationale:** Pydantic models define all I/O contracts; breaking changes ripple to AI prompts and tests.
  - **Enforcement / Signals (Inferred):** Convention per `epfr-downloader/AGENTS.md`.

- **Rule:** EPFR API pagination is 0-based. `FIRST_PAGE_NO = 0`.
  - **Rationale:** Upstream API convention; wrong offset produces duplicate or missing records.
  - **Enforcement / Signals (Observed):** Constant in `config.py`; documented in `epfr-downloader/AGENTS.md`.

- **Rule:** The EPFR API returns raw binary content with no filename. File type must be detected via magic bytes (`detector.py`), not from any header or URL.
  - **Rationale:** API design constraint — no `Content-Disposition` or filename hints.
  - **Enforcement / Signals (Observed):** `detector.py` inspects first bytes; files saved as `<record_id><detected_ext>`.

- **Rule:** Files are organized by UNP (tax ID) in `output/<UNP>/`. UNP comes from `rec.holder.unp` with fallback to `rec.organization.unp`.
  - **Rationale:** Business-key-based folder layout for downstream consumption.
  - **Enforcement / Signals (Observed):** `_get_unp()` in `client.py`; folder creation in download logic.

- **Rule:** Linting must pass (`make lint`) before commit. Ruff config: line-length 120, rules `F E W I D B UP C4 SIM PIE T20`, ignores `E501 E712`.
  - **Rationale:** Code quality gate enforced in CI.
  - **Enforcement / Signals (Observed):** `pyproject.toml` `[tool.ruff]` section; `.github/workflows/tests.yml` runs `make lint`.

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

- **Global architecture docs:** Cross-project boundaries, layer definitions, invariants that span multiple modules.
- **Local AGENTS.md:** Module-specific APIs, internal data contracts, safe change rules, test coverage notes.
- **Code comments / docstrings:** Function-level behavior, parameter semantics, edge cases (see `.skills/python_docs_and_comments.md` for Google-style docstring policy).
