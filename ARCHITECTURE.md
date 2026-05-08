# Architecture

## 1. High-Level Overview

This repository is a Python workspace for one background-worker project that downloads and processes dividend disclosure records from `epfr.gov.by`. The project is a standalone Mistral AI Workflows application: it runs a long-lived worker process that polls the Mistral Workflows runtime for jobs, executes durable pipeline steps (activities), and writes structured output to the local filesystem.

The runtime code lives entirely under `epfr-downloader/`. The repository root provides shared tooling configuration (`ruff` and `ty`) and automation helpers (`Makefile`, CI).

**Evidence anchors:** `epfr-downloader/pyproject.toml`, `epfr-downloader/src/discover.py`, `pyproject.toml` (root, linting/type tooling), `Makefile` (root).

## 2. System Architecture (Logical)

The project follows five logical layers:

```
[Mistral Workflows Runtime]
        |  dispatches jobs / receives results
        v
[Worker Process]  <- src/discover.py
  auto-discovers workflow classes, calls workflows.run_worker()
        |
        v
[Workflow Orchestration]  <- src/workflows/epfr/workflow.py (+ *_workflow.py)
  defines durable workflow class; sequences activities
        |  calls
        v
[Activities]  <- @workflows.activity() decorated async functions
  stateless async functions; contain all I/O and env-var access
        |
        v
[Support Layer]  <- client.py, detector.py, extractor.py, converter.py,
                   pdf_ocr.py, ai_distiller.py, config.py, models.py
  HTTP client, parsers/converters, OCR/AI integrations, schemas, config
```

### Component responsibilities

- **Mistral Workflows Runtime**: external orchestration plane; durable execution, retries, and job dispatch.
- **Worker Process** (`discover.py`): scans `src/workflows/` for classes decorated with `@workflows.workflow.define(...)`, then starts `workflows.run_worker()`.
- **Workflow Orchestration** (`workflow.py`, `pdf_ocr_workflow.py`, `ai_distiller_workflow.py`): defines top-level pipeline classes with `async def run()` entrypoints and sequences activity calls.
- **Activities**: retryable units of work annotated with `@workflows.activity()`. All environment-variable reads, network I/O, and filesystem writes must live here.
- **Support Layer**: pure helpers for API calls, file-type detection, archive extraction, format conversion, OCR, AI distillation, and Pydantic modeling.

## 3. Code Map (Physical)

```text
workflows/                          # Repository root
|-- pyproject.toml                  # Root project: ruff + ty config only (tooling deps)
|-- Makefile                        # lint/refactor/type-check for epfr-downloader
|-- AGENTS.md                       # Repo-level contributor guide and change rules
|-- ARCHITECTURE.md                 # This file
|-- .github/                        # Repo automations and CI workflows
|
`-- epfr-downloader/                # EPFR API downloader + OCR + AI distiller
    |-- pyproject.toml              # Runtime deps: mistralai-workflows, aiohttp, pydantic, openpyxl
    |-- Makefile                    # start-worker, execute, lint, test
    |-- AGENTS.md                   # Project-local module map and invariants
    `-- src/
        |-- discover.py             # Worker entrypoint: auto-discovers + starts Mistral worker
        `-- workflows/
            |-- start.py            # CLI trigger
            `-- epfr/
                |-- workflow.py              # epfr-files-downloader: fetch -> download -> extract -> convert -> mapping
                |-- pdf_ocr_workflow.py      # epfr-pdf-ocr-converter: OCR PDFs -> markdown, update mapping
                |-- ai_distiller_workflow.py # epfr-ai-distiller: structured dividend extraction from markdown
                |-- client.py                # aiohttp client for epfr.gov.by REST API
                |-- detector.py              # Magic-byte file type detection
                |-- extractor.py             # Archive extraction + OOXML detection
                |-- converter.py             # docx/doc/xls/xlsx -> markdown conversion
                |-- pdf_ocr.py               # PDF OCR via Mistral
                |-- ai_distiller.py          # Mistral API structured extraction
                |-- config.py                # Constants and tuning knobs
                |-- models.py                # Pydantic data models
                |-- prompts/
                |   `-- dividends_parsing.md
                `-- tests/
                    |-- test_ai_distiller.py
                    |-- test_client.py
                    |-- test_converter.py
                    |-- test_detector.py
                    |-- test_extractor.py
                    |-- test_models.py
                    `-- test_pdf_ocr.py
```

## 4. Life of a Request / Primary Data Flow

### Worker startup

```
make start-worker
  -> uv run python src/discover.py
       - load_dotenv()            # reads .env for MISTRAL_API_KEY
       - discover_workflows()     # scans src/workflows/ for __workflows_workflow_def
       - workflows.run_worker()   # long-running: polls Mistral runtime for jobs
```

### Triggering a workflow run

```
make execute input='{"max_pages": 5}'
  -> uv run python src/workflows/start.py --workflow <name> --input '{...}'
       -> submits execution request to Mistral Workflows runtime
```

### EPFR pipelines

**Workflow 1 - epfr-files-downloader:**
```
EpfrFilesDownloader.run()
  - fetch_all_pages()            # paginate epfr.gov.by REST API -> list[EpfrRecord]
  - download_all_epfr_files()    # HTTP GET each file -> output/<UNP>/
  - extract_all_epfr_archives()  # unpack archives, detect OOXML
  - convert_all_epfr_files()     # docx/xls/xlsx/doc -> markdown
  - save_unp_mapping()           # write unp_file_mapping.json (atomic)
```

**Workflow 2 - epfr-pdf-ocr-converter:**
```
EpfrPdfOcrWorkflow.run()
  - ocr_epfr_mapping_pdfs()      # PDF -> markdown via Mistral OCR, update mapping
```

**Workflow 3 - epfr-ai-distiller:**
```
EpfrAiDistillerWorkflow.run()
  - distill_epfr_dividends()     # Mistral API structured extraction from markdown
```

## 5. Architectural Invariants & Constraints

- **Rule:** All `os.environ` / `.env` reads must occur inside `@workflows.activity()` functions, never inside workflow class methods.
  - **Rationale:** The Mistral Workflows runtime sandboxes workflow classes and restricts direct environment access.
- **Rule:** New workflow classes must be in a package under `src/workflows/` and decorated with `@workflows.workflow.define(...)`.
  - **Rationale:** `discover.py` registers workflows by scanning for `__workflows_workflow_def`.
- **Rule:** Root `Makefile` targets operate on `epfr-downloader/`.
  - **Rationale:** Root automation is a convenience wrapper for project lint/refactor/type-check flows.
- **Rule:** EPFR changes should pass `make lint` and `make test` in `epfr-downloader/`.
  - **Rationale:** Tests are maintained under `src/workflows/epfr/tests/`.
- **Rule:** Use correct environments: root `.venv` for tooling, `epfr-downloader/.venv` for runtime dependencies.
  - **Rationale:** They have separate dependency sets and lockfiles.
- **Rule:** Output JSON writes must remain atomic (`tempfile` + `os.replace`).
  - **Rationale:** Prevents partially written artifacts.

## 6. Documentation Strategy

`ARCHITECTURE.md` (this file) is the global map: repository structure, dependency direction, and cross-cutting invariants.

Local docs:

- `AGENTS.md` (root) - contributor guide, change rules, validation commands.
- `epfr-downloader/AGENTS.md` - project-local module map and constraints.
- `epfr-downloader/src/workflows/epfr/AGENTS.md` - pipeline internals, stage contracts, activity boundaries.
- `README.md` - setup and usage quick reference.
