"""
Output writer — 13-column CSV + JSON envelope with extended metadata. [Z2-6]

ST1 — write_csv: pandas, UTF-8-BOM, post-write column-order verification
ST2 — write_json: 6 extended fields, per-document grouping, post-write verification
ST3 — validate_record: pre-write field checks + column order guard
ST4 — write_outputs: orchestrator entry point + console summary
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import pandas as pd

from src.fetcher.logger import get_logger
from src.output.models import (
    CSV_COLUMNS,
    OutputRecord,
    OutputSchemaError,
    OutputWriteError,
    _REQUIRED_COLUMNS,
)

if TYPE_CHECKING:
    pass

logger = get_logger("output.writer")


# ── ST3: Output Schema Validator ───────────────────────────────────────────────

def validate_record(record: OutputRecord) -> list[str]:
    """
    Pre-write field validation. Returns list of violation strings (empty = OK).
    Checks required fields, column order guard, confidence range, indicator_id format.
    """
    violations: list[str] = []

    row = record.as_csv_row()

    # Required fields must be non-empty
    for col in _REQUIRED_COLUMNS:
        val = row.get(col)
        if not val or (isinstance(val, str) and not val.strip()):
            violations.append(f"required field '{col}' is empty")

    # Column order guard — ensure keys match CSV_COLUMNS exactly
    row_keys = list(row.keys())
    if row_keys != CSV_COLUMNS:
        violations.append(
            f"column order mismatch: got {row_keys}, expected {CSV_COLUMNS}"
        )

    # Confidence range
    if record.confidence is not None and not (0.0 <= record.confidence <= 1.0):
        violations.append(f"confidence {record.confidence} out of range [0, 1]")

    # Indicator ID format — derived from taxonomy.json, not hardcoded
    from src.retrieval.config import get_valid_indicator_ids
    if record.indicator_id not in get_valid_indicator_ids():
        violations.append(f"invalid indicator_id '{record.indicator_id}'")

    # Discovery tag
    if record.discovery_tag not in ("KNOWN", "NEW"):
        violations.append(
            f"discovery_tag must be 'KNOWN' or 'NEW', got '{record.discovery_tag}'"
        )

    # Mapping rationale length
    if record.mapping_rationale and len(record.mapping_rationale) > 300:
        violations.append(
            f"mapping_rationale exceeds 300 chars ({len(record.mapping_rationale)})"
        )

    return violations


# ── ST1: CSV Writer ────────────────────────────────────────────────────────────

def write_csv(records: list[OutputRecord], path: Path) -> Path:
    """
    Write records to CSV with exact 13-column schema.
    Encoding: UTF-8-BOM (Excel-compatible).
    Post-write: re-reads header to verify column order.

    Returns the written path.
    Raises OutputWriteError on any failure.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = [r.as_csv_row() for r in records]
    df = pd.DataFrame(rows, columns=CSV_COLUMNS)

    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write CSV to {path}: {exc}") from exc

    # Post-write column order verification
    try:
        written_df = pd.read_csv(path, nrows=0, encoding="utf-8-sig")
        written_cols = list(written_df.columns)
        if written_cols != CSV_COLUMNS:
            raise OutputWriteError(
                f"Post-write column order mismatch in {path}: "
                f"got {written_cols}, expected {CSV_COLUMNS}"
            )
    except OSError as exc:
        raise OutputWriteError(f"Post-write verification failed for {path}: {exc}") from exc

    logger.info({
        "event": "csv_written",
        "path": str(path),
        "rows": len(rows),
        "columns": CSV_COLUMNS,
    })
    return path


# ── ST2: JSON Envelope Writer ──────────────────────────────────────────────────

