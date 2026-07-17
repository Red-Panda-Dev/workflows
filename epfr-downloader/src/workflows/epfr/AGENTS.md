# AGENTS.md — EPFR Workflow Package

Pipeline internals for the four EPFR workflows: data flow between modules, activity boundaries, data contracts, cross-module dependencies, and per-file editing rules.

## Scope and inheritance

Applies to: `epfr-downloader/src/workflows/epfr/` (the four workflow modules and their helpers). Inherits from `epfr-downloader/AGENTS.md` → root `AGENTS.md` (workspace layout, ruff/ty config, validation commands, and the architectural invariants: workflow sandbox, auto-discovery, **workflow isolation — communicate via `output/` artifacts only, no cross-workflow imports**). This file is the source of truth for activity boundaries, data contracts, and per-file editing rules; it contains no parent-rule overrides.

## Workflow overview

Four independent workflows, each in its own entrypoint module:

| Workflow | Entry module | Class | Activities | Produces |
|----------|-------------|-------|------------|----------|
| `epfr-files-downloader` | `workflow.py` | `EpfrFilesDownloader` | 5 (inline) | `unp_file_mapping.json` |
| `epfr-ocr-converter` | `pdf_ocr_workflow.py` | `EpfrOcrConverter` | 3 | mapping entries updated → `.md` |
| `epfr-ai-distiller` | `ai_distiller_workflow.py` | `EpfrAiDistillerWorkflow` | 3 | `ai_distilled_dividends.json` |
| `epfr-share-payout-exporter` | `share_payout_exporter_workflow.py` | `EpfrSharePayoutExporterWorkflow` | 4 | `share_payouts_by_unp.json` + `share_dividends_insert.sql` |

### epfr-files-downloader (5 activities)

1. `fetch_all_pages` → paginate EPFR API → `list[EpfrRecord]`
2. `download_all_epfr_files` → download raw files to `output/<UNP>/`
3. `extract_all_epfr_archives` → extract ZIP/TAR/GZ, detect OOXML-in-ZIP
4. `convert_all_epfr_files` → docx/doc/xls/xlsx → Markdown, optional source cleanup
5. `save_unp_mapping` → atomic write of `unp_file_mapping.json` with file lineage

Early termination: fetching stops when the API response has `last=True` (before `max_pages`).

### epfr-ocr-converter (3 activities)

1. `scan_ocr_entries` → read mapping JSON, collect OCR-able entries (PDF/PNG/JPG/JPEG)
2. `process_ocr_files` → base64-encode → Mistral OCR → write `.md`, update mapping
3. `finalize_ocr_mapping` → atomic mapping rewrite; optional source cleanup

### epfr-ai-distiller (3 activities)

1. `scan_ai_distiller_files` → read mapping JSON, collect `.md` work items (no AI calls)
2. `process_ai_distillation` → sequential Mistral structured extraction → per-company/file dividends (no file I/O; **2 h activity timeout**)
3. `finalize_ai_distillation` → atomic write of `ai_distilled_dividends.json`

### epfr-share-payout-exporter (4 activities)

1. `scan_share_payout_export` → load share reference CSV, build `(unp, share_kind) → instrument` index, load distilled JSON
2. `process_share_payout_matching` → flatten dividends, match against CSV index, skip ambiguous/autofilled/file-error entries (in-memory join)
3. `finalize_share_payout_export` → atomic write of `share_payouts_by_unp.json`
4. `generate_share_payout_sql` → read the just-written JSON → write `share_dividends_insert.sql`

SQL generation runs **inside** the workflow (activity 4). The old standalone `generate_sql.py` has been removed.

## Module map

