# workflows

Python monorepo with two independent [Mistral AI Workflows](https://docs.mistral.ai/workflows/getting-started/introduction) projects that automate downloading and processing dividend disclosure records from Belarusian financial regulators.

| Project | Source | Pipeline |
|---------|--------|----------|
| **centraldepo-parser** | [centraldepo.by](https://www.centraldepo.by/uslugi/raskrytie-informatsii/reestr/dividends/) | Scrape → download → extract → convert to Markdown → AI-distill structured dividend data |
| **epfr-downloader** | [epfr.gov.by](https://epfr.gov.by) REST API | Paginate records → download files by company UNP → produce mapping JSON |

- **Language:** Python 3.14.3
- **Package manager:** [uv](https://github.com/astral-sh/uv) — each project has its own `.venv` and `uv.lock`
- **Linting/formatting:** [ruff](https://docs.astral.sh/ruff/) (line-length 120, rules `F E W I D B UP C4 SIM PIE T20`)
- **Type checking:** [ty](https://github.com/astral-sh/ty)
- **Workflow runtime:** Mistral AI Workflows SDK (`mistralai-workflows`)

## Quick Start

### Prerequisites

- Python 3.14.3
- [uv](https://github.com/astral-sh/uv) package manager
- `MISTRAL_API_KEY` environment variable (required by both projects)

### Setup

```bash
# Clone and enter the repo
git clone <repo-url> && cd workflows

# Each project installs independently
cd centraldepo-parser && uv sync && cd ..
cd epfr-downloader && uv sync && cd ..
```

Create a `.env` file in each project directory:

```
MISTRAL_API_KEY=your_key_here
```

### Running a Workflow

Each project follows the same two-step pattern: start a worker, then trigger execution.

```bash
# Terminal 1 — start the worker (long-running)
cd centraldepo-parser && make start-worker

# Terminal 2 — trigger a workflow
cd centraldepo-parser && make execute-collect-assets input='{"max_pages": 2}'
```

```bash
# Terminal 1
cd epfr-downloader && make start-worker

# Terminal 2
cd epfr-downloader && make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'
```

## Project Details

### centraldepo-parser

Scrapes paginated dividend disclosure records from centraldepo.by, downloads archive files, extracts documents, converts them to Markdown, and uses Mistral Large to extract structured dividend data.

**Two-phase workflow:**

1. **`centraldepo-collect-assets`** — scrape pages, download archives, extract files → `output/centraldepo_dividends.json`
2. **`centraldepo-distill-dividends`** — convert files to Markdown, AI-distill structured dividend data → final JSON

```bash
cd centraldepo-parser

# Phase 1: collect
make execute-collect-assets input='{"max_pages": 10}'

# Phase 2: distill
make execute-distill-dividends input='{"input_path": "output/centraldepo_dividends.json"}'

# Or run both phases
make execute-pipeline collect_input='{"max_pages": 10}' distill_input='{"input_path": "output/centraldepo_dividends.json"}'
```

**Key features:**
- Concurrent downloads with retry/backoff and configurable concurrency limits
- Archive extraction (ZIP, TAR, GZ, TGZ, TAR.GZ)
- Document conversion: docx/doc/xls locally, PDF via Mistral OCR
- AI distillation: Mistral Large structured parsing with Pydantic models
- Atomic file writes throughout

### epfr-downloader

Fetches paginated disclosure records from the epfr.gov.by REST API, downloads raw file content, and organizes files by company UNP (tax ID).

**Single workflow:** `epfr-files-downloader`

```bash
cd epfr-downloader
make execute input='{"max_pages": 50, "date_from": "2026-03-01"}'
```

**Key features:**
- Magic-byte file type detection (API returns raw bytes with no filenames)
- UNP-based folder layout: `epfr_files/<UNP>/<record_id>.<ext>`
- 0-based pagination with early termination on `last=True`
- Atomic mapping JSON write (`unp_file_mapping.json`)
- Unit test coverage under `src/workflows/epfr/tests/`

## Validation

```bash
# Lint centraldepo-parser (from repo root)
make lint

# Auto-fix centraldepo-parser
make refactor

# Type-check centraldepo-parser
make type-check

# Lint epfr-downloader
cd epfr-downloader && make lint

# Run epfr-downloader tests
cd epfr-downloader && make test
```

## Architecture

Each project follows the same layered structure:

```
[Mistral Workflows Runtime]  — external orchestration, retries, job dispatch
         │
    [Worker Process]          — src/discover.py: auto-discovers workflow classes, starts worker
         │
    [Workflow Orchestration]  — src/workflows/<project>/workflow.py: sequences activities
         │
    [Activities]              — @workflows.activity() decorated async functions; all I/O and env access
         │
    [Support Layer]           — client.py, parser.py, converter.py, ai_distiller.py, models.py, etc.
```

**Key constraints:**
- `os.environ` access only inside `@workflows.activity()` functions (Mistral runtime sandbox)
- No cross-project imports between centraldepo-parser and epfr-downloader
- New workflows must be placed under `src/workflows/<name>/` with `@workflows.workflow.define(...)`
- Three isolated `uv` environments: root (linting only), each project (runtime deps)

## Documentation

| Document | Scope |
|----------|-------|
| `ARCHITECTURE.md` | Full code map, logical layers, data flow, architectural invariants |
| `centraldepo-parser/AGENTS.md` | Project-local module map, activity boundaries, change rules |
| `centraldepo-parser/README.md` | Setup, commands, data model, troubleshooting |
| `centraldepo-parser/src/workflows/centraldepo/AGENTS.md` | Pipeline internals, data contracts between stages |
| `epfr-downloader/AGENTS.md` | Project-local module map, change rules, invariants |

## License

Private workflow automation suite. Distribution and usage governed by organizational policies.
