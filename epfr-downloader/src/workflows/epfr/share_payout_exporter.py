"""Share payout export: CSV index loading, dividend flattening, and atomic JSON export."""

import csv
import json
import os
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import get_shares_source_data_csv
from .models import EpfrSharePayoutExportInput, EpfrSharePayoutExportRow


def load_share_reference_index(csv_path: Path) -> tuple[dict[tuple[str, str], str], dict[str, Any]]:
    """Load shares_source_data.csv and build a UNP+share_kind -> instrument_uuid index.

    Rows where (unp, share_kind) is ambiguous (appears in multiple CSV rows) are
    excluded from the index and counted in stats.
    """
    rows: list[dict[str, str]] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    key_counts: Counter[tuple[str, str]] = Counter()
    key_to_uuid: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (row["unp"], row["share_kind"])
        key_counts[key] += 1
        key_to_uuid[key] = row["instrument_uuid"]

    ambiguous_count = 0
    ambiguous_keys: set[tuple[str, str]] = set()
    index: dict[tuple[str, str], str] = {}
    for key, count in key_counts.items():
        if count > 1:
            ambiguous_count += count
            ambiguous_keys.add(key)
        else:
            index[key] = key_to_uuid[key]

    known_unps: set[str] = {row["unp"] for row in rows}
    stats = {"ambiguous_share_kind": ambiguous_count, "known_unps": known_unps, "ambiguous_keys": ambiguous_keys}
    return index, stats


def run_share_payout_export(input: EpfrSharePayoutExportInput) -> dict[str, Any]:
    """Read distilled JSON, flatten dividends, match against CSV index, write export file atomically."""
    csv_path = Path(input.shares_csv_path) if input.shares_csv_path else get_shares_source_data_csv()
    index, csv_stats = load_share_reference_index(csv_path)

    distilled_path = Path(input.output_dir) / input.input_filename
    with distilled_path.open(encoding="utf-8") as f:
        distilled_data: dict[str, Any] = json.load(f)

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

    known_unps: set[str] = csv_stats["known_unps"]
    ambiguous_keys: set[tuple[str, str]] = csv_stats["ambiguous_keys"]

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

                key = (unp_str, share_type)
                if key not in index:
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

                row = EpfrSharePayoutExportRow(
                    share_uuid=index[key],
                    period_year=dividend["period_year"],
                    period_type=dividend["period_type"],
                    period_number=dividend["period_number"],
                    amount_per_share=Decimal(str(dividend["amount_per_share"])),
                    decision_date=dividend["decision_date"],
                    record_date=dividend["record_date"],
                    payment_date=dividend["payment_date"],
                )
                payouts.append(row.model_dump(mode="json"))
                matched_count += 1

        if payouts:
            export_data[unp_str] = payouts

    dir_path = Path(input.output_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    output_path = dir_path / input.output_filename
    fd, tmp_path = tempfile.mkstemp(suffix=".json", dir=str(dir_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(output_path))
    except BaseException:
        os.unlink(tmp_path)
        raise

    total_unmatched = (
        missing_csv_unp + missing_share_kind + ambiguous_share_kind + autofilled_share_type + skipped_file_errors
    )
    return {
        "output_path": str(output_path),
        "matched_payouts": matched_count,
        "unmatched_payouts": total_unmatched,
        "missing_csv_unp": missing_csv_unp,
        "missing_share_kind": missing_share_kind,
        "ambiguous_share_kind": ambiguous_share_kind,
        "csv_ambiguous_share_kind": csv_stats["ambiguous_share_kind"],
        "autofilled_share_type": autofilled_share_type,
        "skipped_file_errors": skipped_file_errors,
        "total_companies_exported": len(export_data),
        "unmatched_samples": {
            "missing_csv_unp": samples_missing_csv,
            "missing_share_kind": samples_missing_share_kind,
            "ambiguous_share_kind": samples_ambiguous,
            "autofilled_share_type": samples_autofilled,
            "skipped_file_errors": samples_file_errors,
        },
    }
