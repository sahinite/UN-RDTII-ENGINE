"""
Output writer — 13-column CSV (exact OUTPUT_TEMPLATE_31MAY.xlsx schema,
judge-validated programmatically) + JSON envelope with extended metadata
(ocr_quality_cer, processing_time_seconds, model_version, raw_context_before/
after, verbatim_original, archive_url). [Z2-6]

CSV columns (exact order): economy, law_name, law_number_ref, last_amended,
indicator_id, article, discovery_tag, location_reference, verbatim_snippet,
mapping_rationale, source_url, confidence, notes

TODO: def write_csv(records, path)
TODO: def write_json(records, path)
"""
