# AGENTS.md — CentralDepo Workflow Package

Pipeline internals for split CentralDepo workflows. Covers data flow between modules, activity boundaries, data contracts, and per-file editing rules.

## Pipeline stages

Two independent workflows are defined in `workflow.py`:

- `centraldepo-collect-assets`: stages 1-5
- `centraldepo-distill-dividends`: stages 6-9

Shared activities are defined in `activities.py` and reused by both workflows.

The full pipeline stages are:

1. `scrape_pages_batch` → batches of pages via aiohttp
2. `transform_to_output` → groups `DividendRecord` objects → `CompanyResult` objects
3. `save_results` → writes `centraldepo_dividends.json` atomically
4. `download_all_results_files` → downloads archives to MD5-named company folders
5. `extract_all_downloaded_archives` → extracts ZIP/TAR/GZ, removes archives
6. `convert_all_downloaded_files` → docx/doc/xls locally, PDF via Mistral OCR (base64 data URI)
7. `run_ai_data_distillation` → Mistral Large structured extraction per MD file
8. `save_distillation_results` → writes `ai_distilled.json` atomically
9. `generate_final_json` → writes `final_mapping.json` atomically

Early termination: batch scraping stops on empty page (end of pagination) or 3+ consecutive failures.

## Module map

| File | Role | Key exports |
|------|------|-------------|
| `config.py` | All constants and tuning knobs | `BASE_URL`, `SELECTOR`, `BATCH_SIZE`, `MAX_CONCURRENT_*`, `AI_MODEL`, retry/timeout defaults |
| `models.py` | Pydantic data shapes for entire pipeline | `DividendRecord`, `ScrapeResult`, `CompanyResult`, `CollectAssetsInput`, `DistillDividendsInput`, `WorkflowOutput`, `DividendData`, `SharePayout` |
| `parser.py` | HTML → structured records | `parse_items()`, `transform_to_output()` |
| `client.py` | HTTP client | `AiohttpClient`, `AiohttpSessionManager`, `CircuitBreaker`, `RateLimiter` |
| `downloader.py` | Concurrent file download | `download_all_files()`, `get_company_folder_name()`, `get_filename_from_url()` |
| `extractor.py` | Archive extraction | `extract_all_archives()`, `is_archive()` |
| `converter.py` | Document → Markdown conversion | `convert_all_files()`, `process_pdf_files()`, `convert_to_markdown()` |
| `ai_distiller.py` | AI structured data extraction | `run_ai_distillation()`, `AIDistiller`, `process_single_file()` |
| `prompts/dividends_parsing.md` | Mistral Large prompt template | Template with `{{REFERENCE_DATE}}` and `{{DOCUMENT_TEXT}}` placeholders |
| `common.py` | Shared orchestration helpers | `build_page_url()`, `atomic_write_json()`, `load_company_results()` |
| `activities.py` | Shared workflow activities | all `@workflows.activity()` functions |
| `workflow.py` | Orchestration: 2 workflow classes | `CentralDepoCollectAssetsWorkflow`, `CentralDepoDistillDividendsWorkflow` |

## Data contracts between stages

```
scrape_pages_batch()
  input:  page_urls: list[tuple[int, str]], timeout
  output: list[ScrapeResult]  (ScrapeResult.items = list[DividendRecord])

transform_to_output()
  input:  list[DividendRecord]
  output: list[CompanyResult]  (grouped by lowercase company name, deduplicated URLs)

save_results()
  input:  WorkflowOutput, output_path: str
  output: str  (absolute path to saved JSON)

download_all_results_files()
  input:  list[CompanyResult], output_path: str
  output: dict  {total_companies, total_files, successful, failed, failed_urls, by_company}

extract_all_downloaded_archives()
  input:  list[CompanyResult], output_path: str
  output: dict  {total_companies, total_archives, successful, failed, files_extracted, by_company}

convert_all_downloaded_files()
  input:  list[CompanyResult], output_path: str
  output: dict  {total_companies, total_files_attempted, total_successful, total_failed, cleaned_up_files, by_company}

run_ai_data_distillation()
  input:  list[CompanyResult], output_path: str, reference_date: str | None
  output: (distillation_data: dict, stats: dict)
  distillation_data maps company_hash → {company_name, files_paths, dividends}

save_distillation_results()
  input:  distillation_data: dict, output_root: str
  output: str  (path to ai_distilled.json)

generate_final_json()
  input:  list[CompanyResult], output_root: str
  output: str  (path to final_mapping.json)
```

## Activity boundaries

All functions decorated with `@workflows.activity()` are in `activities.py`:

| Activity | Lines | Env vars read | External calls |
|----------|-------|---------------|----------------|
| `scrape_pages_batch` | 59-107 | None | HTTP (centraldepo.by) |
| `save_results` | 134-178 | None | Filesystem |
| `download_all_results_files` | 181-221 | None | HTTP (centraldepo.by), Filesystem |
| `extract_all_downloaded_archives` | 224-265 | None | Filesystem |
| `convert_all_downloaded_files` | 338-389 | `CLEANUP_SOURCE_FILES` | Mistral OCR API (PDFs), Filesystem |
| `run_ai_data_distillation` | 392-428 | `MISTRAL_API_KEY` (via `AIDistiller`) | Mistral chat API |
| `save_distillation_results` | 431-474 | None | Filesystem |
| `generate_final_json` | 268-335 | None | Filesystem |

## Cross-module dependencies

