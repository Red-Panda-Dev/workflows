"""TDD tests for env-backed config migration (config.py).

These tests encode the *intended* behaviour of the future config loader.
They MUST fail until Tasks 2–3 implement:
  - config.load_epfr_config()  → returns an immutable dataclass
  - config.EPFR_DEFAULTS       → canonical .env.example defaults
  - config.get_dotenv_path()   → explicit epfr-downloader/.env Path
"""

# ruff: noqa: D102

import importlib
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers – safe module reload with full env isolation
# ---------------------------------------------------------------------------

CONFIG_MODULE = "workflows.epfr.config"


@pytest.fixture()
def _clean_config(monkeypatch: pytest.MonkeyPatch):
    """Remove all EPFR_/MISTRAL_ env vars and force-reload config module."""
    for key in list(os.environ):
        if key.startswith(("EPFR_", "MISTRAL_")) or key in {"SERVER_URL", "DEPLOYMENT_NAME"}:
            monkeypatch.delenv(key, raising=False)
    mod = importlib.import_module(CONFIG_MODULE)
    importlib.reload(mod)
    yield mod
    importlib.reload(mod)


# ===================================================================
# 1. Typed parsing
# ====================================================================


class TestTypedParsing:
    """load_epfr_config() must return an immutable dataclass with correctly
    typed fields parsed from environment-variable strings."""

    def test_int_field(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_MAX_PAGES", "5")
        cfg = mod.load_epfr_config()
        assert isinstance(cfg.max_pages, int)
        assert cfg.max_pages == 5

    def test_float_field(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_PAGE_DELAY", "2.5")
        cfg = mod.load_epfr_config()
        assert isinstance(cfg.page_delay, float)
        assert cfg.page_delay == 2.5

    def test_str_field(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_AI_MODEL", "mistral-small-latest")
        cfg = mod.load_epfr_config()
        assert isinstance(cfg.ai_model, str)
        assert cfg.ai_model == "mistral-small-latest"

    def test_bool_field_true(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_CLEANUP_SOURCE", "true")
        cfg = mod.load_epfr_config()
        assert cfg.cleanup_source is True

    def test_bool_field_false(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_CLEANUP_SOURCE", "false")
        cfg = mod.load_epfr_config()
        assert cfg.cleanup_source is False

    def test_path_field(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_OUTPUT_DIR", "/tmp/epfr_test_output")
        cfg = mod.load_epfr_config()
        assert isinstance(cfg.output_dir, Path)
        assert cfg.output_dir == Path("/tmp/epfr_test_output")


# ===================================================================
# 2. Env override precedence
# ====================================================================


class TestEnvOverridePrecedence:
    """Environment variable > .env.example default > code fallback."""

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_MAX_PAGES", "3")
        cfg = mod.load_epfr_config()
        assert cfg.max_pages == 3  # overrode .env.example default of 10

    def test_default_used_when_no_env(self, _clean_config):
        mod = _clean_config
        cfg = mod.load_epfr_config()
        # No EPFR_MAX_PAGES set → should fall back to .env.example canonical (10)
        assert cfg.max_pages == 10

    def test_server_url_env_override(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("SERVER_URL", "https://custom.api.example.com")
        cfg = mod.load_epfr_config()
        assert cfg.server_url == "https://custom.api.example.com"

    def test_server_url_default_when_no_env(self, _clean_config):
        mod = _clean_config
        cfg = mod.load_epfr_config()
        assert cfg.server_url == "https://api.mistral.ai"

    def test_deployment_name_env_override(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("DEPLOYMENT_NAME", "production")
        cfg = mod.load_epfr_config()
        assert cfg.deployment_name == "production"

    def test_deployment_name_default_when_no_env(self, _clean_config):
        mod = _clean_config
        cfg = mod.load_epfr_config()
        assert cfg.deployment_name == "default"

    def test_dotenv_example_values_are_defaults(self, _clean_config):
        mod = _clean_config
        defaults = mod.EPFR_DEFAULTS
        # Spot-check canonical .env.example values
        assert defaults.max_pages == 10
        assert defaults.page_delay == 1.0
        assert defaults.first_page_no == 0
        assert defaults.ai_model == "ministral-8b-latest"
        assert defaults.ocr_model == "mistral-ocr-latest"
        assert defaults.max_concurrent_downloads == 10
        assert defaults.download_timeout == 120
        assert defaults.max_pdf_size_bytes == 52428800


# ===================================================================
# 3. Malformed env rejection
# ====================================================================


class TestMalformedValues:
    """Deterministic exceptions with key name in message on bad values."""

    def test_non_numeric_int(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_MAX_PAGES", "abc")
        with pytest.raises(Exception) as exc_info:
            mod.load_epfr_config()
        assert "EPFR_MAX_PAGES" in str(exc_info.value)

    def test_negative_size(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_MAX_PDF_SIZE_BYTES", "-1")
        with pytest.raises(Exception) as exc_info:
            mod.load_epfr_config()
        assert "EPFR_MAX_PDF_SIZE_BYTES" in str(exc_info.value)

    def test_malformed_float(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_PAGE_DELAY", "0.5.5")
        with pytest.raises(Exception) as exc_info:
            mod.load_epfr_config()
        assert "EPFR_PAGE_DELAY" in str(exc_info.value)

    def test_malformed_bool(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_CLEANUP_SOURCE", "yes")
        with pytest.raises(Exception) as exc_info:
            mod.load_epfr_config()
        assert "EPFR_CLEANUP_SOURCE" in str(exc_info.value)

    def test_negative_timeout(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_AI_TIMEOUT", "-10")
        with pytest.raises(Exception) as exc_info:
            mod.load_epfr_config()
        assert "EPFR_AI_TIMEOUT" in str(exc_info.value)

    def test_zero_retries(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_AI_MAX_RETRIES", "0")
        with pytest.raises(Exception) as exc_info:
            mod.load_epfr_config()
        assert "EPFR_AI_MAX_RETRIES" in str(exc_info.value)


# ===================================================================
# 4. Missing secret behaviour
# ====================================================================


class TestMissingSecretBehavior:
    """MISTRAL_API_KEY missing → only fails when a secret-consuming path
    requests it, NOT on import or load_epfr_config()."""

    def test_import_without_secret(self, _clean_config):
        """Importing config must not raise even with no MISTRAL_API_KEY."""
        mod = _clean_config
        # Should succeed — no exception on import
        assert hasattr(mod, "load_epfr_config")

    def test_load_config_without_secret(self, _clean_config):
        """load_epfr_config() must succeed without MISTRAL_API_KEY."""
        mod = _clean_config
        cfg = mod.load_epfr_config()
        # mistral_api_key should be None or empty — NOT raise
        assert getattr(cfg, "mistral_api_key", None) is None or getattr(cfg, "mistral_api_key", "") == ""

    def test_secret_required_on_execution_path(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        """A dedicated accessor (e.g. require_mistral_api_key) must raise
        when the key is absent."""
        mod = _clean_config
        # Remove the key explicitly
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        cfg = mod.load_epfr_config()
        with pytest.raises(Exception) as exc_info:
            mod.require_mistral_api_key(cfg)
        assert "MISTRAL_API_KEY" in str(exc_info.value)

    def test_secret_present_does_not_raise(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key-123")
        cfg = mod.load_epfr_config()
        key = mod.require_mistral_api_key(cfg)
        assert key == "test-key-123"


# ===================================================================
# 5. Cwd-stable dotenv loading
# ====================================================================


class TestCwdStableDotenv:
    """get_dotenv_path() must resolve to the same absolute Path regardless
    of the current working directory."""

    def test_dotenv_path_from_repo_root(self, _clean_config):
        mod = _clean_config
        path = mod.get_dotenv_path()
        assert isinstance(path, Path)
        assert path.name == ".env"
        assert "epfr-downloader" in str(path)

    def test_dotenv_path_from_subdirectory(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        mod = _clean_config
        # Simulate running from a subdirectory
        monkeypatch.chdir("/tmp")
        path = mod.get_dotenv_path()
        assert isinstance(path, Path)
        assert path.name == ".env"
        assert "epfr-downloader" in str(path)

    def test_dotenv_path_is_absolute(self, _clean_config):
        mod = _clean_config
        path = mod.get_dotenv_path()
        assert path.is_absolute()


# ===================================================================
# 6. Default values match .env.example
# ====================================================================


class TestDefaultsMatchEnvExample:
    """All defaults returned by EPFR_DEFAULTS must match the canonical
    .env.example values exactly."""

    def _defaults(self, _clean_config):
        return _clean_config.EPFR_DEFAULTS

    # --- EPFR API ---

    def test_base_api_url(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.base_api_url == "https://epfr.gov.by/portal/reporting/securities-market"

    def test_file_download_url_template(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.file_download_url_template == "https://epfr.gov.by/portal/file/{record_id}/content"

    def test_default_search_query(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.default_search_query == "дивиденд"

    def test_default_sub_category_id(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.default_sub_category_id == 1

    def test_default_sort_field(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.default_sort_field == "realUploadDate"

    def test_default_sort_dir(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.default_sort_dir == "desc"

    # --- Pagination ---

    def test_max_pages(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.max_pages == 10

    def test_first_page_no(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.first_page_no == 0

    def test_page_delay(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.page_delay == 1.0

    # --- Download ---

    def test_max_concurrent_downloads(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.max_concurrent_downloads == 10

    def test_download_timeout(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.download_timeout == 120

    def test_download_retries(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.download_retries == 3

    def test_chunk_size(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.chunk_size == 8192

    # --- Retry ---

    def test_max_retries(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.max_retries == 3

    def test_retry_backoff_base(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.retry_backoff_base == 2

    def test_retry_backoff_max(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.retry_backoff_max == 30

    # --- OCR ---

    def test_max_concurrent_ocr(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.max_concurrent_ocr == 2

    def test_max_pdf_size_bytes(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.max_pdf_size_bytes == 52428800

    def test_ocr_model(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.ocr_model == "mistral-ocr-latest"

    # --- AI ---

    def test_ai_model(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.ai_model == "ministral-8b-latest"

    def test_ai_temperature(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.ai_temperature == 0.0

    def test_ai_timeout(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.ai_timeout == 60

    def test_ai_max_retries(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.ai_max_retries == 3

    def test_ai_retry_backoff_base(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.ai_retry_backoff_base == 2

    def test_ai_file_delay(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.ai_file_delay == 1

    # --- Client ---

    def test_server_url(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.server_url == "https://api.mistral.ai"

    def test_deployment_name(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.deployment_name == "default"

    # --- Output ---

    def test_output_dir(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.output_dir == Path("output")

    def test_mapping_filename(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.mapping_filename == "unp_file_mapping.json"

    def test_ai_distilled_filename(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.ai_distilled_filename == "ai_distilled_dividends.json"

    # --- Date ---

    def test_default_date_from(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.default_date_from == "2022-01-01"

    def test_default_date_to(self, _clean_config):
        d = self._defaults(_clean_config)
        assert d.default_date_to == ""

    # --- Config is immutable ---

    def test_config_dataclass_is_frozen(self, monkeypatch: pytest.MonkeyPatch, _clean_config):
        """The returned config must be immutable (frozen dataclass)."""
        mod = _clean_config
        monkeypatch.setenv("EPFR_MAX_PAGES", "7")
        cfg = mod.load_epfr_config()
        with pytest.raises(AttributeError):
            cfg.max_pages = 99  # type: ignore[misc]


# ===================================================================
# 7. Input resolution helpers
# ====================================================================


class TestInputResolution:
    """Resolution helpers fill omitted values from config."""

    def test_workflow_input_defaults(self, _clean_config):
        mod = _clean_config
        resolved = mod.resolve_workflow_input()
        assert resolved["max_pages"] == 10
        assert resolved["date_from"] == "2022-01-01"
        assert resolved["date_to"] == mod.date.today().isoformat()
        assert resolved["timeout"] == 60
        assert resolved["output_dir"] == "output"

    def test_workflow_input_explicit_wins(self, _clean_config):
        mod = _clean_config
        resolved = mod.resolve_workflow_input(max_pages=5, date_from="2026-01-01", date_to="2026-06-30")
        assert resolved["max_pages"] == 5
        assert resolved["date_from"] == "2026-01-01"
        assert resolved["date_to"] == "2026-06-30"
        assert resolved["timeout"] == 60

    def test_workflow_input_env_override(self, monkeypatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_MAX_PAGES", "3")
        resolved = mod.resolve_workflow_input()
        assert resolved["max_pages"] == 3

    def test_workflow_input_date_to_env_override(self, monkeypatch, _clean_config):
        mod = _clean_config
        monkeypatch.setenv("EPFR_DEFAULT_DATE_TO", "2026-12-31")
        resolved = mod.resolve_workflow_input()
        assert resolved["date_to"] == "2026-12-31"

    def test_ai_distiller_input_defaults(self, _clean_config):
        mod = _clean_config
        resolved = mod.resolve_ai_distiller_input()
        assert resolved["model_name"] == "ministral-8b-latest"
        assert resolved["temperature"] == 0.0
        assert resolved["max_retries"] == 3

    def test_share_payout_input_defaults(self, _clean_config):
        mod = _clean_config
        resolved = mod.resolve_share_payout_export_input()
        assert resolved["output_filename"] == "share_payouts_by_unp.json"

    def test_pdf_ocr_input_defaults(self, _clean_config):
        mod = _clean_config
        resolved = mod.resolve_pdf_ocr_input()
        assert resolved["cleanup_source"] is True
        assert resolved["mapping_filename"] == "unp_file_mapping.json"
