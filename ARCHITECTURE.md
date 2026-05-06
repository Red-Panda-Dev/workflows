# Architecture

## 1. High-Level Overview

This repository is a Python monorepo containing two independent background-worker projects that download and process dividend disclosure records published by Belarusian financial regulators. Each project is a standalone Mistral AI Workflows application: it runs a long-lived worker process that polls the Mistral Workflows runtime for jobs, executes durable pipeline steps (activities), and writes structured output to the local filesystem.

The two projects are fully decoupled — they share no code, no runtime environment, and no data paths. They happen to share the same repository root and a common root-level ruff/ty toolchain configuration. (Observed: separate `pyproject.toml`, `uv.lock`, and `.venv` trees under `centraldepo-parser/` and `epfr-downloader/`; no cross-imports.)

**Evidence anchors:** `centraldepo-parser/pyproject.toml`, `epfr-downloader/pyproject.toml`, `centraldepo-parser/src/discover.py`, `epfr-downloader/src/discover.py`, `pyproject.toml` (root, linting-only), `Makefile` (root, centraldepo-only lint targets).

## 2. System Architecture (Logical)

The repository contains five logical layers, mirrored in each project:

```
[Mistral Workflows Runtime]
        │  dispatches jobs / receives results
        ▼
[Worker Process]  ← src/discover.py
  auto-discovers workflow classes, calls workflows.run_worker()
        │
        ▼
[Workflow Orchestration]  ← src/workflows/<project>/workflow.py (+ *._workflow.py)
  defines durable workflow class; sequences activities
        │  calls
        ▼
[Activities]  ← @workflows.activity() decorated async functions
  stateless async functions; contain all I/O and env-var access
        │
        ▼
[Support Layer]  ← client.py, parser.py, extractor.py, converter.py,
                   ai_distiller.py, pdf_ocr.py, config.py, models.py, common.py
  HTTP clients, file parsers, format converters, Pydantic models, config
```

**Component responsibilities:**

- **Mistral Workflows Runtime**: external orchestration plane; provides durable execution, retries, and job dispatch. Neither project controls this boundary.
- **Worker Process** (`discover.py`): scans `src/workflows/` for classes decorated with `@workflows.workflow.define(...)`, then starts `workflows.run_worker()`. This is the only process entrypoint.
- **Workflow Orchestration** (`workflow.py` and `*_workflow.py`): defines top-level pipeline classes with an `async def run()` entrypoint method. Sequences activity calls. Must not access `os.environ` directly (runtime sandbox constraint).
- **Activities**: units of retryable work annotated with `@workflows.activity()`. All environment variable reads, network I/O, and filesystem operations must live here.
- **Support Layer**: pure helpers — HTTP clients (`aiohttp`), HTML/document parsers, format converters (docx/xls/pdf → markdown), Pydantic data models, AI distillation via Mistral API, PDF OCR via Mistral OCR.

**Boundary:** `centraldepo-parser` and `epfr-downloader` do not import from each other. This boundary is physically enforced by separate virtual environments.

### Per-project pipeline shape

**`centraldepo-parser`** — split into two separately invocable workflows:
1. `centraldepo-collect-assets`: scrape `centraldepo.by` pages → download disclosure files → extract archives → save `centraldepo_dividends.json`.
2. `centraldepo-distill-dividends`: load that JSON → convert files to markdown → AI-distill structured dividend data via Mistral API → write final JSON.

**`epfr-downloader`** — three independently invocable workflows:
1. `epfr-files-downloader`: paginate `epfr.gov.by` REST API → download files → extract archives → convert documents → emit `unp_file_mapping.json`.
2. `epfr-pdf-ocr-converter`: OCR-convert PDFs in mapped output to markdown, update the mapping. (Observed: `pdf_ocr_workflow.py`)
3. `epfr-ai-distiller`: extract structured dividend data from mapped markdown files via Mistral AI. (Observed: `ai_distiller_workflow.py`)

## 3. Code Map (Physical)

