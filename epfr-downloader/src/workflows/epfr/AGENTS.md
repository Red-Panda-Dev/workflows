# AGENTS.md — EPFR Workflow Package

Pipeline internals for the four EPFR workflows. Covers data flow between modules, activity boundaries, data contracts, and per-file editing rules.

## Workflow overview

Four independent workflows, each in its own entrypoint module:

| Workflow | Entry module | Activities | Purpose |
|----------|-------------|------------|---------|
| `epfr-files-downloader` | `workflow.py` | 5 (inline) | Fetch pages → download → extract → convert → produce mapping JSON |
| `epfr-ocr-converter` | `pdf_ocr_workflow.py` | 3 | OCR files (PDF, PNG, JPG, JPEG) in mapped folders → update mapping entries to `.md` |
| `epfr-ai-distiller` | `ai_distiller_workflow.py` | 3 | AI-extract structured dividends from `.md` files → produce distilled JSON |
| `epfr-share-payout-exporter` | `share_payout_exporter_workflow.py` | 4 | Join distilled JSON with share CSV → export JSON → generate SQL INSERTs |

### epfr-files-downloader pipeline stages

1. `fetch_all_pages` → paginate EPFR API → `list[EpfrRecord]`
2. `download_all_epfr_files` → download raw files to `output/<UNP>/` folders
3. `extract_all_epfr_archives` → extract ZIP/TAR/GZ archives, detect OOXML-in-ZIP
4. `convert_all_epfr_files` → docx/doc/xls/xlsx → Markdown, cleanup source files
5. `save_unp_mapping` → write `unp_file_mapping.json` with file lineage tracking

Early termination: fetching stops when API response has `last=True`.

### epfr-ocr-converter pipeline

1. Read `unp_file_mapping.json`
2. For each OCR-able entry (PDF, PNG, JPG, JPEG): base64 encode → Mistral OCR → write `.md`
3. Update mapping entries to point at `.md` files instead of originals
4. Optionally cleanup source files

### epfr-ai-distiller pipeline (3 activities)

1. `scan_ai_distiller_files` → read mapping JSON, collect `.md` entries to process (no AI calls)
2. `process_ai_distillation` → sequential Mistral Large structured extraction → per-company/file dividend data (no file I/O; 2 h activity timeout)
3. `finalize_ai_distillation` → atomic write of `ai_distilled_dividends.json`

### epfr-share-payout-exporter pipeline (4 activities)

1. `scan_share_payout_export` → load share reference CSV, build (unp, share_kind) → instrument index, load distilled JSON
2. `process_share_payout_matching` → flatten dividends, match against CSV index, skip ambiguous/autofilled/file-error entries
3. `finalize_share_payout_export` → atomic write of `share_payouts_by_unp.json`
4. `generate_share_payout_sql` → read `share_payouts_by_unp.json` → write `share_dividends_insert.sql`

SQL generation now runs **inside** the workflow (activity 4). The old standalone `generate_sql.py` has been removed.

## Module map

