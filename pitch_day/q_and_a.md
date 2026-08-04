# Judge Q&A — Short Answers

## How does it find laws?

Economy YAML files declare the official portals and discovery strategy. The engine uses portal-specific adapters, then applies shared ranking, taxonomy exclusions, seed merging, and KNOWN/NEW tagging.

## How does it handle scanned PDFs?

It detects whether text extraction is adequate, then uses a cloud-first OCR cascade with local OCR as a floor. OCR quality is measured and low-quality results are flagged instead of being silently treated as reliable.

## How does it handle multiple languages?

Singapore's pitch example is English. For non-English economies, the engine uses multilingual retrieval and translates where required for mapping while retaining the original-language text as the verbatim evidence source.

## How are hallucinated snippets controlled?

The parser checks that the proposed verbatim snippet occurs in the extracted source text. Failed verbatim assertions are discarded by default, and the output records the source location and review flags.

## How are KNOWN and NEW defined?

KNOWN means the act/provision matches the Round 1 reference set. NEW means the engine discovered a provision outside those known examples. Matching is based on act and provision identity, not only on URL.

## How are wrong indicators controlled?

The taxonomy constrains valid indicator IDs, retrieval is hybrid, the mapper is prompted with indicator definitions, and known-provision cross-indicator checks prune confirmed drift. Ambiguity remains visible through notes and confidence.

## What happens when a portal fails?

The transport and fetch layers use configured fallbacks, and exact document bytes can be archived locally. If evidence cannot be validated, the record is flagged rather than presented as fully reliable.

## Can the LLM be changed?

Yes. The provider and model are configured through environment variables, while the mapping and validation interfaces remain the same. Cloud and local providers are supported.

## What is automated versus human-reviewed?

Discovery, extraction, retrieval, mapping, tagging, validation, and writing are automated. Humans should review low-confidence OCR/translation, secondary sources, ambiguous cross-references, and legal interpretation that affects a final policy decision.

## How does it scale?

New economies are normally YAML configuration. New pillars require taxonomy and indicator definitions, while the same fetch, retrieval, mapping, validation, and output contracts remain reusable.

## What would you improve next?

Add broader primary-source coverage, stronger page-number extraction for complex HTML/PDF layouts, more human-in-the-loop review tooling, and continuous monitoring for amendments.
