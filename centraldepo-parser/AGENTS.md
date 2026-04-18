# AGENTS.md

## Scope

The CentralDepo Dividend Parser — a Mistral Workflow that scrapes paginated dividend disclosure records from `centraldepo.by` via the Cloudflare Browser Rendering API, groups them by company, and saves results to JSON.

## What lives here

```text
src/
├── discover.py                # Scans workflows/ for workflow classes, starts Mistral worker
├── dev_worker.py              # watchdog-based auto-reload wrapper around discover.py
└── workflows/
    ├── __init__.py            # Empty package marker (auto-discovery entry point)
    ├── start.py               # CLI to trigger workflow execution via Mistral client
    └── centraldepo/           # Core workflow package
        ├── config.py          # Constants: BASE_URL, SCRAPE_API, SELECTOR, defaults
        ├── models.py          # Pydantic models: DividendRecord, ScrapeResult, WorkflowInput/Output
        ├── parser.py          # HTML parsing: extract records, group by company
        ├── client.py          # Cloudflare Browser Rendering HTTP client with retries
        └── workflow.py        # Mistral workflow + activities (entry point)
example.py                     # Standalone async scraper (non-workflow version of same logic)
output/                        # JSON output directory (gitignored)
```

## Local boundaries and invariants

- **Workflow sandbox:** The Mistral workflow runtime restricts `os.environ` access inside the workflow class. All env var reads must happen inside `@workflows.activity()` functions. See `get_credentials()` in `workflow.py`.
- **Discovery contract:** New workflows must be placed in a subpackage under `src/workflows/`. The class must be decorated with `@workflows.workflow.define(...)` — the discoverer scans for `__workflows_workflow_def`.
- **Atomic writes:** Both `workflow.py` (`save_results` activity) and `example.py` (`write_json`) use temp-file-then-rename for atomic JSON output. Keep this pattern if adding new output paths.
- **Pagination convention:** Page 1 uses the base URL with no query params. Page 2+ appends `?PAGEN_1=<n>`. This is hardcoded in both `config.py:BASE_URL` and `_build_page_url()`.
- **CSS selector:** `.news-item` is the selector used to find dividend entries. If the target site changes its markup, update `config.py:SELECTOR` and the Cloudflare API payload in `client.py`.

## Safe change rules

- **Adding a new workflow:** Create a new package under `src/workflows/<name>/` with a module containing a class decorated with `@workflows.workflow.define(...)`. It will be auto-discovered by `discover.py`.
- **Modifying parsing logic:** Edit `parser.py`. If you also need the standalone scraper to reflect the change, update `example.py` as well.
- **Changing API interaction:** Edit `client.py`. Do not change retry/timeout constants directly — use `config.py`.
- **Changing output schema:** Edit `models.py` first, then update `workflow.py` (the `save_results` activity serializes `WorkflowOutput`) and `parser.py` (produces `CompanyResult`).
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

- `README.md` — project setup and commands
- `.agents/skills/workflows/SKILL.md` — Mistral Workflows SDK reference
- Root `AGENTS.md` — workspace-wide conventions and ruff config