| File | Role | Key exports |
|------|------|-------------|
| `config.py` | All constants and tuning knobs | `BASE_API_URL`, `FIRST_PAGE_NO`, `MAX_CONCURRENT_*`, `AI_MODEL`, `OCR_MODEL`, `SHARE_DIVIDENDS_SQL_FILENAME`, retry/timeout defaults, output filenames |
| `models.py` | Pydantic data shapes for all 4 workflows | `EpfrRecord`, `EpfrApiResponse`, workflow I/O models, `EpfrDividendEntry`, `EpfrSharePayoutExportRow`, scan/process result models |
| `client.py` | HTTP client | `fetch_page()`, `download_all_files()`, `download_file()`, `_get_unp()` |
| `detector.py` | Magic-byte file detection | `SIGNATURES`, detection function |
| `extractor.py` | Archive extraction with OOXML detection | `extract_all_archives()`, `is_archive()`, `extract_zip()`, `extract_tar()` |
| `converter.py` | Document → Markdown conversion | `convert_all_files()`, `convert_to_markdown()` |
| `markdown_cleanup.py` | Remove token-heavy markdown artifacts | `clean_markdown_text()` |
| `pdf_ocr.py` | OCR conversion and mapping update | `ocr_mapping_files()`, `ocr_file_to_markdown()`, `mistralai_ocr()` (supports PDF, PNG, JPG, JPEG) |
| `pdf_ocr_workflow.py` | OCR workflow | `EpfrOcrConverter` class, 3 activities: `scan_ocr_entries`, `process_ocr_files`, `finalize_ocr_mapping` |
| `ai_distiller.py` | AI structured extraction helpers | `AIDistiller`, `normalize_and_fill_dividend` |
| `ai_distiller_workflow.py` | AI distiller workflow | `EpfrAiDistillerWorkflow` class, 3 activities: `scan_ai_distiller_files`, `process_ai_distillation`, `finalize_ai_distillation` |
| `share_payout_exporter.py` | CSV loading, dividend flattening, atomic export | `run_share_payout_export()`, `load_share_reference_index()`, `_make_csv_key()` |
| `share_payout_exporter_workflow.py` | Share payout export workflow | `EpfrSharePayoutExporterWorkflow` class, 4 activities: `scan_share_payout_export`, `process_share_payout_matching`, `finalize_share_payout_export`, `generate_share_payout_sql` |
| `prompts/dividends_parsing.md` | AI prompt template | Template with placeholders for document text and reference date |
| `workflow.py` | Main pipeline workflow | `EpfrFilesDownloader` class, 5 inline activities |

## Data contracts

### epfr-files-downloader

```
fetch_all_pages(input: EpfrWorkflowInput)
  output: list[EpfrRecord]

download_all_epfr_files(records: list[EpfrRecord], output_dir: str)
  output: dict  {total_files_attempted, successful, failed, failed_ids, file_map, by_unp}

extract_all_epfr_archives(output_dir: str, download_stats: dict)
  output: dict  {total_unps, total_archives, successful, failed, files_extracted, by_unp}

convert_all_epfr_files(output_dir: str, download_stats: dict)
  output: dict  {total_files_attempted, total_successful, total_failed, cleaned_up_files, by_unp}

save_unp_mapping(records, output_dir, download_stats, extraction_stats, conversion_stats)
  output: str  (path to unp_file_mapping.json)
```

### epfr-ocr-converter

```
scan_ocr_entries(input: EpfrOcrInput)
  output: OcrScanResult  {mapping_path, mapping_raw, total_unps_scanned, total_ocr_entries, work_items, by_unp, output_dir, mapping_filename, cleanup_source}

process_ocr_files(output_root: str, scan_result: OcrScanResult, overwrite: bool)
  output: OcrProcessResult  {updated_mapping, results, total_successful, total_failed, total_skipped, failed_files, skipped_files, cleaned_up_files}

finalize_ocr_mapping(output_root: str, mapping_filename: str, process_result: OcrProcessResult, cleanup_source: bool)
  output: dict  {mapping_path, total_ocr_entries, total_successful, total_failed, total_skipped, cleaned_up_files, failed_files, by_unp}
```

### epfr-ai-distiller

```
scan_ai_distiller_files(input: EpfrAiDistillerInput)
  output: AiDistillerScanResult   (mapping entries + collected markdown work items; no AI calls)

process_ai_distillation(scan_result: AiDistillerScanResult)
  output: AiDistillerProcessResult   (per-company/file extracted dividends; no file I/O; 2 h timeout)

finalize_ai_distillation(scan_result: AiDistillerScanResult, process_result: AiDistillerProcessResult)
  output: dict[str, Any]   {output_path, total_companies, total_files, successful, failed}  (atomic write of ai_distilled_dividends.json)
```