```
workflows/                          # Repository root
├── pyproject.toml                  # Root project: ruff + ty config only (linting-only deps)
├── Makefile                        # lint/refactor/type-check — centraldepo-parser only
├── AGENTS.md                       # Repo-level contributor guide and change rules
├── ARCHITECTURE.md                 # This file
│
├── centraldepo-parser/             # Project 1: CentralDepo scraper + AI distiller
│   ├── pyproject.toml              # Runtime deps: mistralai-workflows, aiohttp, docx, xlrd, bs4
│   ├── Makefile                    # start-worker, execute-collect-assets, execute-distill-dividends, execute-pipeline
│   ├── AGENTS.md                   # Project-local module map and invariants
│   ├── README.md                   # Setup, commands, data model (note: some file refs are stale)
│   └── src/
│       ├── discover.py             # Worker entrypoint: auto-discovers + starts Mistral worker
│       └── workflows/
│           ├── start.py            # CLI trigger: send workflow execution request
│           └── centraldepo/
│               ├── workflow.py     # Two workflow classes: CollectAssets + DistillDividends
│               ├── activities.py   # All @workflows.activity() decorated pipeline steps
│               ├── client.py       # aiohttp HTTP client for centraldepo.by
│               ├── parser.py       # HTML scraping logic (beautifulsoup4 / lxml)
│               ├── downloader.py   # File download utilities
│               ├── extractor.py    # Archive extraction
│               ├── converter.py    # docx/xls/pdf → markdown conversion
│               ├── ai_distiller.py # Mistral API calls for structured data extraction
│               ├── common.py       # Shared helpers (URL building, result loading)
│               ├── config.py       # Constants and tuning knobs
│               ├── models.py       # Pydantic data models
│               └── prompts/        # Markdown prompt templates for AI distillation
│                   └── dividends_parsing.md
│
├── epfr-downloader/                # Project 2: EPFR API downloader + AI distiller
│   ├── pyproject.toml              # Runtime deps: mistralai-workflows, aiohttp, pydantic, openpyxl
│   ├── Makefile                    # start-worker, execute, lint, test
│   ├── AGENTS.md                   # Project-local module map and invariants
│   └── src/
│       ├── discover.py             # Worker entrypoint (identical pattern to centraldepo)
│       └── workflows/
│           ├── start.py            # CLI trigger
│           └── epfr/
│               ├── workflow.py             # EpfrFilesDownloader: fetch → download → extract → convert → mapping
│               ├── pdf_ocr_workflow.py     # epfr-pdf-ocr-converter: OCR PDFs → markdown, update mapping
│               ├── ai_distiller_workflow.py # epfr-ai-distiller: structured dividend extraction from MD
│               ├── client.py               # aiohttp client for epfr.gov.by REST API
│               ├── detector.py             # Magic-byte file type detection
│               ├── extractor.py            # Archive extraction (ZIP/TAR/GZ) + OOXML detection
│               ├── converter.py            # docx/xls/xlsx → markdown conversion
│               ├── pdf_ocr.py              # PDF OCR via Mistral, updates mapping entries
│               ├── ai_distiller.py         # Mistral API structured extraction
│               ├── config.py               # Constants and tuning knobs
│               ├── models.py               # Pydantic data models
│               ├── prompts/                # Prompt templates for AI distillation
│               │   └── dividends_parsing.md
│               └── tests/                  # Unit tests (only epfr-downloader has test coverage)
│                   ├── test_ai_distiller.py
│                   ├── test_client.py
│                   ├── test_converter.py
│                   ├── test_detector.py
│                   ├── test_extractor.py
│                   ├── test_models.py
│                   └── test_pdf_ocr.py
```

**Output artifacts** (gitignored):
- `centraldepo-parser/output/` — per-company hash-named subdirectories with downloaded/converted files; `centraldepo_dividends.json`, `ai_distilled.json`, `final_mapping.json`.
- `epfr-downloader/` output dir (configurable via input) — per-UNP directories; `unp_file_mapping.json`, distilled output.