| File | Role | Key exports |
|------|------|-------------|
| `config.py` | All constants & tuning knobs (`EpfrConfig` / `EPFR_DEFAULTS`; `EPFR_*` env-overridable) | `BASE_API_URL`, `FIRST_PAGE_NO` (=0), `MAX_CONCURRENT_DOWNLOADS` (10), `MAX_CONCURRENT_OCR` (2), `MAX_PDF_SIZE_BYTES` (50 MiB), `OCR_MODEL`, `OCR_SUPPORTED_EXTENSIONS`, `AI_MODEL` (`ministral-8b-latest`), `AI_MAX_RETRIES`, `AI_FILE_DELAY`, `SHARE_DIVIDENDS_SQL_FILENAME` |
| `models.py` | Pydantic shapes for all 4 workflows + API + AI extraction | `EpfrRecord`, `EpfrApiResponse`, workflow I/O models, `EpfrDividendEntry` (has `model_validator`), `EpfrSharePayoutExportRow`, scan/process result models |
| `client.py` | aiohttp HTTP client | `build_page_url()`, `build_download_url()`, `fetch_page()`, `download_file()`, `download_all_files()`, `_get_unp()` |
| `detector.py` | Magic-byte file detection | `SIGNATURES`, `_detect_ole2_extension()`, `detect_file_extension()`, `build_filename()` |
| `extractor.py` | Archive extraction + OOXML detection | `is_archive()`, `_detect_ooxml_type()`, `extract_zip()`, `extract_tar()`, `extract_archive()`, `extract_unp_archives()`, `extract_all_archives()`; local `MAX_CONCURRENT_EXTRACTS = 10` |
| `converter.py` | Document → Markdown | `convert_to_markdown()`, `convert_unp_files()`, `convert_all_files()` (+ private `_extract_docx/_extract_doc/_extract_xls/_extract_xlsx`) |
| `markdown_cleanup.py` | Remove token-heavy markdown artifacts | `clean_markdown_text()` |
| `pdf_ocr.py` | OCR conversion + mapping update | `mistralai_ocr()`, `ocr_file_to_markdown()`, `ocr_mapping_files()` (aliases `ocr_pdf_to_markdown` / `ocr_mapping_pdfs`) |
| `pdf_ocr_workflow.py` | OCR workflow | `EpfrOcrConverter`, activities: `scan_ocr_entries`, `process_ocr_files`, `finalize_ocr_mapping` |
| `ai_distiller.py` | AI structured-extraction helpers | `AIDistiller`, `normalize_and_fill_dividend()` |
| `ai_distiller_workflow.py` | AI distiller workflow | `EpfrAiDistillerWorkflow`, activities: `scan_ai_distiller_files`, `process_ai_distillation`, `finalize_ai_distillation` |
| `share_payout_exporter.py` | CSV loading, dividend flattening, atomic export | `_make_csv_key()`, `_parse_csv_key()`, `load_share_reference_index()`, `run_share_payout_export()` |
| `share_payout_exporter_workflow.py` | Share payout export workflow | `EpfrSharePayoutExporterWorkflow`, activities: `scan_share_payout_export`, `process_share_payout_matching`, `finalize_share_payout_export`, `generate_share_payout_sql` |
| `prompts/dividends_parsing.md` | AI prompt template | Placeholders for document text + reference date |
| `workflow.py` | Main pipeline workflow | `EpfrFilesDownloader`, 5 inline activities |

## Data contracts

### epfr-files-downloader

```
fetch_all_pages(input: EpfrWorkflowInput)
  -> list[EpfrRecord]

download_all_epfr_files(records: list[EpfrRecord], output_dir: str)
  -> dict  {total_files_attempted, successful, failed, failed_ids, file_map, by_unp}

extract_all_epfr_archives(output_dir: str, download_stats: dict)
  -> dict  {total_unps, total_archives, successful, failed, files_extracted, by_unp}

convert_all_epfr_files(output_dir: str, download_stats: dict)
  -> dict  {total_files_attempted, total_successful, total_failed, cleaned_up_files, by_unp}

save_unp_mapping(records, output_dir, download_stats, extraction_stats, conversion_stats)
  -> str  (path to unp_file_mapping.json)
```

### epfr-ocr-converter

