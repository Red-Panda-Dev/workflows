# AGENTS.md

## Repository overview

Python workspace containing a Mistral Workflows project that scrapes dividend disclosure records from `centraldepo.by` via the Cloudflare Browser Rendering API. The pipeline scrapes paginated records, groups them by company, downloads archive files (ZIP/TAR/GZ), extracts their contents, and saves structured JSON output.

- **Language:** Python 3.14.3
- **Package manager:** uv
- **Linting/formatting:** ruff (configured in root `pyproject.toml`)
- **Workflow runtime:** Mistral AI Workflows SDK (`mistralai-workflows`)

## Where to work

```text
centraldepo-parser/            # Main project — all application code lives here
├── src/
│   ├── discover.py            # Auto-discovers workflow classes, starts worker
│   ├── dev_worker.py          # File watcher with auto-reload for dev
│   └── workflows/
│       ├── start.py           # CLI to trigger a workflow execution
│       └── centraldepo/       # CentralDepo workflow implementation
├── example.py                 # Standalone scraper (non-workflow version)
├── pyproject.toml             # Project dependencies and dev tools config
├── Makefile                   # Run/execute/lint targets
└── .agents/                   # Mistral SDK skill references (read-only)
```

Root-level `pyproject.toml` and `Makefile` provide workspace-wide ruff config and lint targets.

## Architecture and boundaries

- **Root `pyproject.toml`** defines ruff rules that apply to `centraldepo-parser/` via the root `Makefile` lint/refactor targets.
- **`centraldepo-parser/pyproject.toml`** defines the project's own dependencies and dev tools. It has its own `uv.lock` and `.venv`.
- The Mistral workflow runs activities (functions decorated with `@workflows.activity()`) inside a sandboxed environment. Environment variable access must happen inside activities, not in the workflow class itself.
- Workflows are auto-discovered by scanning `src/workflows/` recursively for classes with `__workflows_workflow_def` attribute. New workflows should be placed in subpackages under `src/workflows/`.

## Change rules

- Run `make lint` from the repo root before committing. This runs `ruff format --check` and `ruff check` against `centraldepo-parser/`.
- Run `make refactor` to auto-fix lint issues.
- From inside `centraldepo-parser/`, use `uv run ruff format .` and `uv run ruff check --fix .`.
- Ruff config: line-length 120, rules `F E W I`, ignores `E501 E712 F821`. See root `pyproject.toml` for full config.
- No test files exist yet. `pytest` is listed as a dev dependency in `centraldepo-parser/pyproject.toml`.
- Do not modify files under `centraldepo-parser/.agents/` — those are Mistral SDK references.

## Validation

```bash
# From repo root — lint without modifying files
make lint

# From repo root — auto-fix and format
make refactor

# From centraldepo-parser/ — typecheck (if mypy is installed)
uv run mypy src/

# Start the dev worker (watches for file changes)
cd centraldepo-parser && make start-worker

# Trigger a workflow execution
cd centraldepo-parser && make execute workflow=centraldepo-parser input='{"max_pages": 2}'
```

## Key docs

- `ARCHITECTURE.md` — full code map, logical layers, data flow, architectural invariants
- `centraldepo-parser/README.md` — setup, commands, development workflow
- `centraldepo-parser/AGENTS.md` — project-local module map, change rules, boundaries
- `centraldepo-parser/.agents/skills/workflows/SKILL.md` — Mistral Workflows SDK reference

## Repository-specific gotchas

- **Two separate `uv` environments.** The root `pyproject.toml` has its own `.venv` (for ruff). `centraldepo-parser/` has its own `.venv` with runtime deps. Use the correct venv: root for linting, centraldepo-parser for running.
- **Env vars required in `.env`.** The worker needs `MISTRAL_API_KEY`. The scraper activities need `CF_ACCOUNT_ID` and `CF_API_TOKEN`. Both `.env` files are gitignored.
- **Root project name is a typo:** `worflows` (missing 'k') in root `pyproject.toml` — do not "fix" this without coordination.
- **`example.py` duplicates logic.** The standalone scraper in `example.py` mirrors the workflow in `src/workflows/centraldepo/`. Changes to parsing/scraping logic should be applied to both places if they need to stay in sync.
