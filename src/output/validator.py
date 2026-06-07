"""
Validate + archive + confidence flagging. [Z2-5]

- HTTP GET each source_url (flag broken links)
- Wayback snapshot at output time
- confidence < 0.80 -> auto-append note "Recommend human review — OCR/translation source"

TODO: def validate_and_flag(records) -> list[ValidatedRecord]
"""
