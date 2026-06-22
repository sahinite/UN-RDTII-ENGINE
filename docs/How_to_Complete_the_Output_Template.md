
# GENERAL RULES	
Delete example rows:	Remove rows 6-8 before submitting. They are provided for illustration only.
Do not rename columns:	Column names and order must match this template exactly. Judges validate programmatically.
One row per provision:	Each article or paragraph that maps to an indicator = one row. If one article maps to two indicators, create two rows.

# FIELD RULES	
- Economy:	Use the official UN country name (e.g. "Lao People's Democratic Republic", not "Laos"). See the country list in the hackathon brief.
- Law Name:	Full official name + year. Do not abbreviate. Example: Personal Data Protection Act 2012, not PDPA.
- Indicator ID:	Use the exact RDTII code from the framework (e.g. "P6-I1", "P6-I2", "P7-I1"). Do not invent codes.
- Article / §: Include article number AND paragraph. Example: "Art. 26(2)" or "s. 16(1)(a)". Never write just "Art. 26".
- Page Number: Page number in the source PDF your tool retrieved. Judges will verify this against the original document.
- Verbatim Snippet:	Copy the EXACT text. No editing, no summarising, no paraphrasing. Judges place this next to the source PDF to verify.
- Discovery Tag:	"NEW" = your tool found this provision independently (not in the sample kit examples). "KNOWN" = it was provided as a known example. NEW provisions are worth the most points.
- Source URL:	Direct, working URL to the law on the official government portal. Not Google, not a third-party database.
- Confidence:	Optional but recommended. A decimal 0.00–1.00 reflecting your model certainty. Helps judges flag rows for closer review.
- Notes:	Optional. Use to flag: bilingual sources, OCR quality issues, delegated legislation, cross-references to other laws.


# DISCOVERY TAG — EXPLAINED	
- What is "NEW"?:	Any provision your tool retrieved and mapped that was NOT included in the sample kit "known examples" provided at the start of Round 1.
- What is "KNOWN"?:	Provisions that were highlighted in the sample kit as examples. You still must include them — they verify your tool can match known cases.
- Why it matters:	NEW provisions are worth 20 of the 40 Substantive Accuracy points. Most teams miss this. Finding valid NEW provisions is the single largest scoring differentiator.

# MAPPING RATIONALE — FORMAT GUIDE	
- Purpose:
Helps policy judges understand WHY the tool mapped a provision to an indicator — not just WHAT was found. Use it as a navigation aid, not as a replacement for reading the verbatim snippet.
- Required format:
Use this structure: "This [article/section] [prohibits / requires / permits / establishes] [what]. Maps to [indicator] because [1-sentence legal logic]."
- Max length:
300 characters. If you cannot explain the mapping in 300 characters, the rationale is probably too vague. Keep it specific.
- Name the legal mechanism:
Do not write 'This article is about data protection.' Write 'This article prohibits collection without consent, establishing consent as the mandatory legal basis for processing — P7-I1.'
- Do not paraphrase the snippet:
	The rationale explains the legal significance of the provision, not its content. The verbatim snippet already shows the content.
- Leave blank if uncertain:
If your tool's legal reasoning is unreliable for a particular row, leave the field blank. A blank rationale is neutral. A wrong rationale misleads judges.
- Scoring note:
This field is NOT directly scored. It is used by policy judges to validate mapping quality. Strong, accurate rationales signal a tool that understands the law, not just pattern-matches text.
- Example — correct:
"This Art. 26(2) prohibits cross-border transfer of personal data as the default. Maps to P6-I1 (general restriction) because the burden falls on the controller to justify any transfer, making restriction the baseline position."
- Example — too vague: (do not submit like this)
"This article is about data transfers and relates to cross-border data flow rules."