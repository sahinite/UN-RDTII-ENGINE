"""Currency check + Wayback Machine archiving. [Z1-4]

Pipeline per CandidateAct:
  ST1 — Live URL validation (HTTP GET gate)
  ST2 — In-force detection (keyword scan + portal status tags)
  ST3 — Auto-replacement fetch for cancelled acts (4 scenarios)
  ST4 — last_amended year extraction
  ST5 — Wayback Machine archiving
"""

import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from src.crawler.crawler import CandidateAct, _registered_domain

logger = logging.getLogger(__name__)

# ── Environment config ─────────────────────────────────────────────────────────

_CURRENCY_FETCH_TIMEOUT_SEC = float(os.getenv("CURRENCY_FETCH_TIMEOUT_SEC", "15"))
_CURRENCY_RETRY_WAIT_SEC = float(os.getenv("CURRENCY_RETRY_WAIT_SEC", "5"))
_WAYBACK_RATE_LIMIT_SEC = float(os.getenv("WAYBACK_RATE_LIMIT_SEC", "1.0"))

_AGENT_STRING = "RDTII-Engine/2.0 (UN ESCAP Hackathon; currency-check)"
_WAYBACK_UA = "RDTII-Engine/2.0 (UN ESCAP Hackathon)"
_WAYBACK_SAVE_URL = "https://web.archive.org/save/{url}"

_YEAR_MIN = 1950
_YEAR_MAX = datetime.now().year

# Module-level alias so tests can patch asyncio.sleep without side-effects
_sleep = asyncio.sleep


# ── Output contract ────────────────────────────────────────────────────────────

@dataclass
class CurrencyResult:
    # Forwarded from CandidateAct
    act_title: str
    act_url: str              # canonical URL (post-redirect; possibly replacement)
    description_snippet: str
    document_type: str
    discovery_tag: str
    portal_source: str
    economy: str
    pillar: str
    pass_number: int
    # Added by currency.py
    http_status: int          # HTTP status from ST1 (200, 404, -1=timeout, 0=error)
    currency_status: str      # "in_force" | "cancelled" | "uncertain" | "broken"
    flag_for_review: bool
    currency_note: str
    last_amended: str         # 4-digit year string or ""
    archive_url: str          # Wayback snapshot URL or ""


# ── Detection patterns ─────────────────────────────────────────────────────────

_CANCELLED_PATTERNS = [
    r"repealed",
    r"revoked",
    r"no longer in force",
    r"superseded by",
    r"replaced by",
    r"has been cancelled",
    r"ceased to have effect",
    r"withdrawn",
    r"akta ini telah dibatalkan",     # Bahasa: "this act has been repealed"
]

_IN_FORCE_PATTERNS = [
    r"current version",
    r"in force as of",
    r"current as at",
    r"as amended",
    r"consolidated to",
    r"in force",
]

_REPLACEMENT_PATTERNS = [
    r"replaced by (.+?)(?:\.|$)",
    r"see (.+?) \d{4}",
    r"refer to (.+?) act",
    r"superseded by (.+?)(?:\.|$)",
]

_AMENDED_PATTERNS = [
    r"as amended (?:in|up to|through) (\d{4})",
    r"last amended[:\s]+(?:\w+ )?(\d{4})",
    r"amendment(?:s)? (?:act )?(\d{4})",
    r"consolidated (?:as at|to) (?:\d{1,2} \w+ )?(\d{4})",
    r"\[as at (?:\d{1,2} \w+ )?(\d{4})\]",
    r"(\d{4}) (?:revised edition|edition)",
]

_SECTORAL_KEYWORDS = frozenset([
    "banking", "insurance", "telecom", "telecommunications", "healthcare",
    "finance", "financial", "securities", "medical", "energy",
])

_HORIZONTAL_DPL_KEYWORDS = frozenset([
    "personal data protection", "data protection act",
    "privacy act", "data privacy",
])


# ── ST1: Live URL Validation ───────────────────────────────────────────────────

