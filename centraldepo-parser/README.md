# CentralDepo Dividend Parser

> **Business Purpose:** Automated scraping, downloading, and extraction of dividend disclosure records from CentralDepo (Belarusian Central Depository) for financial data aggregation and analysis.

A [Mistral Workflows](https://docs.mistral.ai/workflows/getting-started/introduction) project that scrapes paginated dividend disclosure records from [centraldepo.by](https://www.centraldepo.by/uslugi/raskrytie-informatsii/reestr/dividends/), groups entries by company, downloads archive files, extracts their contents, and saves structured results to JSON.

**Uses direct HTTP scraping with aiohttp and BeautifulSoup parsing** — no external service dependencies required.

## Business Overview

### Problem Solved
CentralDepo (The Central Depository of the Republic of Belarus) publishes dividend disclosure records across multiple paginated pages. Each record contains a company name and a link to an archive file (ZIP, TAR, GZ, etc.) containing detailed dividend information. Manually collecting this data is:
- **Time-consuming** — hundreds of pages with 10-50 entries each
- **Error-prone** — manual copy-paste misses entries
- **Incomplete** — no automatic file downloading or extraction

### Solution
This workflow automates the entire pipeline:
1. **Discovery** — Finds all dividend records across paginated pages
2. **Deduplication** — Removes duplicate company/URL combinations
3. **Grouping** — Organizes URLs by company with consistent naming
4. **Downloading** — Retrieves all archive files concurrently
5. **Extraction** — Extracts archive contents into organized folders
6. **Reporting** — Generates JSON output with comprehensive statistics

### Target Audience
- Financial analysts tracking Belarusian corporate dividend payments
- Data engineers building financial datasets
- Compliance teams monitoring disclosure requirements
- Investment researchers collecting structured dividend data

### Data Output
The workflow produces:
- **JSON metadata file** (`output/centraldepo_dividends.json`) — Company names, company hashes, archive URLs, and statistics
- **Company folders** — Each company's downloaded archives stored in `output/<md5_hash>/`
- **Extracted files** — Archive contents extracted into company folders

## Architecture

### Workflow Steps Diagram

```mermaid
graph TD
    A[Start: CentralDepoWorkflow.run()] --> B[Build Page URLs]
    B --> C[Loop Pages 1..N]
    C --> D[scrape_pages_batch]
    D -->|Success| E[Parse HTML Items]
    D -->|Failure| F[Retry up to 3x]
    F -->|All fail| C
    E --> G[Collect DividendRecords]
    G --> C
    C -->|Empty page| H[End pagination]
    H --> I[transform_to_output]
    I --> J[Group by Company]
    J --> K[Deduplicate URLs]
    K --> L[Sort Alphabetically]
    L --> M[save_results]
    M --> N[JSON output file]
    N --> O[download_all_results_files]
    O --> P[Concurrent downloads per company]
    P --> Q[Atomic file writes]
    Q --> R[download_stats]
    R --> S[extract_all_downloaded_archives]
    S --> T[Detect archive types]
    T --> U[Parallel extraction]
    U --> V[Atomic extraction: temp dir --> final]
    V --> W[extraction_stats]
    W --> X[Return WorkflowOutput]
    X --> Y[End]

    style A fill:#007acc,stroke:#005288,color:#fff
    style D fill:#4caf50,stroke:#388e3c,color:#fff
    style M fill:#4caf50,stroke:#388e3c,color:#fff
    style O fill:#4caf50,stroke:#388e3c,color:#fff
    style S fill:#4caf50,stroke:#388e3c,color:#fff
    style N fill:#ff9800,stroke:#e68a00,color:#000
    style R fill:#ff9800,stroke:#e68a00,color:#000
    style W fill:#ff9800,stroke:#e68a00,color:#000
    style X fill:#007acc,stroke:#005288,color:#fff
    style Y fill:#007acc,stroke:#005288,color:#fff
```

### Activity Breakdown

| Activity | Purpose | Input | Output |
|----------|---------|-------|--------|
| `scrape_pages_batch()` | Scrape pages in parallel via aiohttp | page_urls, timeout | List[ScrapeResult] |
| `scrape_single_page()` | Scrape one page (backward compatibility) | page, url, timeout | List[DividendRecord] or None |
| `save_results()` | Atomically write JSON output with results and stats | WorkflowOutput, output_path | Absolute file path |
| `download_all_results_files()` | Download all archives concurrently per company | List[CompanyResult], output_path | Dict download statistics |
| `extract_all_downloaded_archives()` | Extract all downloaded archives | List[CompanyResult], output_path | Dict extraction statistics |

### Component Structure

```text
centraldepo-parser/
├── src/
│   ├── discover.py              # Workflow auto-discovery & worker starter
│   ├── dev_worker.py            # Development auto-reload worker
│   └── workflows/
│       ├── __init__.py          # Package marker for discovery
│       ├── start.py             # CLI to trigger workflows
│       └── centraldepo/         # Core workflow package
│           ├── config.py        # Constants: BASE_URL, SELECTOR, retry settings
│           ├── models.py        # Pydantic models for data structures
│           ├── client.py        # HTTP client: AiohttpClient with aiohttp and BeautifulSoup
│           ├── parser.py        # HTML parsing & output transformation
│           ├── downloader.py    # Concurrent file download logic
│           ├── extractor.py     # Archive extraction handling
│           └── workflow.py      # Main workflow class & activities
├── output/                     # Generated output (gitignored)
├── Makefile                    # Run targets
├── pyproject.toml              # Dependencies & tool config
└── README.md                   # This file
```

## Data Model

### Input Schema (WorkflowInput)
```python
{
    "max_pages": int,        # 1-100, pages to scrape (default: 10)
    "delay": float,          # 0.0-10.0, seconds between pages (default: 1.0)
    "timeout": int,          # 10-600, request timeout seconds (default: 180)
    "output_path": str,      # JSON output path (default: "output/centraldepo_dividends.json")

}
```

### Output Schema (WorkflowOutput)
```python
{
    "results": [
        {
            "company_name": str,    # Company name (original case preserved)
            "company_hash": str,    # MD5 hash of lowercase company name (folder name)
            "urls": [str],          # Sorted, deduplicated archive URLs
            "files": [str]          # Local file names currently present in company folder
        }
    ],
    "stats": {
        "total_pages_scraped": int,
        "total_records": int,
        "companies_found": int,
        "duplicate_urls_removed": int
    },
    "download_stats": {
        "total_companies": int,
        "total_files": int,
        "successful": int,
        "failed": int,
        "failed_urls": [str],
        "by_company": {str: {...}}
    },
    "extraction_stats": {
        "total_companies": int,
        "total_archives": int,
        "successful": int,
        "failed": int,
        "failed_archives": [str],
        "files_extracted": int,
        "by_company": {str: {...}}
    }
}
```

## Setup

### Prerequisites
- Python 3.14.3+
- [uv](https://github.com/astral-sh/uv) package manager
- Mistral Workflows API key (for worker registration)


### Install Dependencies
```bash
cd centraldepo-parser
uv sync
```

### Environment Configuration
Create a `.env` file in the project root:
```bash
# Required for Mistral Workflows
MISTRAL_API_KEY=your_mistral_api_key_here

```

## Commands

### Register Workflows in AI Studio
Auto-discovers all workflow classes in `src/workflows/`, registers them with AI Studio, and starts polling for executions. The task queue uses your hostname:

```bash
make start-worker
```

### Execute a Workflow
In a separate terminal, trigger a workflow execution by name:

```bash
# Step 1: collect assets (parse pages, download archives, extract files)
make execute workflow=centraldepo-collect-assets

# Step 1 with custom scrape configuration
make execute workflow=centraldepo-collect-assets input='{"max_pages": 50, "delay": 2.0, "timeout": 300}'

# Step 1 with custom output path
make execute workflow=centraldepo-collect-assets \
  input='{"max_pages": 20, "output_path": "output/dividends_2024.json"}'

# Step 2: convert extracted files to MD and distill dividends
make execute workflow=centraldepo-distill-dividends

# Step 2 for custom output path from Step 1
make execute workflow=centraldepo-distill-dividends \
  input='{"input_path": "output/dividends_2024.json"}'
```

### Development Commands
```bash
# Format code
uv run ruff format .

# Lint code
uv run ruff check .

# Auto-fix lint issues
uv run ruff check --fix . && uv run ruff format .

# Run standalone scraper (non-workflow version)
uv run python example.py --max-pages 10 --delay 1.0
```

## Usage Examples

### Example 1: Quick Test Run (aiohttp mode - default)
```bash
make execute workflow=centraldepo-collect-assets input='{"max_pages": 2, "timeout": 60}'
```
Scrapes first 2 pages with a 60-second timeout using direct HTTP — good for testing configuration.

### Example 2: Full Collection
```bash
make execute workflow=centraldepo-collect-assets \
  input='{"max_pages": 500, "delay": 2.0, "timeout": 300, "output_path": "output/full_dividends.json"}'

make execute workflow=centraldepo-distill-dividends \
  input='{"input_path": "output/full_dividends.json"}'
```
Comprehensive scrape with generous timeouts and delays using direct HTTP.

### Example 3: Incremental Update
```bash
# Check how many pages exist
make execute workflow=centraldepo-collect-assets input='{"max_pages": 100, "delay": 1.0}'
# Note the last page with results, then fetch more
make execute workflow=centraldepo-collect-assets input='{"max_pages": 200, "delay": 2.0}'

# Run conversion and AI distillation on latest collected output
make execute workflow=centraldepo-distill-dividends input='{"input_path": "output/centraldepo_dividends.json"}'
```

## Performance Characteristics

| Metric | Value | Notes |
|--------|-------|-------|
| Pages per minute | ~30-50 | Depends on delay setting and rate limits |
| Records per page | 10-50 | Varies by CentralDepo pagination |
| Concurrent downloads | Up to 10 | Configurable via MAX_CONCURRENT_DOWNLOADS |
| Concurrent extractions | Up to 10 | Configurable via MAX_CONCURRENT_EXTRACTS |
| Memory per download | ~8KB chunk | Streaming download, minimal memory usage |
| Retry logic | 3 attempts | Exponential backoff with jitter |

## Error Handling

The workflow implements robust error handling:

### Scraping Errors
- **Rate limiting (429)**: Respects Retry-After header, waits and retries
- **Server errors (5xx)**: Exponential backoff, up to 3 retries
- **Empty pages**: Stops pagination (assumes end of results)
- **3 consecutive failures**: Stops early to avoid wasting resources
- **Network errors**: Automatic retry with exponential backoff

### Error Handling
- HTTP errors (4xx/5xx from centraldepo.by) are handled gracefully

### Download Errors
- Failed URLs are tracked in `download_stats.failed_urls`
- Each URL gets 3 attempts with exponential backoff
- Completed downloads are never retried on subsequent runs

### Extraction Errors
- Failed archives are tracked in `extraction_stats.failed_archives`
- Supports: ZIP, TAR, GZ, TGZ, TAR.GZ
- Extracted files overwrite existing files with same name

## Output Directory Structure

After a successful run, the output directory contains:

```text
output/
├── centraldepo_dividends.json          # Main results file with metadata
└── <md5_hash_1>/                       # Company 1 folder (MD5 of lowercase name)
    ├── archive1.zip                    # Downloaded archive
    ├── archive2.tar.gz                 # Another downloaded archive
    ├── file1.pdf                       # Extracted file
    ├── file2.xlsx                      # Extracted file
    └── ...
├── <md5_hash_2>/                       # Company 2 folder
│   ├── ...
└── ...
```

The MD5-based folder names ensure:
- Consistent folder naming regardless of company name case
- Filesystem-safe names (no special characters)
- Easy correlation between folder and company via the JSON file

## Dependencies

### Runtime
- `mistralai-workflows>=3.0.0,<4` — Mistral Workflows SDK
- `pydantic` — Data validation and serialization
- `python-dotenv` — Environment variable loading
- `aiohttp>=3.8` — Async HTTP requests (both modes)
- `beautifulsoup4>=4.14.3` — HTML parsing (aiohttp mode)
- `lxml>=6.1.0` — Fast HTML/XML parser backend (aiohttp mode)

### Development
- `ruff>=0.11` — Linting and formatting
- `mypy>=1.15` — Type checking
- `pytest>=8.0` — Testing framework
- `watchdog>=4.0` — File watching for dev worker

### System Dependencies (Optional)
For document conversion of binary `.doc` files (Microsoft Word 97-2003 format), the workflow calls system tools directly via `subprocess`.

```bash
# Ubuntu/Debian
sudo apt-get install antiword catdoc
```

The `.doc` conversion fallback order is:
1. `python-docx` for mislabeled `.docx` files
2. `docx2txt` for alternate OOXML extraction
3. `antiword` or `catdoc` for true binary `.doc` files
4. Best-effort raw text decoding if no system tool is available

Without `antiword` or `catdoc`, binary `.doc` files still fall back to best-effort decoding, but extracted text quality may be poor.

## Troubleshooting

### Common Issues

**"No .news-item selector block in response"**
- CentralDepo may have changed their HTML structure
- Update `SELECTOR` in `config.py`

**"Rate limited" warnings**
- Increase `delay` parameter between page requests
- centraldepo.by may be rate limiting your IP

**"HTTP 4xx/5xx" errors**
- Verify the target URL is accessible via browser

**Download failures**
- Verify URLs in JSON output are accessible via regular browser
- Some URLs may be behind authentication or have expired

### Debug Mode
Enable verbose logging by setting the `LOGLEVEL` environment variable:
```bash
LOGLEVEL=DEBUG make execute workflow=centraldepo-collect-assets input='{"max_pages": 2}'
```

## Related Files

| File | Purpose |
|------|---------|
| `centraldepo-parser/AGENTS.md` | Project-specific conventions and change rules |
| `.agents/skills/workflows/SKILL.md` | Mistral Workflows SDK reference |
| Root `AGENTS.md` | Workspace-wide conventions and ruff config |
| `example.py` | Standalone scraper (mirrors workflow logic) |

## License

This project is part of a private workflow automation suite. Distribution and usage rights are governed by organizational policies.
