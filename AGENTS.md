# AGENTS.md

## Repository overview

Python workspace for the EPFR pipeline — a set of Mistral AI Workflows that download and process dividend disclosure records from `epfr.gov.by`.

| Project | Source | Purpose |
|---------|--------|---------|
| `epfr-downloader/` | `epfr.gov.by` REST API | Fetch paginated records, download files by UNP, extract, convert, OCR PDFs, AI-distill, export share payouts, produce mapping JSON |

- **Language:** Python 3.14.3 (`>=3.14,<3.15`)
- **Package manager:** uv (root for tooling, `epfr-downloader/.venv` for runtime)
- **Linting/formatting:** ruff (line-length 120, rules `F E W I D B UP C4 SIM PIE T20`, ignores `E501 E712`, Google-style docstrings)
- **Type checking:** ty
- **Workflow runtime:** Mistral AI Workflows SDK (`mistralai-workflows`)

**Read first:** `ARCHITECTURE.md` — full code map, logical layers, data flow, and architectural invariants.

## Where to work

```text
workflows/                          # Repository root
├── Makefile                        # lint / refactor / type-check (delegate into epfr-downloader/)
├── pyproject.toml                  # Root project: ruff + ty config (linting-only deps)
├── ARCHITECTURE.md                 # Full system code map and invariants
├── shares_source_data.csv          # Share reference input for the payout exporter
│
└── epfr-downloader/                # EPFR pipeline: download → OCR → AI distill → export
    ├── Makefile                    # installdeps, start-worker, execute, lint, test, docker-*
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

- **Two `uv` environments.** Root `.venv` holds ruff + ty tooling only; `epfr-downloader/.venv` holds runtime deps. Do not mix them.
- **Workflow sandbox rule.** The Mistral runtime restricts `os.environ` access inside workflow classes. All env var reads must happen inside `@workflows.activity()` functions, never in class methods.
- **Auto-discovery contract.** `discover.py` scans `src/workflows/` for classes carrying `__workflows_workflow_def`. New workflows must live in a subpackage under `src/workflows/` with a class decorated `@workflows.workflow.define(...)`.
- **Workflow isolation.** The four workflows are decoupled: they communicate only through filesystem artifacts (JSON/Markdown files under `output/`), and **no workflow module imports another**. To chain them, run the next workflow so it reads the previous one's output (e.g. the downloader's `unp_file_mapping.json` feeds the OCR and AI-distiller workflows). Never add a cross-workflow import.
- **Four workflows.** `epfr-files-downloader`, `epfr-ocr-converter`, `epfr-ai-distiller`, `epfr-share-payout-exporter`. The exporter also emits `share_dividends_insert.sql` (SQL generation is its 4th activity, `generate_share_payout_sql`).

## Change rules

- Run `make lint` from repo root (or `cd epfr-downloader && make lint`) before committing.
- Run `cd epfr-downloader && make test` for EPFR code changes.
- Ruff config: line-length 120, rules `F E W I D B UP C4 SIM PIE T20`, ignores `E501 E712`.
- Do not edit `.agents/` — read-only Mistral SDK references.
- Follow `.skills/python_docs_and_comments.md` for the comment/docstring policy (Google-style docstrings; comment the why, not the what).

## Validation

```bash
# --- From repo root (tooling env) ---
make lint           # ruff format --check + ruff check (runs inside epfr-downloader/)
make refactor       # auto-fix + format (runs inside epfr-downloader/)
make type-check     # uv run ty check epfr-downloader/

# --- From epfr-downloader/ (runtime env) ---
make installdeps    # uv sync
make lint
make refactor
make test           # NOTE: test_pdf_ocr.py is skipped by default (needs Mistral OCR credentials)
make start-worker
make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'
make execute-share-payout-exporter input='{"output_dir": "output"}'
make docker-build && make docker-run
```

## Context routing

Read only when relevant:

- Architectural / cross-module changes → `ARCHITECTURE.md` (system map, layers, dependency direction, data flow). For exact activity names, counts, and data contracts, cross-check `epfr-downloader/src/workflows/epfr/AGENTS.md` — higher-level docs can lag the source on those details.
- Project-local module map, invariants, make targets → `epfr-downloader/AGENTS.md`
- Per-file editing rules, activity signatures, data contracts → `epfr-downloader/src/workflows/epfr/AGENTS.md`
- Setup and usage → `README.md`
- Docstring / comment policy (Google-style) → `.skills/python_docs_and_comments.md`

## CI

- `.github/workflows/tests.yml` — on push to `main`, runs (in `epfr-downloader/`): `make installdeps`, `make lint`, `make test`, then uploads `coverage/coverage.xml` to Codecov.
- `.github/dependabot.yml` — weekly pip + GitHub Actions dependency updates.
- Coverage config in `epfr-downloader/.coveragerc`; reports in `epfr-downloader/coverage/`.

## Repository-specific gotchas

- Root `.venv` is tooling-only; runtime dependencies live in `epfr-downloader/.venv`. Root `make lint`/`refactor` shell into `epfr-downloader/` and use its env.
- `make test` in `epfr-downloader` skips `test_pdf_ocr.py` by default (requires Mistral OCR API credentials/quota).
- Env vars load from project-local `.env`; `MISTRAL_API_KEY` is required. All `EPFR_*` config knobs (concurrency, models, limits) can be overridden via env — see `epfr-downloader/src/workflows/epfr/config.py`.
- Docker run mounts `epfr-downloader/output` → `/app/output`, so outputs persist outside the container.
- `shares_source_data.csv` at repo root is the share reference input for the payout exporter workflow.
- **Higher-level docs can lag on activity-level detail.** `ARCHITECTURE.md`/`README.md` may not yet reflect exact activity names or counts; for those, trust `epfr-downloader/src/workflows/epfr/AGENTS.md` and the source.
