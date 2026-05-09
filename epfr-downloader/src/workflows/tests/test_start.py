"""Tests for CLI entrypoint parsing in start.py.

Covers parse_args() defaults and overrides, main() JSON validation,
null-input fallback, and mocked workflow execution.
"""

# ruff: noqa: D102

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_package_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = []
    return module


def _import_start():
    """Import (or re-import) the start module with load_dotenv patched out."""
    with patch("dotenv.load_dotenv"):
        import workflows.start as mod

        return mod


def _inject_mistral_client_mock(monkeypatch: pytest.MonkeyPatch, mock_client: MagicMock):
    """Inject a mock mistralai.workflows.client.get_mistral_client into sys.modules."""
    wf_module = _make_package_module("mistralai.workflows")
    client_module = types.ModuleType("mistralai.workflows.client")
    client_module.get_mistral_client = MagicMock(return_value=mock_client)

    monkeypatch.setitem(sys.modules, "mistralai.workflows", wf_module)
    monkeypatch.setitem(sys.modules, "mistralai.workflows.client", client_module)


# ===================================================================
# 1. parse_args — defaults and overrides
# ====================================================================


class TestParseArgs:
    """parse_args() should return correct argparse.Namespace values."""

    def test_parse_args_defaults(self, monkeypatch: pytest.MonkeyPatch):
        """Default workflow is epfr-files-downloader and default input is '{}'."""
        mod = _import_start()
        monkeypatch.setattr(sys, "argv", ["prog"])
        args = mod.parse_args()
        assert args.workflow == "epfr-files-downloader"
        assert args.input == "{}"

    def test_parse_args_custom_workflow(self, monkeypatch: pytest.MonkeyPatch):
        """--workflow flag overrides the default workflow name."""
        mod = _import_start()
        monkeypatch.setattr(sys, "argv", ["prog", "--workflow", "epfr-ai-distiller"])
        args = mod.parse_args()
        assert args.workflow == "epfr-ai-distiller"

    def test_parse_args_custom_input(self, monkeypatch: pytest.MonkeyPatch):
        """--input flag passes through the raw JSON string."""
        mod = _import_start()
        monkeypatch.setattr(sys, "argv", ["prog", "--input", '{"max_pages": 5}'])
        args = mod.parse_args()
        assert args.input == '{"max_pages": 5}'


# ===================================================================
# 2. main — JSON validation and execution
# ====================================================================


class TestMain:
    """main() validates JSON, handles edge cases, and drives execution."""

    @pytest.mark.anyio
    async def test_main_valid_json_object(self, monkeypatch: pytest.MonkeyPatch):
        """main() parses a valid JSON object and calls the Mistral client."""
        mod = _import_start()
        monkeypatch.setattr(sys, "argv", ["prog", "--input", '{"max_pages": 5}'])

        mock_cfg = MagicMock()
        mock_cfg.server_url = "https://api.example.com"
        mock_cfg.deployment_name = "test-deploy"

        mock_client = MagicMock()
        mock_client.workflows.execute_workflow_and_wait_async = AsyncMock()
        _inject_mistral_client_mock(monkeypatch, mock_client)

        with (
            patch.object(mod, "load_epfr_config", return_value=mock_cfg),
            patch.object(mod, "require_mistral_api_key", return_value="test-key"),
        ):
            await mod.main()

        mock_client.workflows.execute_workflow_and_wait_async.assert_awaited_once_with(
            workflow_identifier="epfr-files-downloader",
            input={"max_pages": 5},
            deployment_name="test-deploy",
        )

    def test_main_invalid_json_exits(self, monkeypatch: pytest.MonkeyPatch):
        """main() raises SystemExit on malformed JSON input."""
        mod = _import_start()
        monkeypatch.setattr(sys, "argv", ["prog", "--input", "not-json"])
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(mod.main())
        assert "invalid JSON" in str(exc_info.value)

    def test_main_non_object_json_exits(self, monkeypatch: pytest.MonkeyPatch):
        """main() raises SystemExit when JSON input is not an object."""
        mod = _import_start()
        monkeypatch.setattr(sys, "argv", ["prog", "--input", "[1,2,3]"])
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(mod.main())
        assert "JSON object" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_main_null_input_becomes_empty_dict(self, monkeypatch: pytest.MonkeyPatch):
        """main() treats JSON null as an empty dict (raw_input or {})."""
        mod = _import_start()
        monkeypatch.setattr(sys, "argv", ["prog", "--input", "null"])

        mock_cfg = MagicMock()
        mock_cfg.server_url = "https://api.example.com"
        mock_cfg.deployment_name = "test-deploy"

        mock_client = MagicMock()
        mock_client.workflows.execute_workflow_and_wait_async = AsyncMock()
        _inject_mistral_client_mock(monkeypatch, mock_client)

        with (
            patch.object(mod, "load_epfr_config", return_value=mock_cfg),
            patch.object(mod, "require_mistral_api_key", return_value="test-key"),
        ):
            await mod.main()

        mock_client.workflows.execute_workflow_and_wait_async.assert_awaited_once_with(
            workflow_identifier="epfr-files-downloader",
            input={},
            deployment_name="test-deploy",
        )
