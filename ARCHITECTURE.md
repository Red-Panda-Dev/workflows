# Architecture

## 1. High-Level Overview

This repository is a Python workspace that hosts a Mistral AI Workflows project for scraping, downloading, extracting, converting, and AI-distilling dividend disclosure records from `centraldepo.by`. The system uses the Cloudflare Browser Rendering API to fetch and parse paginated HTML, groups records by company, downloads archive files (ZIP/TAR/GZ), extracts their contents, converts documents to Markdown (docx/doc/xls via Python libs, PDF via Mistral OCR as base64 data URI), runs AI distillation with Mistral Large to extract structured dividend data, and saves results to JSON (`Observed` — `centraldepo-parser/src/workflows/centraldepo/workflow.py`, `centraldepo-parser/README.md`).

The system runs as a background worker process orchestrated by the Mistral Workflows SDK (`mistralai-workflows`). The worker registers workflow definitions with Mistral AI Studio and polls for execution tasks. A separate CLI trigger submits workflow runs to the Mistral platform, which dispatches them to the local worker (`Observed` — `centraldepo-parser/src/discover.py:57-64`, `centraldepo-parser/src/workflows/start.py:38-73`).

The workspace has two isolated `uv` environments: a root project that provides workspace-wide linting via ruff, and a child project (`centraldepo-parser/`) that holds all runtime code and its own dependencies (`Observed` — root `pyproject.toml`, `centraldepo-parser/pyproject.toml`, separate `uv.lock` and `.venv` at each level).

Evidence anchors: `centraldepo-parser/pyproject.toml`, `centraldepo-parser/src/discover.py`, `centraldepo-parser/src/workflows/centraldepo/workflow.py`, `centraldepo-parser/src/workflows/start.py`, root `pyproject.toml`, root `Makefile`.

## 2. System Architecture (Logical)

Four logical layers:

```
┌─────────────────────────────────────────────────┐
│  Worker Infrastructure                           │
│  discover.py                                     │
│  (auto-discovery, process lifecycle)             │
├─────────────────────────────────────────────────┤
│  Workflow Definitions                            │
│  workflows/<name>/workflow.py                    │
│  (Mistral workflow + activities)                 │
├─────────────────────────────────────────────────┤
│  Domain Logic                                    │
│  client.py · parser.py · models.py · config.py   │
│  downloader.py · extractor.py · converter.py     │
│  ai_distiller.py · prompts/                      │
│  (HTTP, parsing, data shapes, file I/O, AI)      │
├─────────────────────────────────────────────────┤
│  External Integrations                           │
│  Cloudflare Browser Rendering API                │
│  Mistral AI Studio (workflow orchestration + OCR)│
└─────────────────────────────────────────────────┘
```

- **Worker Infrastructure** scans `src/workflows/` for classes with `__workflows_workflow_def` attribute, registers them, and starts polling via `workflows.run_worker`. Depends on: the Mistral SDK. Does not depend on: domain modules under `centraldepo/` (`Observed` — `discover.py:36`).
- **Workflow Definitions** are self-contained packages under `src/workflows/`. Each package owns its activities (functions decorated with `@workflows.activity()`) and orchestration. Depends on: Domain Logic within the same package. Does not depend on: other workflow packages (`Inferred` — only one workflow package exists; no cross-package imports observed).
- **Domain Logic** handles HTTP communication (`client.py`), HTML parsing (`parser.py`), Pydantic models (`models.py`), configuration constants (`config.py`), file downloading (`downloader.py`), archive extraction (`extractor.py`), document-to-Markdown conversion (`converter.py`), and AI distillation (`ai_distiller.py`, `prompts/`). Dependency graph (all `Observed` from import statements):
  ```
  config.py    ← (leaf, no intra-package imports)
  models.py    ← (leaf, no intra-package imports)
  downloader.py ← (leaf, only stdlib + aiohttp)
  parser.py    ← config.py, models.py
  client.py    ← config.py, models.py, parser.py
  extractor.py ← downloader.py
  converter.py ← config.py, downloader.py, mistralai plugins
  ai_distiller.py ← config.py, models.py
  workflow.py  ← all domain modules
  ```
  No circular imports exist.