```
scan_ocr_entries(input: EpfrOcrInput)
  -> OcrScanResult  {mapping_path, mapping_raw, total_unps_scanned, total_ocr_entries,
                     work_items, by_unp, output_dir, mapping_filename, cleanup_source}

process_ocr_files(output_root: str, scan_result: OcrScanResult, overwrite: bool)
  -> OcrProcessResult  {updated_mapping, results, total_successful, total_failed,
                         total_skipped, failed_files, skipped_files, cleaned_up_files}

finalize_ocr_mapping(output_root: str, mapping_filename: str, process_result: OcrProcessResult, cleanup_source: bool)
  -> dict  {mapping_path, total_ocr_entries, total_successful, total_failed,
            total_skipped, cleaned_up_files, failed_files, by_unp}
```

### epfr-ai-distiller

```
scan_ai_distiller_files(input: EpfrAiDistillerInput)
  -> AiDistillerScanResult   (mapping entries + collected markdown work items; no AI calls)

process_ai_distillation(scan_result: AiDistillerScanResult)
  -> AiDistillerProcessResult   (per-company/file extracted dividends; no file I/O; 2 h timeout)

finalize_ai_distillation(scan_result: AiDistillerScanResult, process_result: AiDistillerProcessResult)
  -> dict[str, Any]   {output_path, total_companies, total_files, successful, failed}
                      (atomic write of ai_distilled_dividends.json)
```

### epfr-share-payout-exporter

```
scan_share_payout_export(input: EpfrSharePayoutExportInput)
  -> SharePayoutScanResult   (CSV lookup index + loaded distilled data)

process_share_payout_matching(scan_result: SharePayoutScanResult)
  -> SharePayoutProcessResult   (matched export rows + unmatched/ambiguous stats)

finalize_share_payout_export(scan_result: SharePayoutScanResult, process_result: SharePayoutProcessResult)
  -> dict[str, Any]   {output_path, matched_payouts, unmatched_payouts, missing_csv_unp, ...}
                      (atomic write of share_payouts_by_unp.json)

generate_share_payout_sql(scan_result: SharePayoutScanResult, final_stats: dict[str, Any])
  -> dict[str, Any]   {sql_path, sql_records, ...}   (writes share_dividends_insert.sql)
```

## Activity boundaries

### workflow.py (epfr-files-downloader)