async def _validate_url(url: str, client: httpx.AsyncClient) -> tuple[str, int]:
    """Returns (canonical_url, status_code). -1 = timeout, 0 = error/suspicious redirect."""
    headers = {"User-Agent": _AGENT_STRING}
    try:
        resp = await client.get(
            url, headers=headers, follow_redirects=True,
            timeout=_CURRENCY_FETCH_TIMEOUT_SEC,
        )
        final_url = str(resp.url)

        if _registered_domain(final_url) != _registered_domain(url):
            logger.warning("SUSPICIOUS_REDIRECT: %s → %s", url, final_url)
            return url, 0

        if resp.status_code == 403:
            alt_headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
            try:
                resp2 = await client.get(url, headers=alt_headers, follow_redirects=True, timeout=10)
                if resp2.status_code != 403:
                    return str(resp2.url), resp2.status_code
            except Exception:
                pass
            logger.info("URL_FORBIDDEN: %s", url)
            return url, 403

        if resp.status_code == 429:
            await _sleep(_CURRENCY_RETRY_WAIT_SEC)
            try:
                resp = await client.get(url, headers=headers, follow_redirects=True, timeout=10)
                return str(resp.url), resp.status_code
            except Exception:
                return url, 429

        if resp.status_code >= 500:
            logger.warning("URL_SERVER_ERROR %d: %s", resp.status_code, url)
            return final_url, resp.status_code

        return final_url, resp.status_code

    except httpx.TimeoutException:
        logger.warning("URL_TIMEOUT: %s", url)
        return url, -1
    except Exception as exc:
        logger.warning("URL_ERROR: %s — %s", url, exc)
        return url, 0


# ── ST2: Fetch page content ────────────────────────────────────────────────────

async def _fetch_page_text(url: str, document_type: str, client: httpx.AsyncClient) -> str:
    """Returns the page text for currency analysis (HTML full text; PDF first 2 pages)."""
    try:
        resp = await client.get(url, headers={"User-Agent": _AGENT_STRING},
                                follow_redirects=True, timeout=_CURRENCY_FETCH_TIMEOUT_SEC)
        if document_type == "pdf":
            try:
                import pdfplumber  # type: ignore
                import io
                with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                    pages = pdf.pages[:2]
                    return "\n".join(p.extract_text() or "" for p in pages)
            except Exception:
                return resp.text[:5000]
        return resp.text
    except Exception as exc:
        logger.warning("Fetch text failed for %s: %s", url, exc)
        return ""


# ── ST2: In-force detection ────────────────────────────────────────────────────

def _detect_currency_status(text: str, url: str) -> tuple[str, str]:
    """Pure function. Returns (currency_status, cancellation_note)."""
    lower = text.lower()
    soup = BeautifulSoup(text, "html.parser")

    # Portal-specific structured status tags (highest priority)
    if "sso.agc.gov.sg" in url:
        for tag in soup.find_all("span", class_="status-tag"):
            tag_text = tag.get_text(strip=True).lower()
            if "repealed" in tag_text:
                return "cancelled", "Repealed (SSO status tag)."
            if "current" in tag_text or "in force" in tag_text:
                return "in_force", ""

    if "legislation.gov.au" in url:
        for tag in soup.find_all("div", class_="legislation-status"):
            tag_text = tag.get_text(strip=True).lower()
            if "repealed" in tag_text or "not in force" in tag_text:
                return "cancelled", "Repealed (legislation.gov.au status)."
            if "in force" in tag_text:
                return "in_force", ""

    # Keyword scan (fallback)
    for pattern in _CANCELLED_PATTERNS:
        match = re.search(pattern, lower)
        if match:
            snippet = _extract_sentence_around(text, match.start(), chars=150)
            return "cancelled", snippet.strip()

    for pattern in _IN_FORCE_PATTERNS:
        if re.search(pattern, lower):
            return "in_force", ""

    return "uncertain", ""


def _extract_sentence_around(text: str, pos: int, chars: int = 150) -> str:
    start = max(0, pos - 30)
    end = min(len(text), pos + chars)
    return text[start:end].replace("\n", " ")


# ── ST3: Auto-replacement ──────────────────────────────────────────────────────

def _find_replacement_in_text(text: str) -> tuple[str | None, str | None]:
    """Parse text for replacement URL or act name. Returns (url_or_none, name_or_none)."""
    lower = text.lower()

    # Look for an explicit URL in the cancellation notice
    url_pattern = r"https?://[^\s\"'>]+"
    for match in re.finditer(url_pattern, text):
        candidate = match.group(0).rstrip(".,;)")
        if any(kw in candidate.lower() for kw in ("/act/", "/legislation/", "/details/", "/law/")):
            return candidate, None

    # Look for replacement act name
    for pattern in _REPLACEMENT_PATTERNS:
        match = re.search(pattern, lower)
        if match:
            name = match.group(1).strip().title()
            if len(name) > 5:
                return None, name

    return None, None


def _is_sectoral_law(act_title: str) -> bool:
    title_lower = act_title.lower()
    return (
        any(kw in title_lower for kw in _SECTORAL_KEYWORDS)
        and not any(kw in title_lower for kw in _HORIZONTAL_DPL_KEYWORDS)
    )


