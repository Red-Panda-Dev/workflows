"""Workflow entrypoint for EPFR AI dividend distillation."""

import logging

import mistralai.workflows as workflows

from .ai_distiller import run_ai_distillation
from .models import EpfrAiDistillerInput, EpfrAiDistillerOutput

logger = logging.getLogger(__name__)


@workflows.activity()
async def distill_epfr_dividends(input: EpfrAiDistillerInput) -> dict:
    """Run EPFR AI distillation activity over mapped markdown files."""
    logger.info(
        f"Activity distill_epfr_dividends invoked: output_dir={input.output_dir}, unps={input.unps}, model={input.model_name}"
    )
    result = await run_ai_distillation(input)
    logger.info(
        f"Activity distill_epfr_dividends finished: {result.get('successful', 0)}/{result.get('total_files', 0)} files ok, {result.get('failed', 0)} failed"
    )
    return result


@workflows.workflow.define(
    name="epfr-ai-distiller",
    workflow_display_name="EPFR AI Distiller",
    workflow_description="Extracts structured dividend payouts from mapped EPFR markdown files and saves JSON output.",
)
class EpfrAiDistillerWorkflow:
    """Run AI extraction for all mapped EPFR markdown documents."""

    @workflows.workflow.entrypoint
    async def run(self, input: EpfrAiDistillerInput) -> EpfrAiDistillerOutput:
        """Execute the separate EPFR AI distillation workflow."""
        logger.info(
            f"Workflow epfr-ai-distiller started: output_dir={input.output_dir}, mapping={input.mapping_filename}"
        )
        stats = await distill_epfr_dividends(input)
        output = EpfrAiDistillerOutput(
            output_path=str(stats.get("output_path", "")),
            total_companies=int(stats.get("total_companies", 0)),
            total_files=int(stats.get("total_files", 0)),
            successful=int(stats.get("successful", 0)),
            failed=int(stats.get("failed", 0)),
            stats=stats,
        )
        logger.info(
            f"Workflow epfr-ai-distiller finished: {output.successful}/{output.total_files} files ok, {output.total_companies} companies, output={output.output_path}"
        )
        return output
