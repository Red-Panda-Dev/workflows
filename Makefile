#!make

# Install style libraries from pyproject.toml using uv
install-style:
	uv sync --extra style || uv pip install .[style]

# Run Python code style and import checks without modifying files
lint:
	ruff format centraldepo-parser/ --check
	ruff check centraldepo-parser/

# Automatically refactor Python code: remove unused imports/vars and format
refactor: install-style
	ruff check --fix --unsafe-fixes \
				centraldepo-parser/
	ruff format centraldepo-parser/
