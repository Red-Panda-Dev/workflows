#!/usr/bin/env python3
"""Generate SQL INSERT statements from share_payouts_by_unp.json."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

INPUT_FILE = Path(__file__).parent / "output" / "share_payouts_by_unp.json"
OUTPUT_FILE = Path(__file__).parent / "output" / "share_dividends_insert.sql"

__all__ = ["format_value", "generate_values_row", "generate_sql_from_json"]


def format_value(value: str | int | float | None, is_string: bool = False) -> str:
    """Format a value for SQL insertion."""
    if value is None:
        return "NULL"
    if is_string:
        return f"'{value}'"
    return str(value)


def generate_values_row(record: dict) -> str:
    """Generate a single VALUES row from a dividend record."""
    share_uuid = format_value(record.get("share_uuid"), is_string=True)
    period_year = format_value(record.get("period_year"))
    period_type = format_value(record.get("period_type"), is_string=True)
    period_number = format_value(record.get("period_number"))
    amount_per_share = format_value(record.get("amount_per_share"))
    decision_date = format_value(record.get("decision_date"), is_string=True)
    record_date = format_value(record.get("record_date"), is_string=True)
    payment_date = format_value(record.get("payment_date"), is_string=True)

    return (
        f"    ({share_uuid}, {period_year}, {period_type}, {period_number}, "
        f"{amount_per_share}, {decision_date}, {record_date}, {payment_date}, "
        f"NOW(), NOW(), TRUE)"
    )


def main() -> None:
    """Read JSON and generate SQL file."""
    if not INPUT_FILE.exists():
        print(f"Input file not found: {INPUT_FILE}")
        return

    with INPUT_FILE.open() as f:
        data: dict[str, list[dict]] = json.load(f)

    rows: list[str] = []
    seen: set[tuple] = set()

    for _unp, payouts in data.items():
        for record in payouts:
            # Deduplicate by creating a unique key from all fields
            key = (
                record.get("share_uuid"),
                record.get("period_year"),
                record.get("period_type"),
                record.get("period_number"),
                record.get("amount_per_share"),
                record.get("decision_date"),
                record.get("record_date"),
                record.get("payment_date"),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(generate_values_row(record))

    values_block = ",\n".join(rows)

    sql_content = f"""INSERT INTO public.share_dividend(
    share_uuid, period_year, period_type, period_number,
    amount_per_share, decision_date, record_date, payment_date,
    created_at, updated_at, is_frozen)
VALUES
{values_block}
ON CONFLICT DO NOTHING;"""

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w") as f:
        f.write(f"-- Generated from {INPUT_FILE.name}\n")
        f.write(f"-- Total: {len(rows)} unique records\n\n")
        f.write(sql_content)
        f.write("\n")

    print(f"Generated batch INSERT with {len(rows)} records to {OUTPUT_FILE}")


def generate_sql_from_json(input_path: Path, output_path: Path) -> int:
    """Generate SQL INSERT file from share payouts JSON file.

    Reads JSON from input_path, generates SQL INSERT statements for all
    dividend records, and writes to output_path atomically.

    Args:
        input_path: Path to the share_payouts_by_unp.json file.
        output_path: Path where the SQL file should be written.

    Returns:
        Number of unique records processed and written to SQL.

    Raises:
        FileNotFoundError: If input_path does not exist.
        RuntimeError: If SQL generation or write fails.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with input_path.open() as f:
        data: dict[str, list[dict]] = json.load(f)

    rows: list[str] = []
    seen: set[tuple] = set()

    for _unp, payouts in data.items():
        for record in payouts:
            # Deduplicate by creating a unique key from all fields
            key = (
                record.get("share_uuid"),
                record.get("period_year"),
                record.get("period_type"),
                record.get("period_number"),
                record.get("amount_per_share"),
                record.get("decision_date"),
                record.get("record_date"),
                record.get("payment_date"),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(generate_values_row(record))

    values_block = ",\n".join(rows)

    sql_content = f"""INSERT INTO public.share_dividend(
    share_uuid, period_year, period_type, period_number,
    amount_per_share, decision_date, record_date, payment_date,
    created_at, updated_at, is_frozen)
VALUES
{values_block}
ON CONFLICT DO NOTHING;"""

    # Atomic write
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".sql", dir=str(output_path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(f"-- Generated from {input_path.name}\n")
            f.write(f"-- Total: {len(rows)} unique records\n\n")
            f.write(sql_content)
            f.write("\n")
        os.replace(tmp_path, str(output_path))
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"Failed to write SQL file: {exc}") from exc

    return len(rows)


if __name__ == "__main__":
    main()
