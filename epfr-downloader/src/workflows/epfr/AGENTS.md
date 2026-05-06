# AGENTS.md — EPFR Workflow Package

Pipeline internals for the three EPFR workflows. Covers data flow between modules, activity boundaries, data contracts, and per-file editing rules.

## Workflow overview

Three independent workflows, each in its own entrypoint module:

| Workflow | Entry module | Activities | Purpose |
|----------|-------------|------------|---------|
| `epfr-files-downloader` | `workflow.py` | 5 (inline) | Fetch pages → download → extract → convert → produce mapping JSON |
| `epfr-pdf-ocr-converter` | `pdf_ocr_workflow.py` | 1 (inline) | OCR PDFs in mapped folders → update mapping entries to `.md` |
| `epfr-ai-distiller` | `ai_distiller_workflow.py` | 1 (inline) | AI-extract structured dividends from `.md` files → produce distilled JSON |

### epfr-files-downloader pipeline stages

1. `fetch_all_pages` → paginate EPFR API → `list[EpfrRecord]`
2. `download_all_epfr_files` → download raw files to `output/<UNP>/` folders
3. `extract_all_epfr_archives` → extract ZIP/TAR/GZ archives, detect OOXML-in-ZIP
4. `convert_all_epfr_files` → docx/doc/xls/xlsx → Markdown, cleanup source files
5. `save_unp_mapping` → write `unp_file_mapping.json` with file lineage tracking

Early termination: fetching stops when API response has `last=True`.

### epfr-pdf-ocr-converter pipeline

1. Read `unp_file_mapping.json`
2. For each PDF entry: base64 encode → Mistral OCR → write `.md`
3. Update mapping entries to point at `.md` files instead of PDFs
4. Optionally cleanup source PDFs

### epfr-ai-distiller pipeline

1. Read `unp_file_mapping.json`
2. For each `.md` file: Mistral Large structured extraction → `EpfrDividendExtraction`
3. Write `ai_distilled_dividends.json` with per-company, per-file dividend data

## Module map

| File | Role | Key exports |
|------|------|-------------|
| `config.py` | All constants and tuning knobs | `BASE_API_URL`, `FIRST_PAGE_NO`, `MAX_CONCURRENT_*`, `AI_MODEL`, `OCR_MODEL`, retry/timeout defaults, output filenames |
| `models.py` | Pydantic data shapes for all 3 workflows | `EpfrRecord`, `EpfrApiResponse`, `EpfrWorkflowInput/Output`, `EpfrPdfOcrInput/Output`, `EpfrAiDistillerInput/Output`, `EpfrDividendEntry`, `EpfrAiDistilledCompany/File` |
| `client.py` | HTTP client | `fetch_page()`, `download_all_files()`, `download_file()`, `_get_unp()` |
| `detector.py` | Magic-byte file detection | `SIGNATURES`, detection function |
| `extractor.py` | Archive extraction with OOXML detection | `extract_all_archives()`, `is_archive()`, `extract_zip()`, `extract_tar()` |
| `converter.py` | Document → Markdown conversion | `convert_all_files()`, `convert_to_markdown()` |
| `pdf_ocr.py` | PDF OCR and mapping update | `ocr_mapping_pdfs()`, `ocr_pdf_to_markdown()`, `mistralai_ocr()` |
| `pdf_ocr_workflow.py` | PDF OCR workflow | `EpfrPdfOcrConverter` class, `ocr_epfr_mapping_pdfs` activity |
| `ai_distiller.py` | AI structured extraction | `run_ai_distillation()`, `EpfrAiDistiller` (internal), `_RawDividendEntry` (internal) |
| `ai_distiller_workflow.py` | AI distiller workflow | `EpfrAiDistillerWorkflow` class, `distill_epfr_dividends` activity |
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

### epfr-pdf-ocr-converter

```
ocr_epfr_mapping_pdfs(input: EpfrPdfOcrInput)
  output: dict  {mapping_path, total_pdf_entries, total_successful, total_failed, total_skipped, cleaned_up_files, failed_files}
```

### epfr-ai-distiller

