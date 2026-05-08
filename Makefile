#!make

# Install style libraries from pyproject.toml using uv
install-style:
	uv sync --extra style || uv pip install .[style]

# Run Python code style and import checks without modifying files
lint:
	cd epfr-downloader && uv run ruff format src/ --check
	cd epfr-downloader && uv run ruff check src/

type-check:
	uv run ty check epfr-downloader/

# Automatically refactor Python code: remove unused imports/vars and format
refactor: install-style
	cd epfr-downloader && uv run ruff check --fix src/
	cd epfr-downloader && uv run ruff format src/