### epfr-share-payout-exporter

```
scan_share_payout_export(input: EpfrSharePayoutExportInput)
  output: SharePayoutScanResult   (CSV lookup index + loaded distilled data)

process_share_payout_matching(scan_result: SharePayoutScanResult)
  output: SharePayoutProcessResult   (matched export rows + unmatched/ambiguous stats)

finalize_share_payout_export(scan_result: SharePayoutScanResult, process_result: SharePayoutProcessResult)
  output: dict[str, Any]   {output_path, matched_payouts, unmatched_payouts, missing_csv_unp, ...}  (atomic write of share_payouts_by_unp.json)

generate_share_payout_sql(scan_result: SharePayoutScanResult, final_stats: dict[str, Any])
  output: dict[str, Any]   {sql_path, sql_records, ...}  (writes share_dividends_insert.sql)
```

## Activity boundaries

### workflow.py (epfr-files-downloader)

| Activity | Env vars read | External calls |
|----------|---------------|----------------|
| `fetch_all_pages` | None | HTTP (epfr.gov.by API) |
| `download_all_epfr_files` | None | HTTP (epfr.gov.by files), Filesystem |
| `extract_all_epfr_archives` | None | Filesystem |
| `convert_all_epfr_files` | None | Filesystem, subprocess (antiword/catdoc for .doc) |
| `save_unp_mapping` | None | Filesystem |

### pdf_ocr_workflow.py (epfr-ocr-converter)

| Activity | Env vars read | External calls |
|----------|---------------|----------------|
| `scan_ocr_entries` | None | Filesystem (read mapping) |
| `process_ocr_files` | None | Mistral OCR API, Filesystem |
| `finalize_ocr_mapping` | None | Filesystem (atomic mapping write) |

### ai_distiller_workflow.py (epfr-ai-distiller)

| Activity | Env vars read | External calls |
|----------|---------------|----------------|
| `scan_ai_distiller_files` | None | Filesystem (read mapping) |
| `process_ai_distillation` | `MISTRAL_API_KEY` (via Mistral client) | Mistral chat API |
| `finalize_ai_distillation` | None | Filesystem (atomic write) |

### share_payout_exporter_workflow.py (epfr-share-payout-exporter)

| Activity | Env vars read | External calls |
|----------|---------------|----------------|
| `scan_share_payout_export` | None | Filesystem (CSV + JSON read) |
| `process_share_payout_matching` | None | None (in-memory join) |
| `finalize_share_payout_export` | None | Filesystem (atomic JSON write) |
| `generate_share_payout_sql` | None | Filesystem (read JSON → write `.sql`) |

## Cross-module dependencies

```
workflow.py → client.py, config.py, models.py, extractor.py, converter.py
pdf_ocr_workflow.py → pdf_ocr.py, models.py (uses EpfrOcrInput, OcrScanResult, OcrProcessResult, OcrFileResult, OcrWorkItem)
ai_distiller_workflow.py → ai_distiller.py (AIDistiller, normalize_and_fill_dividend), config.py, models.py
share_payout_exporter_workflow.py → share_payout_exporter.py (load_share_reference_index, _make_csv_key), config.py, models.py
client.py → config.py, detector.py, models.py
extractor.py → (standalone, stdlib only)
converter.py → markdown_cleanup.py (standalone, uses python-docx, xlrd, openpyxl, docx2txt)
markdown_cleanup.py → (standalone, stdlib only)
pdf_ocr.py → config.py, markdown_cleanup.py, mistralai plugins
ai_distiller.py → config.py, models.py, prompts/dividends_parsing.md
share_payout_exporter.py → config.py, models.py
```

No circular imports. `config.py`, `models.py`, `detector.py`, `extractor.py`, and `markdown_cleanup.py` are leaf modules.

## Per-file editing rules

