# AGENTS.md

## Repository overview

Python workspace containing a Mistral Workflows project that scrapes dividend disclosure records from `centraldepo.by` via the Cloudflare Browser Rendering API. The pipeline scrapes paginated records, groups them by company, downloads archive files (ZIP/TAR/GZ), extracts their contents, converts documents to Markdown (docx/doc/xls via Python libs, PDF via Mistral OCR as base64 data URI), runs AI distillation with Mistral Large to extract structured dividend data, and saves results to JSON.

- **Language:** Python 3.14.3
- **Package manager:** uv
- **Linting/formatting:** ruff (configured in root `pyproject.toml`)
- **Workflow runtime:** Mistral AI Workflows SDK (`mistralai-workflows`)

**Read first:** `ARCHITECTURE.md` — full code map, logical layers, data flow, and architectural invariants.

## Where to work

```text
centraldepo-parser/            # Main project — all application code lives here
├── src/
│   ├── discover.py            # Auto-discovers workflow classes, starts worker
│   └── workflows/
│       ├── __init__.py        # Package marker for auto-discovery
│       ├── start.py           # CLI to trigger a workflow execution
│       └── centraldepo/       # CentralDepo workflow implementation
│           ├── config.py      # Constants and tuning knobs
│           ├── models.py      # Pydantic data models
│           ├── parser.py      # HTML parsing logic
│           ├── client.py      # Cloudflare Browser Rendering HTTP client
│           ├── downloader.py  # Concurrent file download
│           ├── extractor.py   # Archive extraction
│           ├── converter.py   # Document-to-Markdown conversion
│           ├── ai_distiller.py # AI structured data extraction
│           ├── prompts/       # AI prompt templates
│           │   └── dividends_parsing.md
│           └── workflow.py    # Mistral workflow + activities
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
- PDF conversion reads files as base64 data URIs and passes them directly to the Mistral OCR API (no intermediate storage).

## Change rules

- Run `make lint` from the repo root before committing. This runs `ruff format --check` and `ruff check` against `centraldepo-parser/`.
- Run `make refactor` to auto-fix lint issues.
- From inside `centraldepo-parser/`, use `uv run ruff format .` and `uv run ruff check --fix .`.
- Ruff config: line-length 120, rules `F E W I D B UP C4 SIM PIE T20`, ignores `E501 E712`. See root `pyproject.toml` for full config.
- No test files exist yet. `pytest` is listed as a dev dependency in `centraldepo-parser/pyproject.toml`.
- Do not modify files under `centraldepo-parser/.agents/` — those are Mistral SDK references.

## Validation

```bash
# From repo root — lint without modifying files
make lint

# From repo root — auto-fix and format
make refactor

# From repo root — type-check with ty
make type-check

# Start the dev worker (watches for file changes)
cd centraldepo-parser && make start-worker

# Trigger a workflow execution
cd centraldepo-parser && make execute workflow=centraldepo-parser input='{"max_pages": 2}'
```

## Key docs

- `ARCHITECTURE.md` — full code map, logical layers, data flow, architectural invariants
- `centraldepo-parser/AGENTS.md` — project-local module map, change rules, boundaries
- `centraldepo-parser/src/workflows/centraldepo/AGENTS.md` — pipeline internals, data contracts, activity boundaries
- `centraldepo-parser/README.md` — setup, commands, data model, troubleshooting
- `.skills/python_docs_and_comments.md` — Python comment and docstring policy

## Repository-specific gotchas

- **Two separate `uv` environments.** The root `pyproject.toml` has its own `.venv` (for ruff + ty). `centraldepo-parser/` has its own `.venv` with runtime deps. Use the correct venv: root for linting, centraldepo-parser for running.
- **Env vars required in `.env`.** The worker, PDF OCR, and AI distillation need `MISTRAL_API_KEY`. The scraper activities need `CF_ACCOUNT_ID` and `CF_API_TOKEN`. All `.env` files are gitignored.
- **Root project name is a typo:** `workflows` (missing 'k') in root `pyproject.toml` — do not "fix" this without coordination.
- **Stale doc references.** `centraldepo-parser/README.md` mentions `example.py` and `dev_worker.py` — neither exists on disk. The workflow in `src/workflows/centraldepo/` is the authoritative implementation.

## Python comments and docstrings

Follow the policy in `.skills/python_docs_and_comments.md`. Mandatory for public APIs, aiohttp handlers, and non-trivial async workflows.
