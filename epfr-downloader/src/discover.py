"""Auto-discover all workflow classes in src/workflows/ and start a worker."""

# ruff: noqa: E402

import asyncio
import importlib
import inspect
import logging
import pkgutil
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

import mistralai.workflows as workflows
from mistralai.workflows.core.definition.workflow_definition import (
    get_workflow_definition,
)


def discover_workflows() -> list[type]:
    """Scan the workflows package and return all workflow classes."""
    discovered = []
    package = importlib.import_module("workflows")

    def _scan_package(package_name: str, package_path: list) -> None:
        for _, modname, ispkg in pkgutil.iter_modules(package_path, prefix=f"{package_name}."):
            try:
                module = importlib.import_module(modname)
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if hasattr(obj, "__workflows_workflow_def"):
                        discovered.append(obj)

                if ispkg:
                    subpackage = importlib.import_module(modname)
                    _scan_package(modname, subpackage.__path__)
            except ImportError as e:
                logger = logging.getLogger(__name__)
                logger.warning("Failed to import %s: %s", modname, e)

    _scan_package("workflows", package.__path__)
    return discovered


async def main() -> None:
    """Async entry point to discover workflows and start the worker."""
    discovered = discover_workflows()

    if not discovered:
        sys.exit(1)

    [get_workflow_definition(wf).name for wf in discovered]

    await workflows.run_worker(discovered)


if __name__ == "__main__":
    asyncio.run(main())