```
workflow.py → client.py, parser.py, config.py, models.py, downloader.py, extractor.py, converter.py, ai_distiller.py
client.py   → config.py, models.py, parser.py
downloader.py → (standalone, imports only stdlib + aiohttp)
extractor.py → downloader.py (get_company_folder_name)
converter.py → config.py, downloader.py (get_company_folder_name), mistralai plugins
ai_distiller.py → config.py, models.py, prompts/dividends_parsing.md
```

No circular imports exist. `config.py` and `models.py` are leaf modules with no intra-package imports.

## Per-file editing rules

### config.py
- Single source of truth for all tuning knobs. Do not hardcode retry/timeout/concurrency values elsewhere.
- Adding a new constant: group it under the appropriate section comment (Concurrency, AI, etc.).

### models.py
- Edit this first when changing the data schema. Then update all consumers.
- `DividendData` and its nested models (`SharePayout`, `PaymentDeadline`, `DecisionDate`, `PaymentPeriod`) define the AI output schema — changes here must be compatible with the prompt in `prompts/dividends_parsing.md`.

### parser.py
- `parse_items()`: extracts `DividendRecord` from raw HTML elements. If the target site changes markup, update the `HREF_RE` regex and the extraction logic here.
- `transform_to_output()`: groups by lowercase company name, deduplicates URLs, preserves original case from first occurrence.

### client.py
- `AiohttpSessionManager`: async context manager providing connection pooling, circuit breaker, and adaptive rate limiting. Use this for batch operations.
- `AiohttpClient`: makes actual HTTP requests. Supports both standalone mode (own session) and pooled mode (via session manager).
- Retry logic is per-page inside `_fetch_and_parse()`. The circuit breaker only triggers on patterns across pages, not individual retry loops.
- `CircuitBreaker`: opens after `CIRCUIT_BREAKER_MAX_FAILURES` consecutive failures, reopens after `CIRCUIT_BREAKER_RESET_TIMEOUT` seconds.
- `RateLimiter`: adjusts delay based on 429 Retry-After headers and success/failure patterns.

### downloader.py
- `get_company_folder_name()`: MD5 hash of lowercase company name. Used by downloader, extractor, converter, ai_distiller, and workflow. If you change this, all stages must be updated.
- `download_file()`: streaming download with atomic write (temp file + rename). Retries up to `DOWNLOAD_RETRIES` with exponential backoff.
- All files are lowercased via `get_filename_from_url()`.

### extractor.py
- `extract_archive()`: atomic extraction pattern — extract to temp dir, move files to parent with lowercase names, remove archive on success.
- Supported formats: `.zip`, `.tar`, `.gz`, `.tgz`, `.tar.gz`. Add new extensions to `ARCHIVE_EXTENSIONS` set.
- Extracted files are renamed to lowercase. Existing files are overwritten.

### converter.py
- Two-pass: non-PDF files first (via `convert_company_files`), then PDFs (via `process_pdf_files`).
- Non-PDF: `convert_to_markdown()` dispatches on extension (`.docx`, `.doc`, `.xls`). Returns `"IS_PDF"` marker for PDF files (handled in second pass).
- PDF: reads bytes, base64 encodes, creates `data:application/pdf;base64,...` URI, passes to `mistralai_ocr`. Size guard at `MAX_PDF_SIZE_BYTES` (50 MiB).
- Source cleanup: if `cleanup_source=True`, removes original files after successful MD conversion. Controlled by `CLEANUP_SOURCE_FILES` env var.

### ai_distiller.py
- `AIDistiller`: reusable instance with shared Mistral client and pre-rendered system prompt. Initialize once per activity execution.
- `process_single_file()`: processes one MD file through Mistral Large `chat.parse_async` with `DividendData` Pydantic model for structured output validation.
- Empty MD files: returned as `None` (not errors).
- Sequential processing: companies and files within companies are processed one at a time with configurable delays (`AI_FILE_DELAY`, `AI_COMPANY_DELAY`) to avoid rate limits.
- Retry: `AI_MAX_RETRIES` attempts with exponential backoff for retryable errors (503, 502, 429, timeout, overload).

### prompts/dividends_parsing.md
- Prompt template with `{{REFERENCE_DATE}}` and `{{DOCUMENT_TEXT}}` placeholders.
- Defines BYN currency rules, decision logic, field schemas, and self-check rules.
- Changes to the extraction schema in `models.py` (especially `DividendData`) must be reflected here.

### workflow.py
- `CentralDepoWorkflow.run()`: the main orchestration method. Calls activities in sequence. Do not add business logic here — delegate to domain modules.
- `_build_page_url()`: page 1 = base URL, page 2+ = `?PAGEN_1=<n>`.
- All env var reads are inside activities (sandbox restriction).

## Key patterns

- **Atomic writes**: `tempfile.mkstemp()` → write → `os.replace()`. Used in `workflow.py`, `downloader.py`, `extractor.py`. Always clean up temp file on error.
- **Company folder naming**: `hashlib.md5(name.lower().encode("utf-8")).hexdigest()`. Defined in `downloader.py:get_company_folder_name()`, imported by extractor, converter, ai_distiller, and workflow.
- **Lowercase normalization**: all filenames are lowercased (`downloader.py`, `extractor.py`). Company names are grouped by lowercase but original case is preserved in output (`parser.py`).
- **Concurrency via Semaphore**: each stage has its own `asyncio.Semaphore` — downloads (10), extractions (10), conversions (5), scrapes (5).
