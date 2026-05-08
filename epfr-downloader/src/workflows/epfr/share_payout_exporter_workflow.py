"""Workflow entrypoint for EPFR share payout export."""

import logging

import mistralai.workflows as workflows

from . import config
from .config import resolve_share_payout_export_input
from .models import EpfrSharePayoutExportInput, EpfrSharePayoutExportOutput
from .share_payout_exporter import run_share_payout_export

logger = logging.getLogger(__name__)


@workflows.activity()
async def export_share_payouts(input: EpfrSharePayoutExportInput) -> dict:
    """Run share payout export activity."""
    logger.info(f"Activity export_share_payouts invoked: output_dir={input.output_dir}, csv={input.shares_csv_path}")
    resolved = resolve_share_payout_export_input(**input.model_dump(exclude_none=True, exclude={"shares_csv_path"}))
    resolved_input = EpfrSharePayoutExportInput(
        output_dir=resolved["output_dir"],
        input_filename=resolved["input_filename"],
        output_filename=resolved["output_filename"],
        shares_csv_path=input.shares_csv_path,
    )
    result = run_share_payout_export(resolved_input)
    logger.info(
        f"Activity export_share_payouts finished: {result.get('matched_payouts', 0)} matched, "
        f"{result.get('unmatched_payouts', 0)} unmatched, {result.get('total_companies_exported', 0)} companies"
    )
    return result


@workflows.workflow.define(
    name=config.SHARE_PAYOUT_EXPORT_WORKFLOW_NAME,
    workflow_display_name="EPFR Share Payout Exporter",
    workflow_description="Exports share payout data from distilled dividends joined with share reference CSV.",
)
class EpfrSharePayoutExporterWorkflow:
    """Export share payout data as DB-ready JSON keyed by UNP."""

    @workflows.workflow.entrypoint
    async def run(self, input: EpfrSharePayoutExportInput) -> EpfrSharePayoutExportOutput:
        """Execute the share payout export workflow."""
        logger.info(f"Workflow {config.SHARE_PAYOUT_EXPORT_WORKFLOW_NAME} started: output_dir={input.output_dir}")
        stats = await export_share_payouts(input)
        output = EpfrSharePayoutExportOutput(
            output_path=str(stats.get("output_path", "")),
            total_companies=int(stats.get("total_companies_exported", 0)),
            total_payouts=int(stats.get("matched_payouts", 0)) + int(stats.get("unmatched_payouts", 0)),
            matched_payouts=int(stats.get("matched_payouts", 0)),
            unmatched_payouts=int(stats.get("unmatched_payouts", 0)),
            stats=stats,
        )
        logger.info(
            f"Workflow {config.SHARE_PAYOUT_EXPORT_WORKFLOW_NAME} finished: {output.matched_payouts} matched, "
            f"{output.unmatched_payouts} unmatched, output={output.output_path}"
        )
        return output