## 4. Life of a Request / Primary Data Flow

### Worker startup (both projects)

```
make start-worker
  └─► uv run python src/discover.py
        ├─ load_dotenv()            # reads .env for MISTRAL_API_KEY
        ├─ discover_workflows()     # scans src/workflows/ for __workflows_workflow_def
        └─ workflows.run_worker()   # long-running: polls Mistral runtime for jobs
```

### Triggering a workflow run

```
make execute input='{"max_pages": 5}'
  └─► uv run python src/workflows/start.py --workflow <name> --input '{...}'
        └─ submits execution request to Mistral Workflows runtime
```

### centraldepo-parser pipeline (two-phase)

**Phase 1 — collect-assets:**
```
CentralDepoCollectAssetsWorkflow.run()
  ├─ scrape_pages_batch()                # HTTP GET centraldepo.by pages → DividendRecord list
  ├─ save_results()                      # write centraldepo_dividends.json
  ├─ download_all_results_files()        # HTTP GET each disclosure file
  └─ extract_all_downloaded_archives()   # unzip/unpack archives
```

**Phase 2 — distill-dividends** (consumes Phase 1 output):
```
CentralDepoDistillDividendsWorkflow.run()
  ├─ convert_all_downloaded_files()      # docx/xls/pdf → markdown
  ├─ run_ai_data_distillation()          # Mistral API: extract structured dividends
  ├─ save_distillation_results()         # write ai_distilled.json
  └─ generate_final_json()               # write final_mapping.json
```

### epfr-downloader pipeline (three workflows, typically sequential)

**Workflow 1 — epfr-files-downloader:**
```
EpfrFilesDownloader.run()
  ├─ fetch_all_pages()                   # paginate epfr.gov.by REST API → list[EpfrRecord]
  ├─ download_all_epfr_files()           # HTTP GET each file → output/<UNP>/
  ├─ extract_all_epfr_archives()         # unpack archives, detect OOXML
  ├─ convert_all_epfr_files()            # docx/xls/xlsx → markdown
  └─ save_unp_mapping()                  # write unp_file_mapping.json (atomic)
```

**Workflow 2 — epfr-pdf-ocr-converter** (consumes Workflow 1 output):
```
EpfrPdfOcrWorkflow.run()
  └─ ocr_epfr_mapping_pdfs()            # PDF → markdown via Mistral OCR, update mapping
```

**Workflow 3 — epfr-ai-distiller** (consumes Workflow 1/2 output):
```
EpfrAiDistillerWorkflow.run()
  └─ distill_epfr_dividends()           # Mistral API structured extraction from MD files
```

## 5. Architectural Invariants & Constraints

- **Rule:** All `os.environ` / `.env` reads must occur inside `@workflows.activity()` functions, never inside workflow class methods.
  - **Rationale:** The Mistral Workflows runtime sandboxes workflow classes and restricts direct environment access. Violating this causes silent failures or runtime errors.
  - **Enforcement / Signals (Observed):** Enforced by runtime. Config modules (`config.py`) are imported inside activities, not at workflow class level. `AGENTS.md` documents this constraint explicitly.

- **Rule:** No cross-project imports. `centraldepo-parser` and `epfr-downloader` must not import from each other.
  - **Rationale:** They are independent projects with separate virtual environments, dependency sets, and deployment lifecycles.
  - **Enforcement / Signals (Observed):** Separate `uv.lock` and `.venv` trees make cross-import physically impossible at runtime.

- **Rule:** New workflow classes must be placed in a subpackage under `src/workflows/` and decorated with `@workflows.workflow.define(...)`.
  - **Rationale:** `discover.py` scans for the `__workflows_workflow_def` attribute set by that decorator. Workflows placed elsewhere will not be registered with the worker.
  - **Enforcement / Signals (Observed):** Auto-discovery logic in `src/discover.py` (both projects).

