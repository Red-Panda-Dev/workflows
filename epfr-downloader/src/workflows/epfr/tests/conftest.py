"""Shared EPFR test fixtures and helpers."""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from collections.abc import Callable
from typing import Any

pytest = importlib.import_module("pytest")


@pytest.fixture()
def epfr_fixtures_dir() -> Path:
    """Return the directory containing committed EPFR test fixtures."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture()
def load_epfr_fixture_json(epfr_fixtures_dir: Path) -> Callable[[str], Any]:
    """Load a JSON fixture from the EPFR fixtures directory."""

    def _load(name: str) -> Any:
        fixture_path = epfr_fixtures_dir / name
        if not fixture_path.exists():
            raise FileNotFoundError(f"Missing EPFR fixture: {name}")
        return json.loads(fixture_path.read_text(encoding="utf-8"))

    return _load


@pytest.fixture()
def load_epfr_fixture_text(epfr_fixtures_dir: Path) -> Callable[[str], str]:
    """Load a text fixture from the EPFR fixtures directory."""

    def _load(name: str) -> str:
        fixture_path = epfr_fixtures_dir / name
        if not fixture_path.exists():
            raise FileNotFoundError(f"Missing EPFR fixture: {name}")
        return fixture_path.read_text(encoding="utf-8")

    return _load


@pytest.fixture()
def copy_epfr_fixture(epfr_fixtures_dir: Path) -> Callable[[str, Path], Path]:
    """Copy a fixture into tmp_path and return the copied path."""

    def _copy(name: str, tmp_path: Path) -> Path:
        fixture_path = epfr_fixtures_dir / name
        if not fixture_path.exists():
            raise FileNotFoundError(f"Missing EPFR fixture: {name}")

        copied_path = tmp_path / name
        shutil.copy2(fixture_path, copied_path)
        return copied_path

    return _copy