- **External Integrations** — the Cloudflare Browser Rendering API (scraping target) and Mistral AI Studio (workflow scheduling + OCR). Not represented by local code; consumed via HTTP and the SDK client respectively.

## 3. Code Map (Physical)

```
workflows/                          # Repository root
├── pyproject.toml                  # Root project: ruff config (line-length 120, rules F E W I D B UP C4 SIM PIE T20)
├── Makefile                        # Workspace-wide lint/refactor/type-check targets against centraldepo-parser/
├── AGENTS.md                       # Workspace-level conventions and validation commands
├── .skills/                        # Shared coding policy docs
│   └── python_docs_and_comments.md # Comment/docstring conventions
├── .agents/skills/                 # SDK skill references (shared across workspace)
│   ├── modern-python/
│   └── workflows/
│
└── centraldepo-parser/             # Main project — all application code
    ├── pyproject.toml              # Dependencies: mistralai-workflows, pydantic, aiohttp, etc.
    ├── Makefile                    # start-worker, execute, installdeps targets
    ├── AGENTS.md                   # Detailed module map and local change rules
    ├── README.md                   # Project setup, commands, data model, troubleshooting
    ├── uv.lock                     # Project-specific lockfile
    └── src/
        ├── discover.py             # Scans src/workflows/ for workflow classes, starts worker
        └── workflows/
            ├── __init__.py         # Package root for auto-discovery
            ├── start.py            # CLI to trigger a workflow execution via Mistral client
            └── centraldepo/        # CentralDepo workflow implementation
                ├── config.py       # Constants: BASE_URL, SCRAPE_API, SELECTOR, defaults, retry, AI config
                ├── models.py       # Pydantic models: DividendRecord, ScrapeResult, WorkflowInput/Output, AI models
                ├── parser.py       # HTML parsing: extract records, group by company
                ├── client.py       # Cloudflare Browser Rendering HTTP client with retries and circuit breaker
                ├── downloader.py   # Concurrent file download with retry/backoff per company
                ├── extractor.py    # Archive extraction (ZIP, TAR, GZ, TGZ) with atomic writes
                ├── converter.py    # Document-to-Markdown: docx/doc/xls via Python libs, PDF via Mistral OCR
                ├── ai_distiller.py # AI distillation: Mistral Large structured dividend data extraction
                ├── prompts/        # Prompt templates for AI distillation
                │   └── dividends_parsing.md  # Main extraction prompt
                ├── AGENTS.md       # Pipeline internals, data contracts, activity boundaries, per-file editing rules
                └── workflow.py     # Mistral workflow class + activities (entry point)
```

Where is X?

| X | Location |
|---|----------|
| Lint/format config | Root `pyproject.toml` `[tool.ruff]` |
| Runtime dependencies | `centraldepo-parser/pyproject.toml` |
| New workflow | New subpackage under `centraldepo-parser/src/workflows/<name>/` |
| Scraping logic | `centraldepo-parser/src/workflows/centraldepo/client.py`, `parser.py` |
| Workflow entry point | `workflow.py` class with `@workflows.workflow.entrypoint` |
| Worker start | `centraldepo-parser/src/discover.py` |
| Workflow trigger CLI | `centraldepo-parser/src/workflows/start.py` |
| File download logic | `centraldepo-parser/src/workflows/centraldepo/downloader.py` |
| Archive extraction | `centraldepo-parser/src/workflows/centraldepo/extractor.py` |
| Document conversion | `centraldepo-parser/src/workflows/centraldepo/converter.py` |
| AI distillation | `centraldepo-parser/src/workflows/centraldepo/ai_distiller.py` |
| AI prompt template | `centraldepo-parser/src/workflows/centraldepo/prompts/dividends_parsing.md` |
| Data shapes (Pydantic) | `centraldepo-parser/src/workflows/centraldepo/models.py` |
| Tuning constants | `centraldepo-parser/src/workflows/centraldepo/config.py` |

## 4. Life of a Request / Primary Data Flow

**Workflow execution path (triggered via Mistral platform):**

