.PHONY: start-worker execute installdeps lint test

## Install dependencies
installdeps:
	uv sync

## Auto-discover all workflows and start the worker
start-worker: installdeps
	uv run python src/discover.py

## Trigger a workflow execution
## Usage: make execute input='{"max_pages": 10, "date_from": "2026-03-01"}'
execute:
	uv run python src/workflows/start.py --workflow epfr-files-downloader $(if $(input),--input '$(input)',)

## Run linter
lint:
	uv run ruff format src/ --check
	uv run ruff check src/

## Auto-fix lint issues
refactor:
	uv run ruff format src/
	uv run ruff check --fix src/

## Run tests
test:
	uv run pytest src/ -v
