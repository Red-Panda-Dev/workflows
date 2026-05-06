"""Shared helpers for CentralDepo workflows."""

import json
import os
import tempfile
from pathlib import Path

from .config import BASE_URL
from .models import CompanyResult, WorkflowOutput


def build_page_url(page: int) -> str:
    """Build paginated URL for centraldepo dividends registry."""
    if page == 1:
        return BASE_URL
    return f"{BASE_URL}?PAGEN_1={page}"


def get_output_root(output_path: str) -> Path:
    """Return output root directory for a workflow output file path."""
    return Path(output_path).parent


def company_results_to_tuples(
    results: list[CompanyResult],
) -> list[tuple[str, str, list[str]]]:
    """Convert CompanyResult models to plain tuples."""
    return [(r.company_name, r.company_hash, r.urls) for r in results]


def workflow_output_to_json(output: WorkflowOutput, output_root: Path | None = None) -> dict:
    """Convert WorkflowOutput to JSON-serializable dictionary."""

    def collect_local_files(company_hash: str) -> list[str]:
        if output_root is None:
            return []

        folder_path = output_root / company_hash
        if not folder_path.exists() or not folder_path.is_dir():
            return []

        return sorted(item.name for item in folder_path.iterdir() if item.is_file())

    data = {
        "results": [
            {
                "company_name": r.company_name,
                "company_hash": r.company_hash,
                "urls": r.urls,
                "files": sorted(r.files) if r.files else collect_local_files(r.company_hash),
            }
            for r in output.results
        ],
        "stats": output.stats,
    }
    if output.download_stats:
        data["download_stats"] = output.download_stats
    if output.extraction_stats:
        data["extraction_stats"] = output.extraction_stats
    if output.conversion_stats:
        data["conversion_stats"] = output.conversion_stats
    if output.distillation_stats:
        data["distillation_stats"] = output.distillation_stats
    return data


def load_company_results(input_path: str) -> list[CompanyResult]:
    """Load company results from saved JSON output."""
    from .downloader import get_company_folder_name

    path = Path(input_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_results = payload.get("results", [])

    normalized_results = []
    for item in raw_results:
        if not item.get("company_hash") and item.get("company_name"):
            item = {**item, "company_hash": get_company_folder_name(item["company_name"])}
        normalized_results.append(CompanyResult.model_validate(item))

    return normalized_results


def atomic_write_json(data: dict, output_path: Path, temp_prefix: str) -> str:
    """Write JSON atomically using temp-file then rename."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(output_path.parent),
        prefix=temp_prefix,
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(output_path))
        return str(output_path.resolve())
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
