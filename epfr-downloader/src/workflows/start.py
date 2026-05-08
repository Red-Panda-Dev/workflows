"""Trigger a workflow execution from the command line."""

# ruff: noqa: E402

import argparse
import asyncio
import importlib
import json

from dotenv import load_dotenv

if __package__ in {None, ""}:
    _config = importlib.import_module("epfr.config")
else:
    _config = importlib.import_module("workflows.epfr.config")

get_dotenv_path = _config.get_dotenv_path
load_epfr_config = _config.load_epfr_config
require_mistral_api_key = _config.require_mistral_api_key

load_dotenv(get_dotenv_path(), override=True)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for triggering an EPFR workflow run.

    Returns:
        Parsed workflow name and JSON input payload string.

    """
    parser = argparse.ArgumentParser(
        description="Trigger a workflow execution.",
    )
    parser.add_argument(
        "--workflow",
        default="epfr-files-downloader",
        help="Name of the workflow to execute",
    )
    parser.add_argument(
        "--input",
        default=r"{}",
        help=r'Input data as a JSON string (e.g. \'{"max_pages": 10}\')',
    )
    return parser.parse_args()


async def main() -> None:
    """Trigger a deployed workflow and wait for completion.

    Reads runtime configuration from environment variables, validates the JSON
    input object, and submits it to the Mistral workflow client.

    Raises:
        SystemExit: If JSON input is invalid or the API key is missing.

    """
    args = parse_args()
    workflow_name = args.workflow

    try:
        raw_input = json.loads(args.input)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Error: invalid JSON for --input: {exc.args[0]}\n"
            f"  Received: {args.input!r}\n"
            f"  Example:  --input '{{\"max_pages\": 10}}'"
        ) from exc

    raw_input = raw_input or {}
    if not isinstance(raw_input, dict):
        raise SystemExit(f"Error: --input must be a JSON object, got {type(raw_input).__name__}")

    cfg = load_epfr_config()

    try:
        api_key = require_mistral_api_key(cfg)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    from mistralai.workflows.client import get_mistral_client

    client = get_mistral_client(
        api_key=api_key,
        server_url=cfg.server_url,
    )

    await client.workflows.execute_workflow_and_wait_async(
        workflow_identifier=workflow_name,
        input=raw_input,
        deployment_name=cfg.deployment_name,
    )


if __name__ == "__main__":
    asyncio.run(main())