1. **Trigger** — `centraldepo-parser/src/workflows/start.py` parses CLI args (`--workflow`, `--input`), loads `.env`, calls Mistral client to submit a workflow run (`Observed` — `start.py:44-73`).
2. **Dispatch** — Mistral AI Studio routes the execution to the local worker (`Inferred` — `start-worker` target in `centraldepo-parser/Makefile:8-9`, `discover.py` calls `workflows.run_worker`).
3. **Discovery** — `centraldepo-parser/src/discover.py` scanned `src/workflows/` at worker startup, found `CentralDepoWorkflow` via `__workflows_workflow_def` attribute, and registered it (`Observed` — `discover.py:36`).
4. **Workflow entry** — `CentralDepoWorkflow.run()` receives `WorkflowInput` (`Observed` — `workflow.py:501-502`).
5. **Credentials** — `get_credentials()` activity reads `CF_ACCOUNT_ID` and `CF_API_TOKEN` from environment (sandbox restriction forces this into an activity) (`Observed` — `workflow.py:110-131`).
6. **Pagination loop** — Pages processed in batches via `scrape_pages_batch()` activity → `CloudflareSessionManager` → `CloudflareClient._make_request()` → HTTP POST to Cloudflare Browser Rendering API → `parse_items()` extracts `DividendRecord` objects from response (`Observed` — `workflow.py:59-107`, `client.py:26-27`).
7. **Transform** — `transform_to_output()` groups records by company name into `CompanyResult` objects, deduplicates URLs (`Observed` — `parser.py`).
8. **Persist** — `save_results()` activity writes JSON atomically (temp file + rename) to `output/` (`Observed` — `workflow.py:134-178`).
9. **Download** — `download_all_results_files()` activity downloads all archive files to MD5-named company folders via `downloader.py` with concurrent streaming and atomic writes (`Observed` — `workflow.py:181-221`).
10. **Extract** — `extract_all_downloaded_archives()` activity extracts ZIP/TAR/GZ archives via `extractor.py`, renaming extracted files to lowercase and removing the archive (`Observed` — `workflow.py:224-265`).
11. **Convert** — `convert_all_downloaded_files()` activity converts documents to Markdown: non-PDF via `converter.py` (python-docx, docx2txt, xlrd), PDF via Mistral OCR as base64 data URI (`Observed` — `workflow.py:338-389`). Source files are cleaned up after successful conversion (`CLEANUP_SOURCE_FILES` env var).
12. **AI Distillation** — `run_ai_data_distillation()` activity processes all MD files through `ai_distiller.py` using Mistral Large `chat.parse_async` with `DividendData` Pydantic model for structured output. Results saved to `ai_distilled.json` (`Observed` — `workflow.py:392-428`).
13. **Final mapping** — `generate_final_json()` activity creates `final_mapping.json` mapping company folder hashes to company info and MD file paths (`Observed` — `workflow.py:268-335`).
14. **Return** — `WorkflowOutput` with results list, stats, download_stats, extraction_stats, conversion_stats, and distillation_stats returned to the Mistral platform.

## 5. Architectural Invariants & Constraints

- **Rule:** All environment variable reads must occur inside `@workflows.activity()` functions, not in the workflow class body.
  - **Rationale:** The Mistral workflow runtime restricts `os.environ` access in the workflow sandbox.
  - **Enforcement / Signals (Observed):** `get_credentials()` in `workflow.py:110-131` is an activity specifically for this purpose. `convert_all_downloaded_files` reads `CLEANUP_SOURCE_FILES` inside the activity body.

- **Rule:** New workflows must be placed in a subpackage under `src/workflows/` with a class decorated by `@workflows.workflow.define(...)`.
  - **Rationale:** Auto-discovery in `discover.py` scans for the `__workflows_workflow_def` attribute on classes.
  - **Enforcement / Signals (Observed):** `discover.py:36` checks `hasattr(obj, "__workflows_workflow_def")`.

- **Rule:** Workflow packages must not import from sibling workflow packages.
  - **Rationale:** Each workflow is independently discovered and should be self-contained.
  - **Enforcement / Signals (Inferred):** No cross-package imports observed in `centraldepo/`; convention enforced by isolation, not a build check.