### config.py
- Single source of truth for all tuning knobs across all 4 workflows. Do not hardcode values elsewhere.
- Groups: API URLs, download concurrency, retry, OCR limits, AI model settings, output filenames, SQL output filename (`SHARE_DIVIDENDS_SQL_FILENAME`), share export paths.

### models.py
- Edit this first when changing any workflow's data schema. Contains all I/O models for all 4 workflows plus API response models and AI extraction models.
- `EpfrDividendEntry` has a `model_validator` enforcing business rules (period numbering, date ordering). Changes to the validation logic here affect both AI distillation output and test expectations.
- AI models (`EpfrDividendExtraction`, `EpfrAiDistilledFile`, `EpfrAiDistilledCompany`) define the structured extraction schema — must be compatible with `prompts/dividends_parsing.md`.
- `EpfrSharePayoutExportRow` defines the DB-ready payout format.
- Scan/process result models (`AiDistillerScanResult`, `AiDistillerProcessResult`, `SharePayoutScanResult`, `SharePayoutProcessResult`) are the inter-activity contracts for the multi-step workflows — changing them touches the activity call chain and tests.

### client.py
- `_get_unp()`: determines UNP from record (holder.unp → organization.unp fallback). Used by `workflow.py` for mapping.
- `fetch_page()`: builds query params for the EPFR API. Pagination is 0-based.
- `download_file()`: streaming download, saves as `<record_id><detected_ext>`. Retries with exponential backoff.

### detector.py
- `SIGNATURES`: ordered list — first match wins. Keep PDF before other overlapping signatures.

### extractor.py
- `is_archive()`: checks extension against `ARCHIVE_EXTENSIONS` set.
- `_detect_ooxml_type()`: inspects ZIP contents for OOXML markers. ZIPs that are actually docx/xlsx/pptx are renamed instead of extracted.
- `extract_zip()` / `extract_tar()`: preserve original filenames from archive. EPFR archives contain real filenames unlike the API downloads.
- `extract_all_archives()`: processes UNP folders in parallel with `MAX_CONCURRENT_EXTRACTS` semaphore.

### converter.py
- `convert_to_markdown()`: dispatches on extension (`.docx`, `.doc`, `.xls`, `.xlsx`). Returns `"IS_PDF"` for PDFs.
- `.doc` fallback chain: python-docx → docx2txt → antiword/catdoc (subprocess) → raw text decode.
- `convert_all_files()`: processes UNP folders, optionally cleans up source files after conversion.
- Uses `markdown_cleanup.clean_markdown_text()` for post-processing.

### markdown_cleanup.py
- `clean_markdown_text()`: removes image-only lines, collapses excessive blank lines, strips pipe-only rows.
- Used by both `converter.py` and `pdf_ocr.py` for consistent markdown output.

### pdf_ocr.py
- `ocr_file_to_markdown()`: reads file (PDF, PNG, JPG, JPEG), base64 encodes, submits to Mistral OCR as data URI with appropriate MIME type. Size guard at `MAX_PDF_SIZE_BYTES` (50 MiB).
- `ocr_mapping_files()`: reads mapping JSON, finds OCR-able entries (PDF, PNG, JPG, JPEG), OCRs them, updates mapping to point at `.md` files, optionally cleans up source files.
- `mistralai_ocr()`: thin wrapper converting dict payload to Mistral plugin's `OCRRequest` model.
- Concurrency: `MAX_CONCURRENT_OCR = 2` to avoid rate limits.
- Uses `markdown_cleanup.clean_markdown_text()` for output.
- Backward compatibility: `ocr_pdf_to_markdown` and `ocr_mapping_pdfs` are aliases to the new functions.

### ai_distiller.py
- `AIDistiller`: sequential processing of companies/files with configurable delays (`AI_FILE_DELAY`) to avoid rate limits.
- Retry: `AI_MAX_RETRIES` with exponential backoff for transient errors (503, 502, 429, timeout, overload).
- `autofilled_fields` tracking: records which fields were inferred vs explicitly extracted, for downstream quality review.
- Consumed by `ai_distiller_workflow.py`'s `process_ai_distillation` activity.

