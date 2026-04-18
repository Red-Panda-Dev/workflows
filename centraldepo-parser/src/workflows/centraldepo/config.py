"""Configuration and constants for CentralDepo workflow."""

from pathlib import Path

# Target website
BASE_URL = "https://www.centraldepo.by/uslugi/raskrytie-informatsii/reestr/dividends/"

# Cloudflare Browser Rendering API
SCRAPE_API = "https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/scrape"

# CSS selector for dividend items
SELECTOR = ".news-item"

# Default values
DEFAULT_MAX_PAGES = 10
DEFAULT_DELAY = 1.0
DEFAULT_TIMEOUT = 180

# Default output path
SCRIPT_DIR = Path(__file__).parent.parent.parent.parent
DEFAULT_OUTPUT = SCRIPT_DIR / "output" / "centraldepo_dividends.json"

# Cloudflare settings
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds, exponential backoff: 2, 4, 8...

# Document conversion settings
MAX_CONCURRENT_CONVERSIONS = 5  # Concurrent file-to-MD conversions
