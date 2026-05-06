# AGENTS.md

## Repository overview

Python workspace with two independent Mistral Workflow projects that download and process dividend disclosure records from Belarusian financial regulators.

| Project | Source | Purpose |
|---------|--------|---------|
| `centraldepo-parser/` | `centraldepo.by` | Scrape, download, extract, convert, AI-distill dividend filings |
| `epfr-downloader/` | `epfr.gov.by` REST API | Fetch paginated records, download files by UNP, extract, convert, OCR PDFs, AI-distill, produce mapping JSON |

- **Language:** Python 3.14.3
- **Package manager:** uv (each project has its own `.venv` and `uv.lock`)
- **Linting/formatting:** ruff (line-length 120, rules F E W I D B UP C4 SIM PIE T20, ignores E501 E712)
- **Workflow runtime:** Mistral AI Workflows SDK (`mistralai-workflows`)

**Read first:** `ARCHITECTURE.md` — full code map, logical layers, data flow, and architectural invariants.

## Where to work

```text
workflows/                          # Repository root
├── Makefile                        # lint/refactor/type-check for centraldepo-parser/ only
├── pyproject.toml                  # Root project: ruff + ty config (linting-only deps)
├── ARCHITECTURE.md                 # Full system code map and invariants
│
├── centraldepo-parser/             # Project 1: CentralDepo scraper + AI distiller
│   ├── Makefile                    # start-worker, execute, execute-collect-assets, execute-distill-dividends, execute-pipeline
│   ├── pyproject.toml              # Runtime deps: mistralai-workflows, pydantic, aiohttp, etc.
│   ├── AGENTS.md                   # Project-local module map and change rules
│   └── src/
│       ├── discover.py             # Auto-discovers workflow classes, starts Mistral worker
│       └── workflows/
│           ├── start.py            # CLI trigger for workflow execution
│           └── centraldepo/        # Full pipeline: scrape → download → extract → convert → AI distill
│               └── AGENTS.md       # Pipeline internals, data contracts, activity boundaries
│
└── epfr-downloader/                # Project 2: EPFR API downloader + OCR + AI distiller
    ├── Makefile                    # start-worker, execute, lint, test
    ├── pyproject.toml              # Runtime deps: mistralai-workflows, pydantic, aiohttp, etc.
    ├── AGENTS.md                   # Project-local module map and change rules
    └── src/
        ├── discover.py             # Auto-discovers workflow classes, starts Mistral worker
        └── workflows/
            ├── start.py            # CLI trigger for epfr workflow execution
            └── epfr/               # 3 workflows: download+extract+convert, PDF OCR, AI distill
                └── AGENTS.md       # Pipeline internals, data contracts, activity boundaries
```

## Architecture and boundaries

- **Three separate `uv` environments.** Root `.venv` (ruff + ty for workspace linting). `centraldepo-parser/.venv` (runtime deps). `epfr-downloader/.venv` (runtime deps). Use the correct environment for each operation.
- **Root Makefile scope.** `make lint`, `make refactor`, and `make type-check` only cover `centraldepo-parser/`. For `epfr-downloader/`, use its own Makefile.
- **Workflow sandbox rule.** The Mistral workflow runtime restricts `os.environ` access inside workflow classes. All env var reads must happen inside `@workflows.activity()` functions — applies to both projects.
- **Auto-discovery contract.** Both projects' `discover.py` scan `src/workflows/` for classes with `__workflows_workflow_def`. New workflows must be in a subpackage under `src/workflows/` with a class decorated `@workflows.workflow.define(...)`.
- **No cross-project imports.** `centraldepo-parser/` and `epfr-downloader/` are fully independent. Do not import between them.
- **Multiple workflows per project.** `centraldepo-parser` has 2 workflows (`centraldepo-collect-assets`, `centraldepo-distill-dividends`). `epfr-downloader` has 3 workflows (`epfr-files-downloader`, `epfr-pdf-ocr-converter`, `epfr-ai-distiller`).

## Change rules

- **centraldepo-parser:** Run `make lint` from repo root or `uv run ruff check src/` from inside the project before committing.
- **epfr-downloader:** Run `make lint` from inside `epfr-downloader/` before committing. Also run `make test` — it has test coverage.
- **Ruff config:** line-length 120, rules `F E W I D B UP C4 SIM PIE T20`, ignores `E501 E712` (root `pyproject.toml`). Both projects inherit this via their own ruff sections or the workspace root.
- **Do not edit `.agents/`** in either project — read-only Mistral SDK references.

## Validation

```bash
# Lint centraldepo-parser (from repo root)
make lint

# Auto-fix centraldepo-parser (from repo root)
make refactor

# Type-check centraldepo-parser (from repo root)
make type-check

# Lint epfr-downloader (from inside epfr-downloader/)
cd epfr-downloader && make lint

# Test epfr-downloader
cd epfr-downloader && make test

# Start centraldepo worker
cd centraldepo-parser && make start-worker

# Start epfr worker
cd epfr-downloader && make start-worker

# Trigger centraldepo workflows
cd centraldepo-parser && make execute-collect-assets input='{"max_pages": 2}'
cd centraldepo-parser && make execute-distill-dividends input='{"input_path": "output/centraldepo_dividends.json"}'

# Trigger epfr workflow
cd epfr-downloader && make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'
```

## Key docs

- `ARCHITECTURE.md` — full code map, logical layers, data flow, architectural invariants
- `centraldepo-parser/AGENTS.md` — project-local module map, change rules, pipeline boundaries
- `centraldepo-parser/src/workflows/centraldepo/AGENTS.md` — pipeline internals, data contracts, activity boundaries
- `centraldepo-parser/README.md` — setup, commands, data model, troubleshooting
- `epfr-downloader/AGENTS.md` — EPFR project module map, change rules, invariants
- `epfr-downloader/src/workflows/epfr/AGENTS.md` — pipeline internals, data contracts, activity boundaries
- `.skills/python_docs_and_comments.md` — Python comment and docstring policy

## Repository-specific gotchas

- **Three isolated `uv` envs.** Root `.venv` = linting only. Each project has its own runtime `.venv`. Running `uv run` from the wrong directory uses the wrong env.
- **Root Makefile lint targets are centraldepo-only.** `make lint` and `make refactor` at the root do not touch `epfr-downloader/`. Lint that project separately.
- **`epfr-downloader` has tests; `centraldepo-parser` does not.** Run `make test` inside `epfr-downloader/` when modifying that project.
- **Root project name typo:** `workflows` (missing 'k') in root `pyproject.toml` — do not correct without coordination.
- **Stale doc references.** `centraldepo-parser/README.md` mentions `example.py` and `dev_worker.py` — neither exists. The `src/workflows/centraldepo/` package is authoritative.
- **Env vars per project:** Both projects need `MISTRAL_API_KEY`. All `.env` files are gitignored.
