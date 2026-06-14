# AGENTS.md

## Repository overview

Python workspace for the EPFR pipeline — a set of Mistral AI Workflows that download and process dividend disclosure records from `epfr.gov.by`.

| Project | Source | Purpose |
|---------|--------|---------|
| `epfr-downloader/` | `epfr.gov.by` REST API | Fetch paginated records, download files by UNP, extract, convert, OCR PDFs, AI-distill, export share payouts, produce mapping JSON |

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
└── epfr-downloader/                # EPFR pipeline: download → OCR → AI distill → export
    ├── Makefile                    # start-worker, execute, lint, test, docker-build, docker-run
    ├── pyproject.toml              # Runtime deps: mistralai-workflows, pydantic, aiohttp, etc.
    ├── Dockerfile                  # Container image for worker deployment
    ├── AGENTS.md                   # Project-local module map and change rules
    └── src/
        ├── discover.py             # Auto-discovers workflow classes, starts Mistral worker
        ├── tests/                  # Unit tests (18 modules, see epfr-downloader/AGENTS.md)
        └── workflows/
            ├── start.py            # CLI trigger for workflow execution
            └── epfr/               # 4 workflows: download, OCR, AI-distill, share payout export
                └── AGENTS.md       # Pipeline internals, data contracts, activity boundaries
```

## Architecture and boundaries

- **Two `uv` environments.** Root `.venv` (ruff + ty tooling). `epfr-downloader/.venv` (runtime deps).
- **Workflow sandbox rule.** The Mistral runtime restricts `os.environ` access inside workflow classes. All env var reads must happen inside `@workflows.activity()` functions.
- **Auto-discovery contract.** `discover.py` scans `src/workflows/` for classes with `__workflows_workflow_def`. New workflows must be in a subpackage under `src/workflows/` with a class decorated `@workflows.workflow.define(...)`.
- **Four workflows.** `epfr-files-downloader`, `epfr-ocr-converter`, `epfr-ai-distiller`, `epfr-share-payout-exporter`. The exporter also emits `share_dividends_insert.sql` (SQL generation is its 4th activity, `generate_share_payout_sql`).

## Change rules

- Run `make lint` from repo root or `cd epfr-downloader && make lint` before committing.
- Run `cd epfr-downloader && make test` for EPFR code changes.
- Ruff config: line-length 120, rules `F E W I D B UP C4 SIM PIE T20`, ignores `E501 E712`.
- Do not edit `.agents/` — read-only Mistral SDK references.
- Follow `.skills/python_docs_and_comments.md` for comment and docstring policy (Google-style docstrings, comment the why not the what).

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

# Test from project (note: test_pdf_ocr.py is skipped by default)
cd epfr-downloader && make test

# Start worker
cd epfr-downloader && make start-worker

# Trigger main workflow
cd epfr-downloader && make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'

# Trigger share payout export workflow
cd epfr-downloader && make execute-share-payout-exporter input='{"output_dir": "output"}'

# Docker: build and run worker container
cd epfr-downloader && make docker-build && make docker-run
```

## Key docs

- `ARCHITECTURE.md` — full code map, logical layers, data flow, architectural invariants
- `epfr-downloader/AGENTS.md` — project-local module map, change rules, invariants
- `epfr-downloader/src/workflows/epfr/AGENTS.md` — pipeline internals, data contracts, activity boundaries
- `README.md` — setup, commands, and high-level usage
- `.skills/python_docs_and_comments.md` — Python comment and docstring policy (Google-style, applies to all `.py` files)

## CI

- `.github/workflows/tests.yml` — runs `make installdeps`, `make lint`, `make test` on push to main, uploads coverage to Codecov
- `.github/dependabot.yml` — weekly pip + GitHub Actions dependency updates
- Coverage config in `epfr-downloader/.coveragerc`; reports in `epfr-downloader/coverage/`

## Repository-specific gotchas

- Root `.venv` is tooling-only; runtime dependencies are in `epfr-downloader/.venv`.
- `make test` in `epfr-downloader` skips `test_pdf_ocr.py` by default (requires Mistral OCR API credentials).
- Env vars are loaded from project-local `.env`; `MISTRAL_API_KEY` is required.
- Docker volume mounts `epfr-downloader/output` to `/app/output` — outputs persist outside container.
- `shares_source_data.csv` at repo root is the share reference input for the payout exporter workflow.