- **Rule:** The root `Makefile` lint and type-check targets cover `centraldepo-parser/` only. `epfr-downloader/` must be linted via its own `Makefile`.
  - **Rationale:** The root Makefile hardcodes `centraldepo-parser/` as the lint target.
  - **Enforcement / Signals (Observed):** Root `Makefile` lint commands explicitly pass `centraldepo-parser/` as path argument.

- **Rule:** `epfr-downloader` changes require running `make test` before committing; `centraldepo-parser` has no automated tests.
  - **Rationale:** epfr-downloader includes unit tests under `src/workflows/epfr/tests/`; centraldepo-parser does not.
  - **Enforcement / Signals (Observed):** `epfr-downloader/Makefile` has a `test` target; centraldepo-parser's Makefile does not.

- **Rule:** Each project must be run using its own `uv` virtual environment. Running `uv run` from the wrong directory uses the wrong env.
  - **Rationale:** Three isolated environments exist: root `.venv` (linting only), `centraldepo-parser/.venv` (runtime), `epfr-downloader/.venv` (runtime).
  - **Enforcement / Signals (Observed):** Separate `uv.lock` at each project root; `make start-worker` targets use relative `uv run` from within the project directory.

- **Rule:** Atomic writes (temp file + rename) must be used for all output JSON files.
  - **Rationale:** Pipeline output files may be consumed by subsequent workflow phases or external tools; partial writes cause data corruption.
  - **Enforcement / Signals (Observed):** `tempfile.mkstemp` + `os.replace` pattern used in `save_unp_mapping()`, `save_results()`, `save_distillation_results()`, `generate_final_json()`.

- **Rule:** Ruff line-length is 120 characters; rules `F E W I D B UP C4 SIM PIE T20` with `E501 E712` ignored. Both projects must conform.
  - **Rationale:** Consistent style across the monorepo; enforced via lint targets.
  - **Enforcement / Signals (Observed):** Root `pyproject.toml` `[tool.ruff]` section; both project `pyproject.toml` files either inherit or restate compatible config.

- **Rule:** Do not edit files under `.agents/` in either project.
  - **Rationale:** These are read-only Mistral SDK references.
  - **Enforcement / Signals (Observed):** Documented in `AGENTS.md`; no build target modifies `.agents/`.

- **Rule:** Pipeline stage constants (concurrency limits, retry counts, timeouts, URLs) live in `config.py` only. Do not hardcode them elsewhere.
  - **Rationale:** Single source of truth for tuning knobs; changes propagate consistently.
  - **Enforcement / Signals (Observed):** Both projects have a `config.py` that centralizes all such constants. `AGENTS.md` files document this convention.

## 6. Documentation Strategy

`ARCHITECTURE.md` (this file) is the global map: it documents the monorepo boundary, the two-project structure, shared conventions, dependency direction, primary data flows, and invariants that span or govern both projects.

Module-level and project-level docs cover local detail:

- `AGENTS.md` (root) — contributor guide, change rules, validation commands, and gotchas for the whole repo.
- `centraldepo-parser/AGENTS.md` — project-local module map, activity boundary rules, and change checklist.
- `centraldepo-parser/src/workflows/centraldepo/AGENTS.md` — pipeline internals, data contracts between pipeline stages, activity boundary details, and per-file editing rules.
- `centraldepo-parser/README.md` — setup, commands, data model, troubleshooting. Note: references `example.py` and `dev_worker.py` which no longer exist; treat `src/workflows/centraldepo/` as authoritative.
- `epfr-downloader/AGENTS.md` — EPFR project module map, change rules, and invariants.

**What belongs where:**

- `ARCHITECTURE.md`: cross-cutting structure, dependency direction, invariants, and the "where is X?" map.
- Project `AGENTS.md` / `README.md`: per-project setup, commands, module-level responsibilities, and local conventions.
- Pipeline-level `AGENTS.md` (e.g., `centraldepo/AGENTS.md`): data contracts between pipeline stages, activity-level boundaries, and pipeline-specific gotchas.
