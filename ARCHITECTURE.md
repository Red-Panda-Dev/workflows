# Architecture

## 1. High-Level Overview

This repository is a Python workspace that hosts a Mistral AI Workflows project for scraping dividend disclosure records from `centraldepo.by`. The scraper uses the Cloudflare Browser Rendering API to fetch and parse paginated HTML, then groups the extracted records by company and persists them as JSON (`Observed` — see `centraldepo-parser/src/workflows/centraldepo/workflow.py`).

The system runs as a background worker process orchestrated by the Mistral Workflows SDK. The worker registers workflow definitions with Mistral AI Studio and polls for execution tasks. A separate CLI trigger submits workflow runs to the Mistral platform, which dispatches them to the local worker (`Observed` — `centraldepo-parser/src/discover.py`, `centraldepo-parser/src/workflows/start.py`).

The workspace has two isolated `uv` environments: a root project that provides workspace-wide linting via ruff, and a child project (`centraldepo-parser/`) that holds all runtime code and its own dependencies (`Observed` — root `pyproject.toml`, `centraldepo-parser/pyproject.toml`).

Evidence anchors: `centraldepo-parser/pyproject.toml`, `centraldepo-parser/src/discover.py`, `centraldepo-parser/src/workflows/centraldepo/workflow.py`, `centraldepo-parser/src/workflows/start.py`, root `pyproject.toml`, root `Makefile`.

## 2. System Architecture (Logical)

Four logical layers:

```
┌─────────────────────────────────────────────────┐
│  Worker Infrastructure                           │
│  discover.py · dev_worker.py                     │
│  (auto-discovery, process lifecycle)             │
├─────────────────────────────────────────────────┤
│  Workflow Definitions                            │
│  workflows/<name>/workflow.py                    │
│  (Mistral workflow + activities)                 │
├─────────────────────────────────────────────────┤
│  Domain Logic                                    │
│  client.py · parser.py · models.py · config.py   │
│  (HTTP, HTML parsing, data shapes, constants)    │
├─────────────────────────────────────────────────┤
│  External Integrations                           │
│  Cloudflare Browser Rendering API                │
│  Mistral AI Studio (workflow orchestration)      │
└─────────────────────────────────────────────────┘
```

- **Worker Infrastructure** scans `src/workflows/` for classes decorated with `@workflows.workflow.define(...)`, registers them, and starts polling. It knows nothing about specific workflow logic. Depends on: the Mistral SDK. Does not depend on: domain modules (`centraldepo/`).
- **Workflow Definitions** are self-contained packages under `src/workflows/`. Each package owns its activities (functions decorated with `@workflows.activity()`) and orchestration. Depends on: Domain Logic within the same package. Does not depend on: other workflow packages.
- **Domain Logic** handles HTTP communication (`client.py`), HTML parsing (`parser.py`), Pydantic models (`models.py`), and configuration constants (`config.py`). Depends on: `config.py`, `models.py`, external libraries (`aiohttp`, `pydantic`). Does not depend on: the Mistral SDK.
- **External Integrations** — the Cloudflare Browser Rendering API (scraping target) and Mistral AI Studio (workflow scheduling). Not represented by local code; consumed via HTTP and the SDK client respectively.

Additionally, `example.py` is a standalone CLI scraper that mirrors the workflow's domain logic without the Mistral runtime. It bypasses all workflow infrastructure (`Observed` — `centraldepo-parser/example.py`).

## 3. Code Map (Physical)

