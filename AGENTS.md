# AGENTS.md

## Repository overview

Python workspace focused on the EPFR pipeline that downloads and processes dividend disclosure records from `epfr.gov.by`.

| Project | Source | Purpose |
|---------|--------|---------|
| `epfr-downloader/` | `epfr.gov.by` REST API | Fetch paginated records, download files by UNP, extract, convert, OCR PDFs, AI-distill, produce mapping JSON |

- **Language:** Python 3.14.3
- **Package manager:** uv (root for tooling, project env for runtime)
- **Linting/formatting:** ruff (line-length 120, rules F E W I D B UP C4 SIM PIE T20, ignores E501 E712)
- **Type checking:** ty
- **Workflow runtime:** Mistral AI Workflows SDK (`mistralai-workflows`)

**Read first:** `ARCHITECTURE.md` — full code map, logical layers, data flow, and architectural invariants.

## Where to work

```text
workflows/                          # Repository root
├── Makefile                        # lint/refactor/type-check for epfr-downloader/
├── pyproject.toml                  # Root project: ruff + ty config (linting-only deps)
├── ARCHITECTURE.md                 # Full system code map and invariants
│
└── epfr-downloader/                # EPFR API downloader + OCR + AI distiller
    ├── Makefile                    # start-worker, execute, lint, test
    ├── pyproject.toml              # Runtime deps: mistralai-workflows, pydantic, aiohttp, etc.
    ├── AGENTS.md                   # Project-local module map and change rules
    └── src/
        ├── discover.py             # Auto-discovers workflow classes, starts Mistral worker
        └── workflows/
            ├── start.py            # CLI trigger for workflow execution
            └── epfr/               # files downloader + PDF OCR + AI distiller workflows
                └── AGENTS.md       # Pipeline internals, data contracts, activity boundaries
```

## Architecture and boundaries

- **Two `uv` environments.** Root `.venv` (ruff + ty tooling). `epfr-downloader/.venv` (runtime deps).
- **Workflow sandbox rule.** The Mistral workflow runtime restricts `os.environ` access inside workflow classes. All env var reads must happen inside `@workflows.activity()` functions.
- **Auto-discovery contract.** `discover.py` scans `src/workflows/` for classes with `__workflows_workflow_def`. New workflows must be in a subpackage under `src/workflows/` with a class decorated `@workflows.workflow.define(...)`.

## Change rules

- Run `make lint` from repo root or `cd epfr-downloader && make lint` before committing.
- Run `cd epfr-downloader && make test` for EPFR code changes.
- Ruff config: line-length 120, rules `F E W I D B UP C4 SIM PIE T20`, ignores `E501 E712`.
- Do not edit `.agents/` — read-only Mistral SDK references.

## Validation

```bash
# Lint from root
make lint

# Auto-fix from root
make refactor

# Type-check from root
make type-check

# Lint from project
cd epfr-downloader && make lint

# Test from project
cd epfr-downloader && make test

# Start worker
cd epfr-downloader && make start-worker

# Trigger main workflow
cd epfr-downloader && make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'
```

## Key docs

- `ARCHITECTURE.md` — full code map, logical layers, data flow, architectural invariants
- `epfr-downloader/AGENTS.md` — project-local module map, change rules, invariants
- `epfr-downloader/src/workflows/epfr/AGENTS.md` — pipeline internals, data contracts, activity boundaries
- `README.md` — setup, commands, and high-level usage
- `.skills/python_docs_and_comments.md` — Python comment and docstring policy

## Repository-specific gotchas

- Root `.venv` is tooling-only; runtime dependencies are in `epfr-downloader/.venv`.
- `make test` in `epfr-downloader` currently includes `test_pdf_ocr.py`, which can fail if local runtime/config for Mistral integrations is unavailable.
- Env vars are loaded from project-local `.env`; `MISTRAL_API_KEY` is required.