```
distill_epfr_dividends(input: EpfrAiDistillerInput)
  output: dict  {output_path, total_companies, total_files, successful, failed}
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

### pdf_ocr_workflow.py

| Activity | Env vars read | External calls |
|----------|---------------|----------------|
| `ocr_epfr_mapping_pdfs` | None | Mistral OCR API, Filesystem |

### ai_distiller_workflow.py

| Activity | Env vars read | External calls |
|----------|---------------|----------------|
| `distill_epfr_dividends` | `MISTRAL_API_KEY` (via Mistral client) | Mistral chat API |

## Cross-module dependencies

```
workflow.py → client.py, config.py, models.py, extractor.py, converter.py
pdf_ocr_workflow.py → pdf_ocr.py, models.py
ai_distiller_workflow.py → ai_distiller.py, models.py
client.py → config.py, detector.py, models.py
extractor.py → (standalone, stdlib only)
converter.py → (standalone, uses python-docx, xlrd, openpyxl, docx2txt)
pdf_ocr.py → config.py, mistralai plugins
ai_distiller.py → config.py, models.py, prompts/dividends_parsing.md
```

No circular imports. `config.py`, `models.py`, `detector.py`, and `extractor.py` are leaf modules.

## Per-file editing rules

### config.py
- Single source of truth for all tuning knobs across all 3 workflows. Do not hardcode values elsewhere.
- Groups: API URLs, download concurrency, retry, OCR limits, AI model settings, output filenames.

### models.py
- Edit this first when changing any workflow's data schema. Contains all I/O models for all 3 workflows plus API response models and AI extraction models.
- `EpfrDividendEntry` has a `model_validator` enforcing business rules (period numbering, date ordering). Changes to the validation logic here affect both AI distillation output and test expectations.
- AI models (`EpfrDividendExtraction`, `EpfrAiDistilledFile`, `EpfrAiDistilledCompany`) define the structured extraction schema — must be compatible with `prompts/dividends_parsing.md`.

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

### pdf_ocr.py
- `ocr_pdf_to_markdown()`: reads PDF, base64 encodes, submits to Mistral OCR as data URI. Size guard at `MAX_PDF_SIZE_BYTES` (50 MiB).
- `ocr_mapping_pdfs()`: reads mapping JSON, finds PDF entries, OCRs them, updates mapping to point at `.md` files, optionally cleans up source PDFs.
- `mistralai_ocr()`: thin wrapper converting dict payload to Mistral plugin's `OCRRequest` model.
- Concurrency: `MAX_CONCURRENT_OCR = 2` to avoid rate limits.

### ai_distiller.py
- Processes companies and files sequentially with configurable delays (`AI_FILE_DELAY`) to avoid rate limits.
- Retry: `AI_MAX_RETRIES` with exponential backoff for transient errors (503, 502, 429, timeout, overload).
- `autofilled_fields` tracking: records which fields were inferred vs explicitly extracted, for downstream quality review.

### prompts/dividends_parsing.md
- Prompt template for Mistral Large structured extraction.
- Changes to the extraction schema in `models.py` (especially `EpfrDividendEntry`) must be reflected here.

## Key patterns

- **Atomic writes**: `tempfile.mkstemp()` → write → `os.replace()`. Used in `workflow.py` (save_unp_mapping), `pdf_ocr.py` (mapping update), `ai_distiller.py` (distilled output).
- **File lineage tracking**: mapping entries carry `extracted_from` (archive → extracted file) and `converted_from` (source → .md) fields. `save_unp_mapping` resolves the full transformation chain.
- **UNP folder naming**: direct UNP string from API (not hashed). Files live in `output/<UNP>/`.
- **Cleanup-on-success**: converter and PDF OCR both support removing source files after successful transformation, controlled by `cleanup_source` flag.
- **Concurrency via Semaphore**: downloads (10), extractions (10), OCR (2). No concurrency limit for AI distillation (sequential by design).

## Tests

7 test modules cover all major components. Run with `make test` from inside `epfr-downloader/`.

When modifying any module, run the corresponding test file:
- `client.py` → `test_client.py`
- `detector.py` → `test_detector.py`
- `extractor.py` → `test_extractor.py`
- `converter.py` → `test_converter.py`
- `pdf_ocr.py` → `test_pdf_ocr.py`
- `ai_distiller.py` → `test_ai_distiller.py`
- `models.py` → `test_models.py`