```
workflows/                          # Repository root
├── pyproject.toml                  # Root project: ruff config (line-length 120, rules F E W I)
├── Makefile                        # Workspace-wide lint/refactor targets against centraldepo-parser/
├── AGENTS.md                       # Workspace-level conventions and validation commands
├── uv.lock                         # Root lockfile (ruff only)
│
└── centraldepo-parser/             # Main project — all application code
    ├── pyproject.toml              # Dependencies: mistralai-workflows, pydantic, aiohttp, etc.
    ├── Makefile                    # start-worker, execute, installdeps targets
    ├── AGENTS.md                   # Detailed module map and local change rules
    ├── example.py                  # Standalone async scraper (non-workflow, duplicates domain logic)
    ├── output/                     # JSON output directory (gitignored)
    ├── .agents/                    # Mistral SDK reference materials (read-only, do not modify)
    └── src/
        ├── discover.py             # Scans src/workflows/ for workflow classes, starts worker
        ├── dev_worker.py           # File watcher (watchdog) that restarts discover.py on .py changes
        └── workflows/
            ├── __init__.py         # Package root for auto-discovery
            ├── start.py            # CLI to trigger a workflow execution via Mistral client
            └── centraldepo/        # CentralDepo workflow implementation
                ├── __init__.py
                ├── config.py       # Constants: BASE_URL, SCRAPE_API, SELECTOR, defaults, retry config
                ├── models.py       # Pydantic models: DividendRecord, ScrapeResult, WorkflowInput/Output
                ├── parser.py       # HTML parsing: extract records, group by company
                ├── client.py       # Cloudflare Browser Rendering HTTP client with retries
                └── workflow.py     # Mistral workflow class + activities (entry point)
```

Where is X?

| X | Location |
|---|----------|
| Lint/format config | Root `pyproject.toml` `[tool.ruff]` |
| Runtime dependencies | `centraldepo-parser/pyproject.toml` |
| New workflow | New subpackage under `centraldepo-parser/src/workflows/<name>/` |
| Scraping logic (workflow) | `centraldepo-parser/src/workflows/centraldepo/client.py`, `parser.py` |
| Scraping logic (standalone) | `centraldepo-parser/example.py` |
| Workflow entry point | `workflow.py` class with `@workflows.workflow.entrypoint` |
| Worker start | `centraldepo-parser/src/discover.py` |
| Dev file watcher | `centraldepo-parser/src/dev_worker.py` |
| Workflow trigger CLI | `centraldepo-parser/src/workflows/start.py` |

## 4. Life of a Request / Primary Data Flow

**Workflow execution path (triggered via Mistral platform):**

1. **Trigger** — `centraldepo-parser/src/workflows/start.py` parses CLI args, loads `.env`, calls Mistral client to submit a workflow run (`Observed`).
2. **Dispatch** — Mistral AI Studio routes the execution to the local worker identified by hostname-based task queue (`Inferred` — `start-worker` target in `centraldepo-parser/Makefile`, `discover.py` calls `workflows.run_worker`).
3. **Discovery** — `centraldepo-parser/src/discover.py` scanned `src/workflows/` at worker startup, found `CentralDepoWorkflow` via `__workflows_workflow_def` attribute, and registered it (`Observed`).
4. **Workflow entry** — `CentralDepoWorkflow.run()` receives `WorkflowInput` (`Observed` — `workflow.py:136`).
5. **Credentials** — `get_credentials()` activity reads `CF_ACCOUNT_ID` and `CF_API_TOKEN` from environment (sandbox restriction forces this into an activity) (`Observed` — `workflow.py:53`).
6. **Pagination loop** — For each page 1..N: `scrape_single_page()` activity → `CloudflareClient.scrape_page()` → HTTP POST to Cloudflare Browser Rendering API → `parse_items()` extracts `DividendRecord` objects from response (`Observed` — `workflow.py:170`, `client.py:52`, `parser.py:14`).
7. **Transform** — `transform_to_output()` groups records by company name into `CompanyResult` objects, deduplicates URLs (`Observed` — `parser.py:51`).
8. **Persist** — `save_results()` activity writes JSON atomically (temp file + rename) to `output/` (`Observed` — `workflow.py:77`).
9. **Return** — `WorkflowOutput` with results list and stats dict returned to the Mistral platform (`Observed` — `workflow.py:232`).

**Standalone path (`example.py`):**