| Activity | Env vars read | External calls |
|----------|---------------|----------------|
| `fetch_all_pages` | None | HTTP (epfr.gov.by API) |
| `download_all_epfr_files` | None | HTTP (epfr.gov.by files), Filesystem |
| `extract_all_epfr_archives` | None | Filesystem |
| `convert_all_epfr_files` | None | Filesystem, subprocess (antiword/catdoc for legacy `.doc`) |
| `save_unp_mapping` | None | Filesystem (atomic write) |

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
workflow.py                 → client.py, config.py, models.py, extractor.py, converter.py
pdf_ocr_workflow.py         → pdf_ocr.py, models.py
ai_distiller_workflow.py    → ai_distiller.py, config.py, models.py
share_payout_exporter_workflow.py → share_payout_exporter.py, config.py, models.py
client.py                   → config.py, detector.py, models.py
converter.py                → markdown_cleanup.py (+ python-docx, xlrd, openpyxl, docx2txt)
pdf_ocr.py                  → config.py, markdown_cleanup.py, mistralai plugins
ai_distiller.py             → config.py, models.py, prompts/dividends_parsing.md
share_payout_exporter.py    → config.py, models.py
extractor.py / markdown_cleanup.py / detector.py / config.py / models.py  → leaf / near-leaf (stdlib + libs)
```

No circular imports. Leaf modules (no inbound business imports): `config.py`, `models.py`, `detector.py`, `extractor.py`, `markdown_cleanup.py`.

## Per-file editing rules

### config.py
- Single source of truth for all tuning knobs across the 4 workflows. Do not hardcode these values in workflow modules.
- Defined via the `EpfrConfig` dataclass and an `EPFR_DEFAULTS` instance; module-level constants (e.g. `MAX_CONCURRENT_OCR`) read from it. Every knob is overridable through an `EPFR_*` env var (e.g. `EPFR_AI_MODEL`, `EPFR_MAX_PDF_SIZE_BYTES`).
- Groups: API URLs, download concurrency, retry, OCR limits/extensions, AI model/retry/delay, output filenames, `SHARE_DIVIDENDS_SQL_FILENAME`, share export paths.

### models.py
- Edit this **first** when changing any workflow's data schema. Holds all I/O models for the 4 workflows plus API response models and AI extraction models.
- `EpfrDividendEntry` carries a `model_validator` enforcing business rules (period numbering, date ordering). Changing its validation affects both AI distillation output and test expectations.
- AI models (`EpfrDividendExtraction`, `EpfrAiDistilledFile`, `EpfrAiDistilledCompany`) define the structured-extraction schema — must stay compatible with `prompts/dividends_parsing.md`.
- `EpfrSharePayoutExportRow` defines the DB-ready payout format.
- Scan/process result models (`AiDistillerScanResult`/`ProcessResult`, `SharePayoutScanResult`/`ProcessResult`, `OcrScanResult`/`ProcessResult`) are the **inter-activity contracts** — changing them touches the activity call chain and tests.

### client.py
- `_get_unp()`: resolves UNP from a record (`holder.unp` → `organization.unp` fallback). Used by `workflow.py` for mapping.
- `fetch_page()` / `build_page_url()`: build EPFR API query params. Pagination is **0-based** (`FIRST_PAGE_NO = 0`).
- `download_file()` / `download_all_files()`: streaming download, saved as `<record_id><detected_ext>`. Retries with exponential backoff.

### detector.py
- `SIGNATURES`: ordered list — **first match wins**. Keep PDF before other overlapping signatures.
- `_detect_ole2_extension()`: OLE2 compound-document subtype detection (the API can return `.doc`/`.xls` as raw OLE2).

### extractor.py
- `is_archive()`: checks extension against `ARCHIVE_EXTENSIONS`.
- `_detect_ooxml_type()`: inspects ZIP contents for OOXML markers. ZIPs that are actually docx/xlsx/pptx are **renamed**, not extracted.
- `extract_zip()` / `extract_tar()`: preserve original filenames from the archive (EPFR archives contain real names, unlike API downloads).
- `extract_all_archives()`: processes UNP folders in parallel via a local `MAX_CONCURRENT_EXTRACTS = 10` semaphore (defined here, not in `config.py`).

### converter.py
- `convert_to_markdown()`: dispatches on extension (`.docx`, `.doc`, `.xls`, `.xlsx`). PDF/PNG/JPG/JPEG are **not** converted here — they are left for the OCR workflow.
- `.doc` fallback chain: python-docx → docx2txt → antiword/catdoc (subprocess) → raw text decode.
- `convert_all_files()`: processes UNP folders, optionally cleans up source files after conversion. Uses `markdown_cleanup.clean_markdown_text()` for post-processing.

### markdown_cleanup.py
- `clean_markdown_text()`: removes image-only lines, collapses excessive blank lines, strips pipe-only rows.
- Consumed by both `converter.py` and `pdf_ocr.py` for consistent markdown output — changes affect both pipelines.

### pdf_ocr.py
- `ocr_file_to_markdown()`: reads a file (PDF/PNG/JPG/JPEG), base64-encodes it, submits to Mistral OCR as a data URI with the correct MIME type (`application/pdf`, `image/png`, `image/jpeg`). Size guard at `MAX_PDF_SIZE_BYTES` (50 MiB).
- `ocr_mapping_files()`: reads the mapping JSON, finds OCR-able entries, OCRs them, updates entries to point at `.md` files, optionally cleans up sources.
- `mistralai_ocr()`: thin wrapper converting a dict payload to the Mistral plugin's `OCRRequest`.
- Concurrency: `MAX_CONCURRENT_OCR = 2` to avoid rate limits.
- Backward-compat aliases: `ocr_pdf_to_markdown = ocr_file_to_markdown`, `ocr_mapping_pdfs = ocr_mapping_files`.

### ai_distiller.py
- `AIDistiller`: sequential processing of companies/files with a configurable delay (`AI_FILE_DELAY`) to avoid rate limits.
- Model: `AI_MODEL` (default `ministral-8b-latest`; `mistral-large-latest` and others are accepted). Configurable per run via the workflow input `model_name`.
- Retry: `AI_MAX_RETRIES` with exponential backoff for transient errors (429/502/503, timeout, overload).
- `autofilled_fields` tracking: records which fields were inferred vs explicitly extracted, for downstream quality review.
- Consumed by `ai_distiller_workflow.py`'s `process_ai_distillation` activity.

### share_payout_exporter.py
- `load_share_reference_index()`: parses the CSV, builds `(unp, share_kind) → instrument_uuid` index, excludes ambiguous keys.
- `_make_csv_key()` / `_parse_csv_key()`: shared key builder/parser; `_make_csv_key` is also imported by the workflow module.
- `run_share_payout_export()`: legacy single-call entrypoint (the workflow now drives the 4 separate activities instead).
- Atomic write pattern: `tempfile.mkstemp()` → write → `os.replace()`.

### share_payout_exporter_workflow.py
- Orchestrates 4 activities (scan → match → finalize JSON → generate SQL), defined inline in this module.
- `generate_share_payout_sql` reads the just-written `share_payouts_by_unp.json` and emits `share_dividends_insert.sql` (replaced the removed standalone `generate_sql.py`).
- Atomic writes for both JSON and SQL outputs.

### prompts/dividends_parsing.md
- Prompt template for Mistral structured extraction.
- Any change to the extraction schema in `models.py` (especially `EpfrDividendEntry`) must be reflected here, and vice versa.

## Key patterns

- **Atomic writes**: `tempfile.mkstemp()` → write → `os.replace()`. Used by `save_unp_mapping`, `finalize_ocr_mapping`, `finalize_ai_distillation`, and the exporter's JSON + SQL finalizers.
- **Multi-step activities for UI progress**: OCR (3), AI distiller (3), share payout exporter (4) each split scan → process → finalize so the Mistral Workflows UI shows per-step progress. Process steps do no file I/O; finalize steps own the atomic write.
- **File lineage tracking**: mapping entries carry `extracted_from` (archive → extracted file) and `converted_from` (source → `.md`). `save_unp_mapping` resolves the full chain.
- **UNP folder naming**: direct UNP string from the API (not hashed). Files live in `output/<UNP>/`.
- **Cleanup-on-success**: converter and PDF OCR both support removing source files after a successful transformation, controlled by a `cleanup_source` flag.
- **Concurrency via semaphore**: downloads (10), extractions (10, local to `extractor.py`), OCR (2). AI distillation and share export are sequential by design.

## Tests

18 test modules in `src/tests/` mirror the source modules. Run with `make test` from inside `epfr-downloader/`.

**Note:** `test_pdf_ocr.py` is skipped by default (requires Mistral OCR API credentials) — see the root `AGENTS.md` gotcha.

Module → test mapping:

| Source module | Test file(s) |
|---------------|--------------|
| `workflow.py` | `test_workflow_activities.py` (activities) + `test_workflow_wrappers.py` (wrapper + `save_unp_mapping`) |
| `pdf_ocr_workflow.py` | `test_pdf_ocr_workflow.py` (3 activities, all supported types) |
| `ai_distiller_workflow.py` | `test_ai_distiller_workflow.py` (3 activities) |
| `share_payout_exporter_workflow.py` | `test_share_payout_exporter_workflow.py` (4 activities incl. SQL) |
| `client.py` / `config.py` / `detector.py` / `extractor.py` / `converter.py` / `markdown_cleanup.py` / `models.py` | `test_<module>.py` |
| `pdf_ocr.py` | `test_pdf_ocr.py` (skipped) + `test_pdf_ocr_mapping.py` (PDF/PNG/JPG/JPEG) |
| `ai_distiller.py` | `test_ai_distiller.py` |
| `share_payout_exporter.py` | `test_share_payout_exporter.py` |
| `discover.py` / `start.py` | `test_start_cli.py` |
| fixture completeness | `test_fixture_inventory.py` |
