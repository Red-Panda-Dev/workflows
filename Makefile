#!/usr/bin/make

# Install style libraries from pyproject.toml using uv
install-style:
	uv sync --extra style || uv pip install .[style]

# Run Python code style and import checks without modifying files
lint:
	cd epfr-downloader && uv run ruff format src/ --check
	cd epfr-downloader && uv run ruff check src/

# Type-check EPFR sources. ty runs from the root tooling env, but import resolution
# uses the epfr-downloader runtime venv (the root env intentionally holds only ruff/ty).
type-check:
	uv run ty check --python epfr-downloader/.venv/bin/python epfr-downloader/

# Automatically refactor Python code: remove unused imports/vars and format
refactor: install-style
	cd epfr-downloader && uv run ruff check --fix src/
	cd epfr-downloader && uv run ruff format src/
