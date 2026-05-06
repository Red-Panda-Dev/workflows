# AGENTS.md

## Scope

The EPFR Files Downloader — a Mistral Workflow that fetches paginated disclosure records from the `epfr.gov.by` REST API, downloads each record's raw file content, organizes files into `epfr_files/<UNP>/` directories, and produces a `unp_file_mapping.json` with company metadata and file paths.

## What lives here

```text
src/
├── discover.py                    # Scans workflows/ for workflow classes, starts Mistral worker
└── workflows/
    ├── __init__.py                # Empty package marker (auto-discovery entry point)
    ├── start.py                   # CLI to trigger epfr-files-downloader workflow execution
    └── epfr/
        ├── config.py              # Constants: API URLs, concurrency, retry, pagination defaults
        ├── models.py              # Pydantic models: EpfrRecord, EpfrApiResponse, EpfrWorkflowInput/Output
        ├── client.py              # aiohttp API client (fetch pages, download files by record ID)
        ├── detector.py            # Magic-byte file extension detection (API returns raw bytes, no names)
        ├── workflow.py            # Mistral workflow + 3 activities: fetch_all_pages, download_all_epfr_files, save_unp_mapping
        └── tests/
            ├── test_client.py     # Client and download logic tests
            ├── test_detector.py   # Magic-byte detection tests
            └── test_models.py     # Pydantic model parsing tests
epfr_files/                        # Downloaded files and mapping JSON (gitignored)
```

## Local boundaries and invariants

- **Workflow sandbox:** `os.environ` access is forbidden inside the `EpfrFilesDownloader` workflow class. All env var reads happen inside `@workflows.activity()` functions.
- **Discovery contract:** The workflow class is decorated with `@workflows.workflow.define(name="epfr-files-downloader", ...)`. The discoverer in `discover.py` scans for `__workflows_workflow_def`.
- **No filenames from API:** The EPFR API returns raw binary content with no filename. `detector.py` inspects the first bytes (magic bytes) and maps them to an extension. Files are saved as `<record_id><detected_ext>` (e.g. `141278.pdf`).
- **UNP-based folder layout:** Files are organized by the holder's UNP (tax ID string). UNP comes from `rec.holder.unp` if non-empty, otherwise falls back to `rec.organization.unp`. Records missing a `holder.id` are skipped in the mapping but logged.
- **Pagination starts at 0:** `FIRST_PAGE_NO = 0`. The EPFR API uses 0-based page numbers — do not adjust to 1.
- **Early termination:** Fetching stops when the API response has `last=True`, before reaching `max_pages`.
- **Atomic mapping write:** `save_unp_mapping` uses `tempfile.mkstemp()` → write → `os.replace()`. Keep this pattern for any new output files.
- **No AI calls:** This project has no Mistral Large or OCR API usage — only the Mistral Workflows orchestration SDK.

## Safe change rules

- **Changing API request params:** Edit `client.py` (`fetch_page`). Constants live in `config.py`.
- **Changing download behavior:** Edit `client.py` (`download_all_files`, `download_file`). Retry/concurrency tuning in `config.py`.
- **Adding new magic-byte signatures:** Edit `detector.py:SIGNATURES`. Keep the list ordered — first match wins.
- **Changing output schema:** Edit `models.py` first, then update `workflow.py` and `client.py`.
- **Do not edit `.agents/`** — read-only Mistral SDK references.

## Validation

```bash
# From inside epfr-downloader/

# Install dependencies
uv sync

# Lint
make lint

# Auto-fix
uv run ruff format src/
uv run ruff check --fix src/

# Run tests
make test

# Start worker
make start-worker

# Trigger workflow
make execute input='{"max_pages": 2, "date_from": "2026-03-01"}'
```

## Nearby docs

- Root `AGENTS.md` — workspace conventions, three-env layout, ruff config
- Root `ARCHITECTURE.md` — full system code map and invariants