Same domain flow (fetch → parse → deduplicate → write) but executed directly as a CLI script without the Mistral runtime. Uses `aiohttp` directly instead of `CloudflareClient`, and `fake_useragent` instead of a hardcoded user agent list (`Observed`).

## 5. Architectural Invariants & Constraints

- **Rule:** All environment variable reads must occur inside `@workflows.activity()` functions, not in the workflow class body.
  - **Rationale:** The Mistral workflow runtime restricts `os.environ` access in the workflow sandbox.
  - **Enforcement / Signals (Observed):** `get_credentials()` in `workflow.py:53` is an activity specifically for this purpose.

- **Rule:** New workflows must be placed in a subpackage under `src/workflows/` with a class decorated by `@workflows.workflow.define(...)`.
  - **Rationale:** Auto-discovery in `discover.py` scans for the `__workflows_workflow_def` attribute on classes.
  - **Enforcement / Signals (Observed):** `discover.py:36` checks `hasattr(obj, "__workflows_workflow_def")`.

- **Rule:** Workflow packages must not import from sibling workflow packages.
  - **Rationale:** Each workflow is independently discovered and should be self-contained.
  - **Enforcement / Signals (Inferred):** No cross-package imports observed in `centraldepo/`; convention enforced by isolation, not a build check.

- **Rule:** JSON output must use atomic writes (temp file + rename).
  - **Rationale:** Prevents partial/corrupt output on crash.
  - **Enforcement / Signals (Observed):** Both `workflow.py:77` (`save_results`) and `example.py:333` (`write_json`) use `tempfile.mkstemp` + `os.replace`.

- **Rule:** Files under `centraldepo-parser/.agents/` are read-only.
  - **Rationale:** Contains Mistral SDK reference materials that should not be customized per project.
  - **Enforcement / Signals (Inferred):** Stated in `centraldepo-parser/AGENTS.md`; no mechanical enforcement.

- **Rule:** Two separate `uv` environments — root for tooling, `centraldepo-parser/` for runtime.
  - **Rationale:** Linting dependencies (ruff) are isolated from runtime dependencies (mistralai-workflows, aiohttp).
  - **Enforcement / Signals (Observed):** Separate `pyproject.toml`, `uv.lock`, and `.venv` at each level.

- **Rule:** Parsing/scraping logic changes must be applied to both `centraldepo/` modules and `example.py` if they need to stay in sync.
  - **Rationale:** `example.py` duplicates the domain logic from the workflow package as a standalone reference.
  - **Enforcement / Signals (Inferred):** No mechanical enforcement; stated in both `AGENTS.md` files.

- **Rule:** All linting passes via `make lint` from the repo root before committing.
  - **Rationale:** Consistent code style enforced at workspace level.
  - **Enforcement / Signals (Observed):** Root `Makefile` defines `lint` target running ruff against `centraldepo-parser/`.

- **Rule:** Retry/timeout constants must live in `config.py`, not be hardcoded in `client.py` or `workflow.py`.
  - **Rationale:** Centralizes tuning knobs for scraping behavior.
  - **Enforcement / Signals (Observed):** `config.py` exports `MAX_RETRIES`, `RETRY_BACKOFF_BASE`, `DEFAULT_TIMEOUT`; `client.py` imports them.

## 6. Documentation Strategy

`ARCHITECTURE.md` (this file) is the global map and invariants document for the repository. It describes the physical layout, logical layers, data flow, and architectural rules that span the entire workspace.

Module-level detail lives in:
- `AGENTS.md` (root) — workspace-wide conventions, ruff config, validation commands.
- `centraldepo-parser/AGENTS.md` — detailed module map, local change rules, safe modification guidance.
- `centraldepo-parser/README.md` — project setup, commands, and development workflow.

SDK reference:
- `centraldepo-parser/.agents/skills/workflows/SKILL.md` — Mistral Workflows SDK reference (read-only).

No tests directory or test documentation exists yet (`Inferred` — `pytest` is listed as a dev dependency but no test files are present).