- **Rule:** JSON and file output must use atomic writes (temp file + rename).
  - **Rationale:** Prevents partial/corrupt output on crash.
  - **Enforcement / Signals (Observed):** `workflow.py` (`save_results`, `save_distillation_results`, `generate_final_json`), `downloader.py`, and `extractor.py` all use `tempfile.mkstemp` + `os.replace`.

- **Rule:** Retry/timeout/concurrency constants must live in `config.py`, not be hardcoded elsewhere.
  - **Rationale:** Centralizes tuning knobs for scraping and AI behavior.
  - **Enforcement / Signals (Observed):** `config.py` exports all constants; `client.py`, `ai_distiller.py`, `converter.py` import them. Convention, not mechanically enforced.

- **Rule:** `config.py` and `models.py` must remain leaf modules with no intra-package imports.
  - **Rationale:** Prevents circular dependencies; these modules define shared types and constants consumed by all domain modules.
  - **Enforcement / Signals (Observed):** Neither file contains relative imports from the `centraldepo` package.

- **Rule:** Two separate `uv` environments — root for tooling, `centraldepo-parser/` for runtime.
  - **Rationale:** Linting dependencies (ruff) are isolated from runtime dependencies (mistralai-workflows, aiohttp).
  - **Enforcement / Signals (Observed):** Separate `pyproject.toml`, `uv.lock`, and `.venv` at each level.

- **Rule:** Files under `centraldepo-parser/.agents/` and root `.agents/skills/` are read-only.
  - **Rationale:** Contains Mistral SDK reference materials that should not be customized per project.
  - **Enforcement / Signals (Inferred):** Stated in `centraldepo-parser/AGENTS.md`; no mechanical enforcement.

- **Rule:** Company folder naming uses `hashlib.md5(name.lower())` defined in `downloader.py:get_company_folder_name()`.
  - **Rationale:** Consistent, filesystem-safe folder names across all pipeline stages.
  - **Enforcement / Signals (Observed):** `extractor.py`, `converter.py`, `ai_distiller.py`, and `workflow.py` all import `get_company_folder_name` from `downloader.py`.

- **Rule:** All linting passes via `make lint` from the repo root before committing.
  - **Rationale:** Consistent code style enforced at workspace level.
  - **Enforcement / Signals (Observed):** Root `Makefile` defines `lint` target running ruff against `centraldepo-parser/`.

- **Rule:** Pipeline activities in `workflow.py` delegate business logic to domain modules, not implement it inline.
  - **Rationale:** Separation of orchestration from domain logic keeps activities testable and the workflow class a thin coordinator.
  - **Enforcement / Signals (Inferred):** Convention visible in all activity implementations — they call into domain modules and handle serialization/logging only.

## 6. Documentation Strategy

`ARCHITECTURE.md` (this file) is the global map and invariants document for the repository. It describes the physical layout, logical layers, data flow, and architectural rules that span the entire workspace.

Module-level detail lives in:
- `AGENTS.md` (root) — workspace-wide conventions, ruff config, validation commands.
- `centraldepo-parser/AGENTS.md` — detailed module map, local change rules, safe modification guidance.
- `centraldepo-parser/src/workflows/centraldepo/AGENTS.md` — pipeline internals, data contracts, activity boundaries, per-file editing rules.
- `centraldepo-parser/README.md` — project setup, commands, data model, and troubleshooting.

SDK reference:
- `centraldepo-parser/.agents/skills/workflows/SKILL.md` — Mistral Workflows SDK reference (read-only).

Comment/docstring conventions:
- `.skills/python_docs_and_comments.md` — policy for public APIs, handlers, and non-trivial async workflows.

No tests directory or test documentation exists yet (`Inferred` — `pytest` is listed as a dev dependency in `centraldepo-parser/pyproject.toml` but no test files are present).

**Doc discrepancy note:** `centraldepo-parser/AGENTS.md` and `centraldepo-parser/README.md` reference `example.py` (standalone scraper) and `dev_worker.py` (file watcher), but neither file exists on disk. The pipeline documentation in `centraldepo-parser/src/workflows/centraldepo/AGENTS.md` is the most up-to-date and accurate source for module internals.
