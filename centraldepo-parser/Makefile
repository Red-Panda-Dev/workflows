.PHONY: start-worker execute installdeps execute-collect-assets execute-distill-dividends execute-pipeline

## Install dependencies
installdeps:
	uv sync

## Auto-discover all workflows and start the worker
start-worker: installdeps
	uv run python src/discover.py

## Trigger a workflow execution
## Usage: make execute workflow=hello-world input='{"name": "World"}'
execute:
	uv run python src/workflows/start.py $(if $(workflow),--workflow $(workflow),) $(if $(input),--input '$(input)',)

## Execute centraldepo-collect-assets workflow
## Usage: make execute-collect-assets input='{"max_pages": 10}'
execute-collect-assets:
	uv run python src/workflows/start.py --workflow centraldepo-collect-assets $(if $(input),--input '$(input)',)

## Execute centraldepo-distill-dividends workflow
## Usage: make execute-distill-dividends input='{"input_path": "output/centraldepo_dividends.json"}'
execute-distill-dividends:
	uv run python src/workflows/start.py --workflow centraldepo-distill-dividends $(if $(input),--input '$(input)',)

## Execute both workflows sequentially
## Usage: make execute-pipeline collect_input='{"max_pages": 10}' distill_input='{"input_path": "output/centraldepo_dividends.json"}'
execute-pipeline:
	uv run python src/workflows/start.py --workflow centraldepo-collect-assets $(if $(collect_input),--input '$(collect_input)',)
	uv run python src/workflows/start.py --workflow centraldepo-distill-dividends $(if $(distill_input),--input '$(distill_input)',)
