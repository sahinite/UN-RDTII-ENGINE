"""
Shared test fixtures for Z2-4 LLM Extractor tests. [Z2-4 ST7]
"""

from __future__ import annotations

from src.mapping.models import LLMResponse
from src.retrieval.models import Chunk, LocationReference, RetrievedChunk

PDPA_CHUNK_TEXT = (
    "26. Restriction on transfer of personal data outside Singapore\n"
    "An organisation shall not transfer personal data of an individual to a country "
    "or territory outside Singapore except in accordance with requirements prescribed "
    "under this Act to ensure that the organisation provides a standard of protection "
    "to personal data so transferred that is comparable to the protection under this Act."
)

PDPA_P7_CHUNK_TEXT = (
    "24. Protection of personal data\n"
    "An organisation shall protect personal data in its possession or under its control by "
    "making reasonable security arrangements to prevent unauthorised access, collection, use, "
    "disclosure, copying, modification, disposal or similar risks."
)


def make_retrieved_chunk(
    text: str = PDPA_CHUNK_TEXT,
    article: str = "26",
    location: str = "Page 34",
    page: int = 33,
) -> RetrievedChunk:
    loc_ref = LocationReference(
        act_title="Personal Data Protection Act 2012",
        part="PART VI",
        article_number=article,
        page=page,
    )
    chunk = Chunk(
        chunk_id=f"Personal Data Protection Act 2012__{article}__0",
        text=text,
        location_reference=loc_ref,
        doc_source_url="https://sso.agc.gov.sg/Act/PDPA2012",
    )
    return RetrievedChunk(
        chunk=chunk,
        rerank_score=2.1,
        context_window="25. Purpose of Part VI.",
        retrieval_method="hybrid",
    )


VALID_LLM_JSON = """{
  "found": true,
  "provisions": [{
    "article": "Section 26",
    "verbatim_snippet": "An organisation shall not transfer personal data of an individual to a country or territory outside Singapore",
    "mapping_rationale": "Section 26 imposes a default prohibition on cross-border transfer of personal data. Maps to P6-I1 because it establishes a blanket restriction on overseas transfer.",
    "confidence": 0.95,
    "location_reference": "Page 34",
    "non_consecutive": false
  }]
}"""

NOT_FOUND_LLM_JSON = '{"found": false, "provisions": []}'

P7_LLM_JSON = """{
  "found": true,
  "provisions": [{
    "article": "Section 24",
    "verbatim_snippet": "An organisation shall protect personal data in its possession or under its control by making reasonable security arrangements to prevent unauthorised access",
    "mapping_rationale": "Section 24 mandates security arrangements for personal data. Maps to P7-I1 because it requires lawful and secure processing.",
    "confidence": 0.92,
    "location_reference": "Page 32",
    "non_consecutive": false
  }]
}"""


def make_llm_response(
    text: str = VALID_LLM_JSON,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-20250514",
) -> LLMResponse:
    return LLMResponse(
        text=text,
        input_tokens=420,
        output_tokens=55,
        model=model,
        provider=provider,
        latency_ms=1840.0,
        cost_usd=0.002085,
    )


DOC_METADATA = {
    "economy": "Singapore",
    "law_name": "Personal Data Protection Act 2012",
    "law_number_ref": None,
    "last_amended": "2021",
    "source_url": "https://sso.agc.gov.sg/Act/PDPA2012",
    "discovery_tag": "KNOWN",
    "verbatim_original": None,
}

TAXONOMY_FIXTURE = {
    "P6-I1": {
        "name": "General prohibition / restriction",
        "legal_question": "Does the law restrict cross-border transfer of personal data as a default?",
        "in_scope": [
            "Default restriction on transferring personal data outside the jurisdiction",
        ],
        "out_of_scope": [
            "Government data or national security exclusions",
        ],
        "negative_examples": ["Banking Act S.47", "tax records", "national intelligence data"],
    },
    "P7-I1": {
        "name": "Legal basis for processing",
        "legal_question": "Does the law require a lawful basis for collecting or processing personal data?",
        "in_scope": [
            "Requirement for lawful basis before collecting or processing personal data",
        ],
        "out_of_scope": [
            "Government data or public sector exclusions",
        ],
        "negative_examples": ["government data only", "national security exclusion"],
    },
}
