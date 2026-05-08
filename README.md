# workflows

Python workspace for a [Mistral AI Workflows](https://docs.mistral.ai/workflows/getting-started/introduction) project that automates downloading and processing dividend disclosure records from Belarusian regulator APIs.

| Project | Source | Pipeline |
|---------|--------|----------|
| **epfr-downloader** | [epfr.gov.by](https://epfr.gov.by) REST API | Paginate records -> download files by UNP -> extract/convert/OCR -> AI-distill -> produce mapping JSON |

- **Language:** Python 3.14.3
- **Package manager:** [uv](https://github.com/astral-sh/uv)
- **Linting/formatting:** [ruff](https://docs.astral.sh/ruff/)
- **Type checking:** [ty](https://github.com/astral-sh/ty)

## Quick Start

### Prerequisites

- Python 3.14.3
- [uv](https://github.com/astral-sh/uv)
- `MISTRAL_API_KEY`

### Setup

```bash
git clone <repo-url> && cd workflows
cd epfr-downloader && uv sync
```

Create `epfr-downloader/.env`:

```dotenv
MISTRAL_API_KEY=your_key_here
```

### Run worker and execute

```bash
# Terminal 1: start worker
cd epfr-downloader && make start-worker

# Terminal 2: trigger workflow
cd epfr-downloader && make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'
```

## Validation

```bash
# From repo root
make lint
make refactor
make type-check

# From epfr-downloader/
make lint
make test
```

## Workflow overview

The project has three independently invocable workflows:

1. `epfr-files-downloader` - fetch pages, download files, extract archives, convert documents, write `unp_file_mapping.json`
2. `epfr-pdf-ocr-converter` - OCR PDFs from mapping and update mapping entries to markdown files
3. `epfr-ai-distiller` - extract structured dividend records from markdown and write `ai_distilled_dividends.json`

## Docs

- `ARCHITECTURE.md` - system map and invariants
- `AGENTS.md` - contributor guidance
- `epfr-downloader/AGENTS.md` - project-local rules
- `epfr-downloader/src/workflows/epfr/AGENTS.md` - EPFR pipeline internals
