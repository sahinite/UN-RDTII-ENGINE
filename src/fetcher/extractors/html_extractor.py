"""
HTML extraction via BeautifulSoup with URL anchor support.

Extracts article/section hierarchy and builds location_reference_map
(section title → clickable URL anchor) — a key differentiator for judges.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.fetcher.extractors.legislation_meta import derive_act_title
from src.fetcher.logger import get_logger
from src.fetcher.models import CostLogEntry, FetchedDocument

if TYPE_CHECKING:
    from src.fetcher.models import Zone1Result

logger = get_logger("html_extractor")


class ExtractionError(Exception):
    pass


def detect_encoding(raw_bytes: bytes, content_type: str) -> str:
    # 1. Content-Type header charset
    m = re.search(r"charset=([^\s;]+)", content_type, re.IGNORECASE)
    if m:
        return m.group(1).strip('"\'')

    # 2. <meta charset> or <meta http-equiv="Content-Type">
    snip = raw_bytes[:4096].decode("ascii", errors="replace")
    m2 = re.search(r'charset=["\']?([A-Za-z0-9_\-]+)', snip, re.IGNORECASE)
    if m2:
        return m2.group(1)

    # 3. chardet if available
    try:
        import chardet
        detected = chardet.detect(raw_bytes[:8192])
        if detected and detected.get("encoding"):
            return detected["encoding"]
    except ImportError:
        pass

    return "utf-8"


def _find_anchor_id(tag: Tag, soup: BeautifulSoup) -> str | None:
    """Priority: tag's own id → parent section/article id → preceding <a name>."""
    if tag.get("id"):
        return str(tag["id"])

    for parent in tag.parents:
        if isinstance(parent, Tag) and parent.name in ("section", "article"):
            if parent.get("id"):
                return str(parent["id"])

    prev = tag.find_previous_sibling("a")
    if prev and isinstance(prev, Tag) and prev.get("name"):
        return str(prev["name"])

    return None


def build_location_reference(base_url: str, element_id: str) -> str:
    return f"{base_url.rstrip('/')}#{element_id}"


def extract_article_hierarchy(soup: BeautifulSoup, base_url: str) -> list[dict]:
    sections: list[dict] = []
    headings = [h for h in soup.find_all(re.compile(r"^h[1-4]$")) if isinstance(h, Tag)]

    # Resolve every heading's anchor up front. A missing anchor is only worth
    # flagging when the document ACTUALLY uses anchored headings — i.e. some other
    # heading resolved one. When none do (e.g. Singapore SSO's whole-doc view keys
    # provisions off <a name="pr..-"> anchors and its only h-tags are page chrome
    # like "Help"/"Search within Legislation"), the heading-anchor model simply
    # does not apply, so warning per heading is just noise.
    anchor_ids = [_find_anchor_id(h, soup) for h in headings]
    doc_uses_heading_anchors = any(anchor_ids)

    for heading, anchor_id in zip(headings, anchor_ids):
        level = int(heading.name[1])
        title = heading.get_text(strip=True)
        anchor = build_location_reference(base_url, anchor_id) if anchor_id else None

        if not anchor_id and doc_uses_heading_anchors:
            logger.warning({
                "event": "html_anchor_not_found",
                "section_title": title,
                "url": base_url,
                "economy": "",
            })

        # Collect text until next heading at same or higher level
        content_parts: list[str] = []
        for sibling in heading.next_siblings:
            if not isinstance(sibling, Tag):
                content_parts.append(str(sibling).strip())
                continue
            if sibling.name and re.match(r"^h[1-4]$", sibling.name):
                sib_level = int(sibling.name[1])
                if sib_level <= level:
                    break
            content_parts.append(sibling.get_text(separator=" ", strip=True))

        sections.append({
            "level": level,
            "title": title,
            "anchor": anchor,
            "text": " ".join(p for p in content_parts if p),
        })

    return sections


def extract_html(raw_bytes: bytes, zone1_result: "Zone1Result", content_type: str = "") -> FetchedDocument:
    start = time.monotonic()
    encoding = detect_encoding(raw_bytes, content_type)

    try:
        text_str = raw_bytes.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        text_str = raw_bytes.decode("utf-8", errors="replace")

    soup = BeautifulSoup(text_str, "lxml")

    # Remove boilerplate
    for tag in soup(["nav", "footer", "script", "style", "aside", "header"]):
        tag.decompose()

    # JS-rendered detection
    body_text = soup.get_text(strip=True)
    if len(body_text) < 200:
        raise ExtractionError(
            f"HTML appears JS-rendered; re-run via Crawl4AI: {zone1_result.url}"
        )

    base_url = zone1_result.url

    # Check for embedded images
    has_images = bool(soup.find("img"))
    if has_images:
        logger.debug({
            "event": "embedded_images_found",
            "url": base_url,
            "economy": zone1_result.economy,
        })

    section_hierarchy = extract_article_hierarchy(soup, base_url)

    # location_reference_map: section title → anchor URL
    location_reference_map: dict[str, str] = {
        s["title"]: s["anchor"]
        for s in section_hierarchy
        if s.get("anchor")
    }

    full_text = soup.get_text(separator="\n", strip=True)
    elapsed_ms = (time.monotonic() - start) * 1000

    # Check pagination hint
    if re.search(r"[?&]page=\d+", base_url):
        logger.info({
            "event": "pagination_detected",
            "url": base_url,
            "economy": zone1_result.economy,
        })

    cost_log = CostLogEntry(
        engine="beautifulsoup",
        pages=None,
        cost_usd=0.0,
        processing_time_ms=elapsed_ms,
        has_embedded_images=has_images,
    )

    doc = FetchedDocument(
        source_url=zone1_result.url,
        resolved_url=base_url,
        economy=zone1_result.economy,
        # Mirror the PDF path: URL-only seeds arrive with no title, which would emit an
        # empty law_name (schema violation → row dropped). Fall back to the cover-page title.
        act_title=zone1_result.act_title or derive_act_title(full_text, zone1_result.url),
        discovery_tag=zone1_result.discovery_tag,  # type: ignore[arg-type]
        archive_url=zone1_result.archive_url,
        doc_type="HTML",
        extraction_method="beautifulsoup",
        page_count=None,
        raw_text=full_text,
        section_hierarchy=section_hierarchy,
        location_reference_map=location_reference_map,
        cost_log_entry=cost_log,
    )
    doc.validate()

    logger.info({
        "event": "fetch_document_validated",
        "url": zone1_result.url,
        "extraction_method": "beautifulsoup",
        "text_length": len(full_text),
        "flag_for_review": doc.flag_for_review,
        "economy": zone1_result.economy,
    })
    return doc
