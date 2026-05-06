"""Workflow for OCR-converting EPFR PDFs to markdown and updating mapping."""

import logging
from pathlib import Path

import mistralai.workflows as workflows

from .models import EpfrPdfOcrInput, EpfrPdfOcrOutput
from .pdf_ocr import ocr_mapping_pdfs

logger = logging.getLogger(__name__)


@workflows.activity()
async def ocr_epfr_mapping_pdfs(input: EpfrPdfOcrInput) -> dict:
    """Run the OCR mapping update activity for downloaded EPFR PDFs.

    Args:
        input: OCR workflow input with output location, overwrite behavior,
            cleanup behavior, and optional UNP filter.

    Returns:
        OCR statistics returned by the mapping update layer.
    """
    logger.info(
        f"Starting epfr-pdf-ocr-converter activity with output_dir={input.output_dir}, "
        f"mapping_filename={input.mapping_filename}, overwrite={input.overwrite}, "
        f"cleanup_source={input.cleanup_source}, unps={input.unps}"
    )
    output_root = Path(input.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    return await ocr_mapping_pdfs(
        output_root=output_root,
        mapping_filename=input.mapping_filename,
        overwrite=input.overwrite,
        cleanup_source=input.cleanup_source,
        unps=input.unps,
    )


@workflows.workflow.define(
    name="epfr-pdf-ocr-converter",
    workflow_display_name="EPFR PDF OCR Converter",
    workflow_description="Converts downloaded EPFR PDF files to markdown using Mistral OCR and updates mapping.",
)
class EpfrPdfOcrConverter:
    """Convert mapped EPFR PDF disclosures to Markdown via Mistral OCR.

    The workflow reads the existing UNP file mapping, OCRs PDF entries, updates
    those entries to point at Markdown files, and returns processing statistics.
    """

    @workflows.workflow.entrypoint
    async def run(self, input: EpfrPdfOcrInput) -> EpfrPdfOcrOutput:
        """Run the EPFR PDF OCR conversion workflow.

        Args:
            input: OCR workflow input containing mapping location and process flags.

        Returns:
            Structured OCR output with totals, failed files, cleanup list, and raw stats.
        """
        logger.info(
            f"Workflow epfr-pdf-ocr-converter started: output_dir={input.output_dir}, "
            f"mapping_filename={input.mapping_filename}, overwrite={input.overwrite}, "
            f"cleanup_source={input.cleanup_source}, unps={input.unps}"
        )
        stats = await ocr_epfr_mapping_pdfs(input)

        output = EpfrPdfOcrOutput(
            mapping_path=str(stats.get("mapping_path", "")),
            total_pdf_entries=int(stats.get("total_pdf_entries", 0)),
            total_successful=int(stats.get("total_successful", 0)),
            total_failed=int(stats.get("total_failed", 0)),
            total_skipped=int(stats.get("total_skipped", 0)),
            cleaned_up_files=list(stats.get("cleaned_up_files", [])),
            failed_files=list(stats.get("failed_files", [])),
            stats=stats,
        )

        logger.info(
            f"PDF OCR complete: {output.total_pdf_entries} entries, {output.total_successful} successful, "
            f"{output.total_failed} failed, {output.total_skipped} skipped"
        )

        return output
