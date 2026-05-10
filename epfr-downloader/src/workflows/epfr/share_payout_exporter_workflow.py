"""Workflow for share payout export from distilled dividends.

The workflow is split into 3 activities for UI progress tracking:
1. scan_share_payout_export - Load CSV index and distilled JSON
2. process_share_payout_matching - Match dividends against CSV index
3. finalize_share_payout_export - Save export JSON output
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import mistralai.workflows as workflows

from . import config
from .config import get_shares_source_data_csv, resolve_share_payout_export_input
from .models import (
    EpfrSharePayoutExportInput,
    EpfrSharePayoutExportOutput,
    SharePayoutProcessResult,
    SharePayoutScanResult,
)
from .share_payout_exporter import load_share_reference_index

logger = logging.getLogger(__name__)


# =============================================================================
# Activity 1: Scan Share Payout Export
# =============================================================================


@workflows.activity()
async def scan_share_payout_export(input: EpfrSharePayoutExportInput) -> SharePayoutScanResult:
    """Load CSV index and distilled JSON for payout export.

    This is Step 1/3 of the Share Payout Exporter workflow. It loads the
    share reference CSV, builds the lookup index, and loads the distilled JSON.

    Args:
        input: Share payout export workflow input with output location and CSV path.

    Returns:
        SharePayoutScanResult containing CSV index, distilled data, and resolved paths.

    Raises:
        FileNotFoundError: If the CSV or distilled JSON file does not exist.

    """
    # Use input values directly if provided, otherwise resolve from config
    resolved = resolve_share_payout_export_input(
        output_dir=input.output_dir,
        input_filename=input.input_filename,
        output_filename=input.output_filename,
    )
    csv_path = Path(input.shares_csv_path) if input.shares_csv_path else get_shares_source_data_csv()

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    logger.info(f"Loading share reference CSV: {csv_path}")
    index, csv_stats = load_share_reference_index(csv_path)

    distilled_path = Path(resolved["output_dir"]) / resolved["input_filename"]
    if not distilled_path.exists():
        raise FileNotFoundError(f"Distilled JSON file not found: {distilled_path}")

    logger.info(f"Loading distilled JSON: {distilled_path}")
    with distilled_path.open(encoding="utf-8") as f:
        distilled_data = json.load(f)

    logger.info(f"Scan complete: {len(index)} CSV entries, {len(distilled_data)} companies in distilled data")

    return SharePayoutScanResult(
        csv_path=str(csv_path.resolve()),
        csv_index=index,
        csv_stats=csv_stats,
        distilled_path=str(distilled_path.resolve()),
        distilled_data=distilled_data,
        output_dir=resolved["output_dir"],
        output_filename=resolved["output_filename"],
    )


# =============================================================================
# Activity 2: Process Share Payout Matching
# =============================================================================


@workflows.activity()
async def process_share_payout_matching(
    scan_result: SharePayoutScanResult,
) -> SharePayoutProcessResult:
    """Match dividends against share reference CSV index.

    This is Step 2/3 of the Share Payout Exporter workflow. It processes
    all companies and files from the distilled data, matches each dividend
    against the CSV index, and builds the export data structure.

    Args:
        scan_result: Output from scan_share_payout_export activity.

    Returns:
        SharePayoutProcessResult with export data and matching stats.

    """
    csv_index = scan_result.csv_index
    csv_stats = scan_result.csv_stats
    distilled_data = scan_result.distilled_data

    logger.info(f"Starting payout matching for {len(distilled_data)} companies")

    export_data: dict[str, list[dict]] = {}
    matched_count = 0
    skipped_file_errors = 0
    autofilled_share_type = 0
    missing_csv_unp = 0
    missing_share_kind = 0
    ambiguous_share_kind = 0
    samples_missing_csv: list[dict] = []
    samples_missing_share_kind: list[dict] = []
    samples_ambiguous: list[dict] = []
    samples_autofilled: list[dict] = []
    samples_file_errors: list[dict] = []

    from .share_payout_exporter import _make_csv_key

    known_unps: set[str] = csv_stats["known_unps"]
    ambiguous_keys: set[str] = csv_stats["ambiguous_keys"]

    for unp_str, company_data in distilled_data.items():
        payouts: list[dict] = []
        for file_entry in company_data.get("files", []):
            if file_entry.get("error") is not None:
                for dividend in file_entry.get("dividends", []):
                    skipped_file_errors += 1
                    if len(samples_file_errors) < 10:
                        samples_file_errors.append(
                            {
                                "unp": unp_str,
                                "share_type": dividend.get("share_type", ""),
                                "file_id": file_entry.get("id"),
                            }
                        )
                continue

            autofilled = file_entry.get("autofilled_fields", [])
            file_id = file_entry.get("id")

            for dividend in file_entry.get("dividends", []):
                share_type = dividend.get("share_type", "")

                if "share_type" in autofilled:
                    autofilled_share_type += 1
                    if len(samples_autofilled) < 10:
                        samples_autofilled.append({"unp": unp_str, "share_type": share_type, "file_id": file_id})
                    continue

                key = _make_csv_key(unp_str, share_type)
                if key not in csv_index:
                    if key in ambiguous_keys:
                        ambiguous_share_kind += 1
                        if len(samples_ambiguous) < 10:
                            samples_ambiguous.append({"unp": unp_str, "share_type": share_type, "file_id": file_id})
                    elif unp_str not in known_unps:
                        missing_csv_unp += 1
                        if len(samples_missing_csv) < 10:
                            samples_missing_csv.append({"unp": unp_str, "share_type": share_type, "file_id": file_id})
                    else:
                        missing_share_kind += 1
                        if len(samples_missing_share_kind) < 10:
                            samples_missing_share_kind.append(
                                {"unp": unp_str, "share_type": share_type, "file_id": file_id}
                            )
                    continue

                from decimal import Decimal

                payouts.append(
                    {
                        "share_uuid": csv_index[key],
                        "period_year": dividend["period_year"],
                        "period_type": dividend["period_type"],
                        "period_number": dividend["period_number"],
                        "amount_per_share": str(Decimal(str(dividend["amount_per_share"]))),
                        "decision_date": dividend["decision_date"],
                        "record_date": dividend["record_date"],
                        "payment_date": dividend["payment_date"],
                    }
                )
                matched_count += 1

        if payouts:
            export_data[unp_str] = payouts

    logger.info(
        f"Matching complete: {matched_count} matched, {skipped_file_errors} file errors, "
        f"{autofilled_share_type} autofilled, {missing_csv_unp} missing UNPs, "
        f"{missing_share_kind} missing share kinds, {ambiguous_share_kind} ambiguous"
    )

    return SharePayoutProcessResult(
        export_data=export_data,
        matched_count=matched_count,
        skipped_file_errors=skipped_file_errors,
        autofilled_share_type=autofilled_share_type,
        missing_csv_unp=missing_csv_unp,
        missing_share_kind=missing_share_kind,
        ambiguous_share_kind=ambiguous_share_kind,
        samples={
            "missing_csv_unp": samples_missing_csv,
            "missing_share_kind": samples_missing_share_kind,
            "ambiguous_share_kind": samples_ambiguous,
            "autofilled_share_type": samples_autofilled,
            "skipped_file_errors": samples_file_errors,
        },
    )


# =============================================================================
# Activity 3: Finalize Share Payout Export
# =============================================================================


@workflows.activity()
async def finalize_share_payout_export(
    scan_result: SharePayoutScanResult,
    process_result: SharePayoutProcessResult,
) -> dict[str, Any]:
    """Save export JSON output after matching.

    This is Step 3/3 of the Share Payout Exporter workflow. It performs
    the atomic write of the share_payouts_by_unp.json file.

    Args:
        scan_result: Output from scan_share_payout_export activity.
        process_result: Output from process_share_payout_matching activity.

    Returns:
        Final stats dictionary with output_path and matching statistics.

    """
    output_root = Path(scan_result.output_dir)
    output_path = output_root / scan_result.output_filename

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=str(output_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(process_result.export_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(output_path))
        logger.info(f"Share payout export saved: {output_path}")
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"Failed to save export: {exc}") from exc

    total_unmatched = (
        process_result.skipped_file_errors
        + process_result.autofilled_share_type
        + process_result.missing_csv_unp
        + process_result.missing_share_kind
        + process_result.ambiguous_share_kind
    )

    # Build final stats matching existing run_share_payout_export output format
    stats: dict[str, Any] = {
        "output_path": str(output_path.resolve()),
        "matched_payouts": process_result.matched_count,
        "unmatched_payouts": total_unmatched,
        "missing_csv_unp": process_result.missing_csv_unp,
        "missing_share_kind": process_result.missing_share_kind,
        "ambiguous_share_kind": process_result.ambiguous_share_kind,
        "csv_ambiguous_share_kind": scan_result.csv_stats.get("ambiguous_share_kind", 0),
        "autofilled_share_type": process_result.autofilled_share_type,
        "skipped_file_errors": process_result.skipped_file_errors,
        "total_companies_exported": len(process_result.export_data),
        "unmatched_samples": process_result.samples,
    }

    logger.info(
        f"Share payout export complete: {stats['matched_payouts']} matched, "
        f"{stats['unmatched_payouts']} unmatched, {stats['total_companies_exported']} companies"
    )

    return stats


# =============================================================================
# Workflow Class (Orchestrator)
# =============================================================================


@workflows.workflow.define(
    name=config.SHARE_PAYOUT_EXPORT_WORKFLOW_NAME,
    workflow_display_name="EPFR Share Payout Exporter",
    workflow_description="Exports share payout data from distilled dividends joined with share reference CSV.",
)
class EpfrSharePayoutExporterWorkflow:
    """Export share payout data as DB-ready JSON keyed by UNP.

    The workflow is split into 3 activities for granular UI progress tracking:
    1. scan_share_payout_export: Load CSV index and distilled JSON
    2. process_share_payout_matching: Match dividends against CSV index
    3. finalize_share_payout_export: Save export JSON output

    This allows the Mistral Workflows UI to display progress as:
    - Step 1/3: Scanning CSV and distilled JSON...
    - Step 2/3: Matching dividends against share reference...
    - Step 3/3: Finalizing and saving export...
    """

    @workflows.workflow.entrypoint
    async def run(self, input: EpfrSharePayoutExportInput) -> EpfrSharePayoutExportOutput:
        """Run the share payout export workflow with 3 tracked steps.

        Coordinates the 3 activities to provide granular progress tracking
        in the UI while maintaining the same external API contract.

        Args:
            input: Share payout export workflow input containing output location and CSV path.

        Returns:
            Structured export output with totals, matched/unmatched counts, and raw stats.

        """
        logger.info(f"Workflow {config.SHARE_PAYOUT_EXPORT_WORKFLOW_NAME} started: output_dir={input.output_dir}")

        # Step 1/3: Scan CSV and distilled JSON
        logger.info("Starting Step 1/3: Scanning CSV and distilled JSON...")
        scan_result = await scan_share_payout_export(input)

        logger.info(
            f"Step 1/3 complete: {len(scan_result.csv_index)} CSV entries, {len(scan_result.distilled_data)} companies"
        )

        # Step 2/3: Match dividends against CSV index
        logger.info("Starting Step 2/3: Matching dividends against share reference...")
        process_result = await process_share_payout_matching(scan_result)

        logger.info(
            f"Step 2/3 complete: {process_result.matched_count} matched, "
            f"{process_result.skipped_file_errors} file errors"
        )

        # Step 3/3: Finalize and save
        logger.info("Starting Step 3/3: Finalizing and saving export...")
        final_stats = await finalize_share_payout_export(scan_result, process_result)

        logger.info("Step 3/3 complete: Export saved")

        output = EpfrSharePayoutExportOutput(
            output_path=str(final_stats.get("output_path", "")),
            total_companies=int(final_stats.get("total_companies_exported", 0)),
            total_payouts=int(final_stats.get("matched_payouts", 0)) + int(final_stats.get("unmatched_payouts", 0)),
            matched_payouts=int(final_stats.get("matched_payouts", 0)),
            unmatched_payouts=int(final_stats.get("unmatched_payouts", 0)),
            stats=final_stats,
        )

        logger.info(
            f"Workflow {config.SHARE_PAYOUT_EXPORT_WORKFLOW_NAME} finished: "
            f"{output.matched_payouts} matched, {output.unmatched_payouts} unmatched, "
            f"output={output.output_path}"
        )
        return output
