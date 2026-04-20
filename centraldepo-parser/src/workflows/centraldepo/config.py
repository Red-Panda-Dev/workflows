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
RETRY_BACKOFF_MAX = 30  # maximum backoff time in seconds

# Connection pooling
MAX_CONNECTIONS = 10
CONNECTION_TIMEOUT = 30

# Concurrency limits for Cloudflare API calls
# Respect Cloudflare rate limits (typically 5-10 concurrent requests allowed)
MAX_CONCURRENT_SCRAPES = 5  # Maximum concurrent page scrapes
BATCH_SIZE = 10  # Number of pages to request in a batch
SCRAPE_BATCH_DELAY = 0.5  # Delay between batches in seconds

# Circuit breaker settings for API health
CIRCUIT_BREAKER_MAX_FAILURES = 3
CIRCUIT_BREAKER_RESET_TIMEOUT = 60  # seconds before circuit reopens

# Adaptive rate limiting
INITIAL_DELAY = 0.5
MIN_DELAY = 0.1
MAX_DELAY = 10.0

# Document conversion settings
MAX_CONCURRENT_CONVERSIONS = 5  # Concurrent file-to-MD conversions

# AI Distillation settings
MAX_CONCURRENT_AI_REQUESTS = 3  # Concurrent Mistral Large API requests (rate limited)
AI_MODEL = "mistral-large-latest"  # Model identifier for AI distillation
AI_TIMEOUT = 60  # Seconds timeout for individual AI requests
AI_TEMPERATURE = 0.0  # Deterministic output for consistent results
AI_MAX_RETRIES = 3  # Retries for transient Mistral API errors (5xx)
AI_RETRY_BACKOFF_BASE = 2  # Exponential backoff base in seconds