### share_payout_exporter.py
- `load_share_reference_index()`: parses CSV, builds (unp, share_kind) → instrument_uuid index, excludes ambiguous keys.
- `_make_csv_key()`: shared key builder, also imported by the workflow module.
- `run_share_payout_export()`: legacy single-call entrypoint (the workflow now uses the 4 separate activities instead).
- Atomic write pattern: `tempfile.mkstemp()` → write → `os.replace()`.

### share_payout_exporter_workflow.py
- Orchestrates 4 activities (scan → match → finalize JSON → generate SQL). Activities are defined inline in this module.
- `generate_share_payout_sql` reads the just-written `share_payouts_by_unp.json` and emits `share_dividends_insert.sql`. This replaced the removed standalone `generate_sql.py`.
- Atomic writes for both JSON and SQL outputs.

### prompts/dividends_parsing.md
- Prompt template for Mistral Large structured extraction.
- Changes to the extraction schema in `models.py` (especially `EpfrDividendEntry`) must be reflected here.

## Key patterns

- **Atomic writes**: `tempfile.mkstemp()` → write → `os.replace()`. Used in `workflow.py` (save_unp_mapping), `pdf_ocr_workflow.py` (finalize_ocr_mapping), `ai_distiller_workflow.py` (finalize_ai_distillation), `share_payout_exporter_workflow.py` (finalize JSON + generate SQL).
- **Multi-step activities for UI progress**: OCR (3), AI distiller (3), share payout exporter (4) each split scan/process/finalize so the Mistral Workflows UI can show per-step progress. Process steps do no file I/O; finalize steps own the atomic write.
- **File lineage tracking**: mapping entries carry `extracted_from` (archive → extracted file) and `converted_from` (source → .md) fields. `save_unp_mapping` resolves the full transformation chain.
- **UNP folder naming**: direct UNP string from API (not hashed). Files live in `output/<UNP>/`.
- **Cleanup-on-success**: converter and PDF OCR both support removing source files after successful transformation, controlled by `cleanup_source` flag.
- **Concurrency via Semaphore**: downloads (10), extractions (10), OCR (2). AI distillation and share export are sequential by design.

## Tests

18 test modules in `src/tests/` cover all major components. Run with `make test` from inside `epfr-downloader/`.

**Note:** `test_pdf_ocr.py` is skipped by default (requires Mistral OCR API credentials).

When modifying any module, run the corresponding test file:
- `client.py` → `test_client.py`
- `config.py` → `test_config.py`
- `detector.py` → `test_detector.py`
- `extractor.py` → `test_extractor.py`
- `converter.py` → `test_converter.py`
- `markdown_cleanup.py` → `test_markdown_cleanup.py`
- `pdf_ocr.py` → `test_pdf_ocr.py` (skipped by default), `test_pdf_ocr_mapping.py` (tests OCR for PDF, PNG, JPG, JPEG)
- `pdf_ocr_workflow.py` → `test_pdf_ocr_workflow.py` (tests OCR workflow for all supported types)
- `ai_distiller.py` → `test_ai_distiller.py`
- `ai_distiller_workflow.py` → `test_ai_distiller_workflow.py` (3 activities: scan/process/finalize)
- `share_payout_exporter.py` → `test_share_payout_exporter.py`
- `share_payout_exporter_workflow.py` → `test_share_payout_exporter_workflow.py` (4 activities incl. SQL generation)
- `models.py` → `test_models.py`
- `workflow.py` → `test_workflow_activities.py` (activities) + `test_workflow_wrappers.py` (wrapper + `save_unp_mapping`)
- `discover.py` / `start.py` → `test_start_cli.py`
- fixture completeness → `test_fixture_inventory.py`
