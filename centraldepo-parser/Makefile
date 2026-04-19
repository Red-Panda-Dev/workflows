.PHONY: start-worker execute installdeps

## Install dependencies
installdeps:
	uv sync

## Auto-discover all workflows and start the worker
start-worker:
	uv run python src/discover.py

## Trigger a workflow execution
## Usage: make execute workflow=hello-world input='{"name": "World"}'
execute:
	uv run python src/workflows/start.py $(if $(workflow),--workflow $(workflow),) $(if $(input),--input '$(input)',)