def write_json(records: list[OutputRecord], path: Path) -> Path:
    """
    Write JSON envelope: records grouped by source_url (per-document).
    Includes all 6 extended metadata fields populated with real values.
    Post-write: re-reads and verifies extended fields are present.

    Returns the written path.
    Raises OutputWriteError on any failure.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Group by source_url (per-document)
    doc_groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        doc_groups[rec.source_url].append(rec.as_json_dict())

    envelope = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "total_records": len(records),
        "documents": [
            {
                "source_url": url,
                "record_count": len(rows),
                "records": rows,
            }
            for url, rows in doc_groups.items()
        ],
    }

    try:
        path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write JSON to {path}: {exc}") from exc

    # Post-write extended field verification
    _EXTENDED_FIELDS = {
        "ocr_quality_cer",
        "processing_time_seconds",
        "model_version",
        "raw_context_before",
        "raw_context_after",
        "verbatim_original",
        "archive_url",
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("documents"):
            first_rec = data["documents"][0]["records"][0]
            missing = _EXTENDED_FIELDS - set(first_rec.keys())
            if missing:
                raise OutputWriteError(
                    f"Post-write JSON missing extended fields: {missing}"
                )
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        raise OutputWriteError(f"Post-write JSON verification failed: {exc}") from exc

    logger.info({
        "event": "json_written",
        "path": str(path),
        "records": len(records),
        "documents": len(doc_groups),
    })
    return path


# ── ST4: Write Orchestrator ────────────────────────────────────────────────────

def write_outputs(
    records: list[OutputRecord],
    output_dir: Path,
    economy: str,
    pillar: int,
    *,
    skip_invalid: bool = False,
) -> dict:
    """
    Main entry point. Validates, then writes CSV + JSON.

    Args:
        records: OutputRecord list from the pipeline.
        output_dir: Directory for output files.
        economy: Economy name (used in filenames).
        pillar: Pillar number (6 or 7).
        skip_invalid: If True, skip records with violations; else raise.

    Returns:
        dict with keys: csv_path, json_path, written, skipped, violations_total.
    """
    output_dir = Path(output_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    stem = f"{economy}_P{pillar}_{ts}"
    csv_path = output_dir / f"{stem}.csv"
    json_path = output_dir / f"{stem}.json"

    valid_records: list[OutputRecord] = []
    skipped = 0
    violations_total = 0
    all_violations: list[dict] = []

    for i, rec in enumerate(records):
        viols = validate_record(rec)
        if viols:
            violations_total += len(viols)
            all_violations.append({
                "record_index": i,
                "indicator_id": rec.indicator_id,
                "violations": viols,
            })
            if skip_invalid:
                skipped += 1
                logger.warning({
                    "event": "record_skipped",
                    "index": i,
                    "indicator_id": rec.indicator_id,
                    "violations": viols,
                })
                continue
            else:
                raise OutputSchemaError(
                    f"Record {i} ({rec.indicator_id}) failed validation: {viols}"
                )
        valid_records.append(rec)

    if not valid_records:
        logger.warning({"event": "no_valid_records", "economy": economy, "pillar": pillar})

    written_csv = write_csv(valid_records, csv_path) if valid_records else None
    written_json = write_json(valid_records, json_path) if valid_records else None

    summary = {
        "csv_path": str(written_csv) if written_csv else None,
        "json_path": str(written_json) if written_json else None,
        "written": len(valid_records),
        "skipped": skipped,
        "violations_total": violations_total,
    }

    _print_console_summary(summary, economy, pillar, all_violations)

    logger.info({
        "event": "write_outputs_complete",
        "economy": economy,
        "pillar": pillar,
        **summary,
    })

    return summary


def _print_console_summary(
    summary: dict, economy: str, pillar: int, violations: list[dict]
) -> None:
    print(f"\n{'='*60}")
    print(f"  OUTPUT SUMMARY — {economy} | Pillar {pillar}")
    print(f"{'='*60}")
    print(f"  Records written : {summary['written']}")
    print(f"  Records skipped : {summary['skipped']}")
    if summary["csv_path"]:
        print(f"  CSV output      : {summary['csv_path']}")
    if summary["json_path"]:
        print(f"  JSON output     : {summary['json_path']}")
    if violations:
        print(f"\n  Schema violations ({summary['violations_total']} total):")
        for v in violations[:5]:  # show first 5
            print(f"    [{v['record_index']}] {v['indicator_id']}: {v['violations']}")
        if len(violations) > 5:
            print(f"    ... and {len(violations) - 5} more")
    print(f"{'='*60}\n")


# ── Helper: build OutputRecord from pipeline results ───────────────────────────

def build_output_record(
    validated_result,
    *,
    ocr_quality_cer: Optional[float] = None,
    processing_time_seconds: Optional[float] = None,
) -> OutputRecord:
    """
    Build an OutputRecord from a ValidatedResult (Z2-5) + optional OCR/timing metadata.
    Bridges the Z2-5 → Z2-6 data handoff.
    """
    rec = validated_result.record
    return OutputRecord(
        # 13 CSV columns
        economy=rec.economy,
        law_name=rec.law_name,
        law_number_ref=rec.law_number_ref,
        last_amended=rec.last_amended,
        indicator_id=rec.indicator_id,
        article=rec.article,
        discovery_tag=rec.discovery_tag,
        location_reference=rec.location_reference,
        verbatim_snippet=rec.verbatim_snippet,
        mapping_rationale=rec.mapping_rationale,
        source_url=rec.source_url,
        confidence=rec.confidence,
        notes=rec.notes,
        # 6 JSON extended fields
        ocr_quality_cer=ocr_quality_cer,
        processing_time_seconds=processing_time_seconds,
        model_version=rec.model_used,
        raw_context_before=rec.raw_context_before,
        raw_context_after=rec.raw_context_after,
        verbatim_original=rec.verbatim_original,
        archive_url=validated_result.archive_url,
    )