async def _search_portal(portal_url: str, query: str, client: httpx.AsyncClient) -> str | None:
    """Search a portal for a query. Returns first matching URL or None."""
    domain = _registered_domain(portal_url)
    if "sso.agc.gov.sg" in portal_url:
        search_url = f"https://sso.agc.gov.sg/Search?SearchAct={urllib.parse.quote_plus(query)}"
    else:
        search_url = f"{portal_url.rstrip('/')}?q={urllib.parse.quote_plus(query)}"
    try:
        resp = await client.get(search_url, headers={"User-Agent": _AGENT_STRING},
                                follow_redirects=True, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            abs_url = urllib.parse.urljoin(search_url, href)
            if _registered_domain(abs_url) == domain and len(a.get_text(strip=True)) > 5:
                return abs_url
    except Exception:
        pass
    return None


async def _handle_cancelled_act(
    act_title: str,
    page_text: str,
    portal_source: str,
    client: httpx.AsyncClient,
) -> tuple[str | None, str, bool]:
    """Handle a cancelled act. Returns (replacement_url_or_None, note, flag_for_review)."""
    replacement_url, replacement_name = _find_replacement_in_text(page_text)

    # Scenario 2: replacement URL found in notice
    if replacement_url:
        try:
            resp = await client.get(replacement_url, headers={"User-Agent": _AGENT_STRING},
                                    follow_redirects=True, timeout=10)
            if resp.status_code == 200:
                return replacement_url, f"Replaced. Using: {replacement_url}", False
        except Exception:
            pass
        logger.warning("Replacement URL invalid: %s", replacement_url)
        return None, "Cancelled. Replacement URL found but invalid.", True

    # Scenario 2b: replacement name only — search portal
    if replacement_name:
        found_url = await _search_portal(portal_source, replacement_name, client)
        if found_url:
            try:
                resp = await client.get(found_url, headers={"User-Agent": _AGENT_STRING},
                                        follow_redirects=True, timeout=10)
                if resp.status_code == 200:
                    return found_url, f"Replaced. Using: {replacement_name}", False
            except Exception:
                pass

    # Scenario 3: no replacement found — re-search portal
    for query in [f"{act_title} current version", f"{act_title} replacement"]:
        found_url = await _search_portal(portal_source, query, client)
        if found_url:
            try:
                resp = await client.get(found_url, headers={"User-Agent": _AGENT_STRING},
                                        follow_redirects=True, timeout=10)
                if resp.status_code == 200:
                    return found_url, f"Replacement found via search: {found_url}", False
            except Exception:
                pass

    return None, "Cancelled. No replacement found. Manual review required.", True


# ── ST4: last_amended extraction ───────────────────────────────────────────────

def _extract_last_amended(text: str) -> str:
    """Pure function. Returns the most recent valid 4-digit year found, or ''."""
    years: list[int] = []
    for pattern in _AMENDED_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            raw = match.group(1)
            # Extract trailing 4-digit year if full date string
            year_match = re.search(r"(\d{4})$", raw.strip())
            if year_match:
                year = int(year_match.group(1))
                if _YEAR_MIN <= year <= _YEAR_MAX:
                    years.append(year)
    return str(max(years)) if years else ""


# ── ST5: Wayback Machine archiving ────────────────────────────────────────────

async def _archive_act_url(url: str) -> str:
    """Submit url to Wayback Machine Save API. Returns snapshot URL or ''."""
    save_url = _WAYBACK_SAVE_URL.format(url=url)
    headers = {"User-Agent": _WAYBACK_UA}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            resp = await client.get(save_url, headers=headers)

            if resp.status_code == 429:
                logger.info("Wayback 429 — waiting 60s then retrying")
                await _sleep(60)
                resp = await client.get(save_url, headers=headers)

            if resp.status_code in (200, 302):
                loc = resp.headers.get("Content-Location", "")
                if loc:
                    return f"https://web.archive.org{loc}" if loc.startswith("/") else loc
                return f"https://web.archive.org/web/{url}"

            if resp.status_code == 523:
                logger.warning("ARCHIVE_UNAVAILABLE: %s", url)
            else:
                logger.warning("Wayback returned %d for %s", resp.status_code, url)
            return ""
    except httpx.TimeoutException:
        logger.warning("ARCHIVE_TIMEOUT: %s", url)
        return ""
    except Exception as exc:
        logger.error("Archive error for %s: %s", url, exc)
        return ""


# ── Public API ─────────────────────────────────────────────────────────────────

async def run_currency_check(
    candidates: list[CandidateAct],
    output_dir: str = "logs",
) -> list[CurrencyResult]:
    """
    Runs ST1 → ST2 → ST3 → ST4 → ST5 for each CandidateAct.
    Returns ALL acts (including broken/cancelled) for full traceability.
    ranker.py is responsible for filtering by currency_status.
    """
    results: list[CurrencyResult] = []
    economy = candidates[0].economy if candidates else "UNK"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    t0 = time.monotonic()

    async with httpx.AsyncClient() as client:
        for candidate in candidates:
            result = await _process_one(candidate, client)
            results.append(result)

            # ST5: rate-limit between archive requests
            if result.archive_url:
                await _sleep(_WAYBACK_RATE_LIMIT_SEC)

    _write_summary(results, economy, ts, output_dir, time.monotonic() - t0)
    return results


async def _process_one(candidate: CandidateAct, client: httpx.AsyncClient) -> CurrencyResult:
    """Process a single CandidateAct through ST1→ST5."""
    # ST1 — URL validation
    canonical_url, http_status = await _validate_url(candidate.act_url, client)

    # Broken / unreachable
    if http_status in (0, 404):
        return _make_result(candidate, canonical_url, http_status, "broken",
                            flag=False, note="", last_amended="", archive_url="")

    if http_status == -1:  # timeout
        return _make_result(candidate, canonical_url, http_status, "uncertain",
                            flag=True, note="URL_TIMEOUT — could not confirm currency status.",
                            last_amended="", archive_url="")

    if http_status == 403:
        return _make_result(candidate, canonical_url, http_status, "uncertain",
                            flag=True, note="URL_FORBIDDEN — access denied. Manual review required.",
                            last_amended="", archive_url="")

    if http_status >= 500:
        return _make_result(candidate, canonical_url, http_status, "uncertain",
                            flag=True, note=f"URL_SERVER_ERROR {http_status}.",
                            last_amended="", archive_url="")

    # ST2 — In-force detection + ST4 — last_amended (single fetch)
    page_text = await _fetch_page_text(canonical_url, candidate.document_type, client)
    currency_status, currency_note = _detect_currency_status(page_text, canonical_url)
    last_amended = _extract_last_amended(page_text)

    flag = False
    final_url = canonical_url

    # ST3 — Auto-replacement for cancelled acts
    if currency_status == "cancelled":
        replacement_url, repl_note, flag = await _handle_cancelled_act(
            candidate.act_title, page_text, candidate.portal_source, client
        )
        if replacement_url:
            final_url = replacement_url
            currency_note = repl_note
            # Re-extract last_amended from replacement page
            repl_text = await _fetch_page_text(replacement_url, candidate.document_type, client)
            last_amended = _extract_last_amended(repl_text) or last_amended
        else:
            currency_note = repl_note

    if currency_status == "uncertain":
        flag = True

    # Sectoral law check (Scenario 4): if in-force sectoral act, note the sector
    if currency_status == "in_force" and _is_sectoral_law(candidate.act_title):
        existing_note = currency_note or ""
        currency_note = f"Sectoral law. {existing_note}".strip()

    # ST5 — Wayback archiving (only for reachable URLs)
    archive = await _archive_act_url(final_url)

    return _make_result(candidate, final_url, http_status, currency_status,
                        flag=flag, note=currency_note, last_amended=last_amended,
                        archive_url=archive)


def _make_result(
    candidate: CandidateAct,
    act_url: str,
    http_status: int,
    currency_status: str,
    flag: bool,
    note: str,
    last_amended: str,
    archive_url: str,
) -> CurrencyResult:
    return CurrencyResult(
        act_title=candidate.act_title,
        act_url=act_url,
        description_snippet=candidate.description_snippet,
        document_type=candidate.document_type,
        discovery_tag=candidate.discovery_tag,
        portal_source=candidate.portal_source,
        economy=candidate.economy,
        pillar=candidate.pillar,
        pass_number=candidate.pass_number,
        http_status=http_status,
        currency_status=currency_status,
        flag_for_review=flag,
        currency_note=note,
        last_amended=last_amended,
        archive_url=archive_url,
    )


def _write_summary(results: list[CurrencyResult], economy: str, ts: str,
                   output_dir: str, elapsed_s: float) -> None:
    summary = {
        "economy": economy,
        "total_checked": len(results),
        "in_force": sum(1 for r in results if r.currency_status == "in_force"),
        "cancelled": sum(1 for r in results if r.currency_status == "cancelled"),
        "uncertain": sum(1 for r in results if r.currency_status == "uncertain"),
        "broken": sum(1 for r in results if r.currency_status == "broken"),
        "flagged_for_review": sum(1 for r in results if r.flag_for_review),
        "archived": sum(1 for r in results if r.archive_url),
        "archive_failed": sum(1 for r in results if not r.archive_url and r.currency_status != "broken"),
        "elapsed_seconds": round(elapsed_s, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(output_dir, exist_ok=True)
    path = Path(output_dir) / f"currency_summary_{economy}_{ts}.json"
    path.write_text(json.dumps(summary, indent=2))
    logger.info("[CURRENCY] %s: %d acts — %d in-force, %d cancelled, %d uncertain, %d broken",
                economy, summary["total_checked"], summary["in_force"],
                summary["cancelled"], summary["uncertain"], summary["broken"])
