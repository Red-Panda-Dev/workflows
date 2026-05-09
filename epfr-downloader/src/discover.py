"""Auto-discover all workflow classes in src/workflows/ and start a worker."""

import asyncio
import importlib
import inspect
import logging
import pkgutil
import sys
from collections.abc import Sequence

from dotenv import load_dotenv

from workflows.epfr.config import get_dotenv_path

load_dotenv(get_dotenv_path(), override=True)

import mistralai.workflows as workflows  # noqa: E402
from mistralai.workflows.core.definition.workflow_definition import (  # noqa: E402
    get_workflow_definition,
)

logger = logging.getLogger(__name__)


def scan_package(package_name: str, package_path: Sequence[str], discovered: list[type]) -> None:
    """Recursively scans a package and its subpackages for workflow classes.

    Iterates through all modules in the package using pkgutil, imports each module,
    and inspects for classes that have the __workflows_workflow_def attribute,
    indicating they are workflow classes. For subpackages, recursively continues
    the scan.

    Args:
        package_name: The name of the package to scan.
        package_path: The path to the package.
        discovered: List to append discovered workflow classes to.
    """
    for _, modname, ispkg in pkgutil.iter_modules(package_path, prefix=f"{package_name}."):
        # Skip test modules/packages — they import workflow classes at module level
        # and would cause duplicate registrations with the same workflow name.
        if ".tests" in modname or modname.endswith(".tests"):
            continue
        try:
            module = importlib.import_module(modname)
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if hasattr(obj, "__workflows_workflow_def") and obj not in discovered:
                    discovered.append(obj)

            if ispkg:
                subpackage = importlib.import_module(modname)
                scan_package(modname, subpackage.__path__, discovered)
        except ImportError as e:
            logger.warning("Failed to import %s: %s", modname, e)


def discover_workflows() -> list[type]:
    """Scans the workflows package and returns all discovered workflow classes.

    Initializes an empty list for discovered workflows, imports the root workflows
    package, and kicks off the recursive package scanning process. Returns all
    classes that were identified as workflows during the scan.

    Returns:
        List of discovered workflow classes.
    """
    discovered: list[type] = []
    package = importlib.import_module("workflows")
    scan_package("workflows", package.__path__, discovered)
    return discovered


async def main() -> None:
    """Async entry point to discover workflows and start the worker.

    Orchestrates the workflow discovery process, validates that at least one
    workflow was found, logs the discovered workflow names, and starts the
    async worker to process them. Exits with error code 1 if no workflows
    are discovered.
    """
    discovered = discover_workflows()

    if not discovered:
        logger.error("No workflows discovered")
        sys.exit(1)

    workflow_names = [get_workflow_definition(wf).name for wf in discovered]
    logger.debug("Discovered workflows: %s", workflow_names)

    await workflows.run_worker(discovered)


if __name__ == "__main__":
    asyncio.run(main())
