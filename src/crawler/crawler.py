"""Crawl4AI-based domain-locked depth-2 crawler. [Z1-3]

Two-pass discovery strategy:
  Pass 1 — KNOWN: seed BFS from Round 1 database URLs → confirm existence.
  Pass 2 — NEW:   seed BFS from indicator keyword search URLs → discover new acts.
"""

import asyncio
import json
import logging
import os
import random
import time
import traceback
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from src.cli.progress import substep
from src.config.economy_config import EconomyConfig, Portal
from src.crawler.crawl4ai_runner import (
    SSO_TIMEOUT_MS as _SSO_TIMEOUT_MS,
    SSO_WAIT_FOR as _SSO_WAIT_FOR,
    close_shared_crawler as _close_shared_crawler,
    fetch_with_playwright as _fetch_with_playwright,
    is_js_portal as _is_js_portal,
)
from src.crawler.exceptions import CrawlerError
from src.crawler.probe import ProbeResult

logger = logging.getLogger(__name__)

# ── Environment config ─────────────────────────────────────────────────────────

_CRAWL_TIMEOUT_MS = int(os.getenv("CRAWL_TIMEOUT_MS", "15000"))
_CRAWL_MAX_DEPTH = int(os.getenv("CRAWL_MAX_DEPTH", "2"))
_CRAWL_MAX_PAGES = int(os.getenv("CRAWL_MAX_PAGES", "5"))
_MAX_CONCURRENT_CRAWLS = int(os.getenv("MAX_CONCURRENT_CRAWLS", "3"))
_CRAWL_JITTER_MS = int(os.getenv("CRAWL_JITTER_MS", "400"))
# Pass 1 seeds straight from the Round 1 DB (already-known act URLs). We only need
# to confirm them, not BFS-expand every cross-reference — so it runs at depth 0 and
# the seed list is deduped (by base URL, fragments/anchors stripped) and capped.
_CRAWL_KNOWN_MAX_SEEDS = int(os.getenv("CRAWL_KNOWN_MAX_SEEDS", "20"))

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

_SKIP_EXTENSIONS = frozenset([
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map",
])


_ECONOMY_ISO: dict[str, str] = {
    "singapore": "SG", "malaysia": "MY", "thailand": "TH",
    "australia": "AU", "indonesia": "ID", "vietnam": "VN",
    "philippines": "PH", "cambodia": "KH", "myanmar": "MM",
}

# Module-level alias so tests can patch asyncio.sleep without side-effects
_sleep = asyncio.sleep


# ── Output contract ────────────────────────────────────────────────────────────

@dataclass
class CandidateAct:
    act_title: str
    act_url: str
    description_snippet: str  # first 500 chars from listing page
    document_type: str         # "pdf" | "html"
    discovery_tag: str         # "KNOWN" | "NEW"
    portal_source: str
    economy: str               # ISO code, e.g. "SG"
    pillar: str                # "P6" | "P7" | "P6+P7"
    pass_number: int           # 1 = KNOWN pass, 2 = NEW pass


# ── URL helpers ────────────────────────────────────────────────────────────────

def _normalise_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.lower().rstrip("/"))
    params = urllib.parse.parse_qs(parsed.query)
    keep = {k: v for k, v in params.items() if k not in ("lang", "language", "locale")}
    new_query = urllib.parse.urlencode(keep, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


# Domain extraction lives in the shared, offline-pinned utility; re-exported here
# under the historical name for existing importers (discover, currency, this module).
from src.crawler.domains import registered_domain as _registered_domain


def _is_same_domain(url: str, portal_domain: str) -> bool:
    return _registered_domain(url) == portal_domain


def _is_skip_url(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    ext = Path(path).suffix
    return ext in _SKIP_EXTENSIONS


def _make_absolute(href: str, base_url: str) -> str:
    return urllib.parse.urljoin(base_url, href)


def _short_url(url: str, max_len: int = 70) -> str:
    """Trim a URL for single-line terminal display."""
    if len(url) <= max_len:
        return url
    return url[: max_len - 1] + "…"


def _dedupe_seed_urls(urls: list[str], cap: int) -> list[str]:
    """Collapse known-act URLs to their base page (drop #fragment and trailing
    separators), de-duplicate, and cap the count.

    The Round 1 DB lists many anchor-level provision URLs (…/Act/PDPA2012#pr26-)
    that all resolve to the same act page; without this we'd fetch the same page
    dozens of times.
    """
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        parsed = urllib.parse.urlparse(u)
        base = parsed._replace(fragment="").geturl().rstrip("/;,")
        key = _normalise_url(base)
        if key in seen:
            continue
        seen.add(key)
        out.append(base)
        if len(out) >= cap:
            break
    return out


def _economy_iso(economy_config: EconomyConfig) -> str:
    return _ECONOMY_ISO.get(economy_config.economy_name.lower(), economy_config.economy_name[:2].upper())


# ── Fetch layer (module-level so tests can monkeypatch) ────────────────────────

async def _fetch_with_httpx(url: str, client: httpx.AsyncClient) -> tuple[str, int]:
    headers = {"User-Agent": random.choice(_USER_AGENTS)}
    try:
        resp = await client.get(url, headers=headers, timeout=_CRAWL_TIMEOUT_MS / 1000, follow_redirects=True)
        return resp.text, resp.status_code
    except Exception:
        return "", 0


# ── Document type detection ────────────────────────────────────────────────────

async def _detect_document_type(url: str, client: httpx.AsyncClient) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    try:
        resp = await client.head(url, timeout=2.0, follow_redirects=True)
        if "application/pdf" in resp.headers.get("content-type", "").lower():
            return "pdf"
    except Exception:
        pass
    return "html"


# ── HTML parsing ───────────────────────────────────────────────────────────────

def _extract_act_links(html: str, base_url: str, portal_domain: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    acts: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        abs_url = _make_absolute(href, base_url)
        if _is_skip_url(abs_url):
            continue
        if not _is_same_domain(abs_url, portal_domain):
            continue
        norm = _normalise_url(abs_url)
        if norm in seen:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        seen.add(norm)
        parent = a.find_parent(["li", "div", "tr", "p"])
        snippet = (parent.get_text(separator=" ", strip=True)[:500] if parent else "")
        acts.append({"url": abs_url, "title": title, "snippet": snippet})
    return acts


def _has_next_page(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        if text in ("next", "next page", "›", "»", ">", "next »"):
            return a["href"]
    return None


# ── Backoff ────────────────────────────────────────────────────────────────────

async def _with_retry(fetch_fn, max_retries: int = 3) -> tuple[str, int]:
    for attempt in range(max_retries + 1):
        html, status = await fetch_fn()
        if status == 403:
            logger.info("HTTP 403 – skipping immediately")
            return "", 403
        if status == 429:
            if attempt == max_retries:
                logger.warning("HTTP 429 – max retries reached, skipping")
                return "", 429
            wait_s = 2 ** attempt
            logger.info("HTTP 429 – backoff %ds (attempt %d)", wait_s, attempt + 1)
            await _sleep(wait_s)
            continue
        return html, status
    return "", 0


async def _jitter() -> None:
    jitter_ms = random.randint(200, max(600, _CRAWL_JITTER_MS))
    await _sleep(jitter_ms / 1000)


# ── Session logging ────────────────────────────────────────────────────────────

class _CrawlSession:
    def __init__(self, economy_iso: str, pillar: str, output_dir: str):
        self.economy_iso = economy_iso
        self.pillar = pillar
        self.output_dir = output_dir
        self.seen_urls: set[str] = set()
        self._ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._stats: dict[int, dict] = {1: {"pages": 0, "acts": 0}, 2: {"pages": 0, "acts": 0}}
        self._errors = 0

    def log_page(self, url: str, status: str, depth: int, acts: int, pass_num: int, elapsed_ms: int) -> None:
        entry = {
            "url": url, "status": status, "depth": depth, "acts_extracted": acts,
            "pass": pass_num, "economy": self.economy_iso,
            "elapsed_ms": elapsed_ms, "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        os.makedirs(self.output_dir, exist_ok=True)
        log_path = Path(self.output_dir) / f"crawl_{self.economy_iso}_{self._ts}.jsonl"
        with log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        if pass_num in self._stats:
            self._stats[pass_num]["pages"] += 1
            self._stats[pass_num]["acts"] += acts

    def log_error(self, url: str) -> None:
        self._errors += 1
        os.makedirs(self.output_dir, exist_ok=True)
        err_path = Path(self.output_dir) / f"crawl_errors_{self.economy_iso}_{self._ts}.log"
        with err_path.open("a") as f:
            f.write(f"URL: {url}\n{traceback.format_exc()}\n\n")

    def write_summary(self, total: int, known: int, new: int, elapsed_s: float) -> None:
        summary = {
            "economy": self.economy_iso, "pillar": self.pillar,
            "pass_1_pages_crawled": self._stats[1]["pages"],
            "pass_1_acts_found": self._stats[1]["acts"],
            "pass_2_pages_crawled": self._stats[2]["pages"],
            "pass_2_acts_found": self._stats[2]["acts"],
            "total_candidate_acts": total, "known_count": known, "new_count": new,
            "errors": self._errors, "elapsed_seconds": round(elapsed_s, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        os.makedirs(self.output_dir, exist_ok=True)
        summary_path = Path(self.output_dir) / f"crawl_summary_{self.economy_iso}_{self._ts}.json"
        summary_path.write_text(json.dumps(summary, indent=2))
        logger.info("[CRAWLER] %s Pass 1: %d pages crawled, %d acts found (KNOWN)",
                    self.economy_iso, self._stats[1]["pages"], self._stats[1]["acts"])
        logger.info("[CRAWLER] %s Pass 2: %d pages crawled, %d new acts found (NEW)",
                    self.economy_iso, self._stats[2]["pages"], self._stats[2]["acts"])
        logger.info("[CRAWLER] %s complete: %d candidate acts total (%d KNOWN, %d NEW)",
                    self.economy_iso, total, known, new)


# ── BFS crawler ────────────────────────────────────────────────────────────────

def _lookup_portal_cfg(portal_url: str, economy_config: EconomyConfig) -> Portal | None:
    """Return the Portal config object whose URL matches portal_url, or None."""
    norm = _normalise_url(portal_url)
    for p in economy_config.portals:
        if _normalise_url(str(p.url)) == norm:
            return p
    return None


async def _crawl_bfs(
    start_urls: list[str],
    portal_url: str,
    pass_num: int,
    session: _CrawlSession,
    client: httpx.AsyncClient,
    portal_cfg: Portal | None = None,
    max_depth: int = _CRAWL_MAX_DEPTH,
) -> list[dict]:
    """BFS from start_urls up to max_depth within the portal's domain."""
    portal_domain = _registered_domain(portal_url)
    is_js = _is_js_portal(portal_cfg) if portal_cfg else False
    pw_wait_for = (portal_cfg.playwright_wait_for if portal_cfg else None) or _SSO_WAIT_FOR
    pw_timeout_ms = (portal_cfg.playwright_timeout_ms if portal_cfg else None) or _SSO_TIMEOUT_MS
    do_pagination = portal_cfg.follow_pagination if portal_cfg else False

    queue: list[tuple[str, int]] = [(u, 0) for u in start_urls]
    acts_found: list[dict] = []
    pages_crawled = 0
    page_budget = _CRAWL_MAX_PAGES * len(start_urls)
    engine_tag = "playwright" if is_js else "httpx"

    while queue and pages_crawled < page_budget:
        url, depth = queue.pop(0)
        norm = _normalise_url(url)

        if norm in session.seen_urls:
            session.log_page(url, "duplicate", depth, 0, pass_num, 0)
            continue
        if not _is_same_domain(url, portal_domain):
            session.log_page(url, "off_domain", depth, 0, pass_num, 0)
            logger.debug("Off-domain URL skipped: %s", url)
            continue

        session.seen_urls.add(norm)
        start_ms = time.monotonic()

        # Per-URL visibility: surface to spinner substep + logs which URL is live.
        substep(
            f"Pass {pass_num} [{engine_tag}] {pages_crawled + 1}/{page_budget} d{depth} "
            f"· {_short_url(url)}"
        )
        logger.info(
            "[CRAWL] pass=%d depth=%d (%s) GET %s",
            pass_num, depth, engine_tag, url,
        )

        try:
            await _jitter()
            if is_js:
                html, status = await _with_retry(
                    lambda u=url: _fetch_with_playwright(u, pw_wait_for, pw_timeout_ms)
                )
            else:
                html, status = await _with_retry(
                    lambda u=url, c=client: _fetch_with_httpx(u, c)
                )
        except Exception:
            session.log_error(url)
            session.log_page(url, "error", depth, 0, pass_num, 0)
            continue

        elapsed_ms = int((time.monotonic() - start_ms) * 1000)

        if status in (0,):
            session.log_page(url, "error", depth, 0, pass_num, elapsed_ms)
            logger.info("[CRAWL] pass=%d no response (%dms) · %s", pass_num, elapsed_ms, _short_url(url))
            continue
        if status in (403, 429):
            session.log_page(url, f"http_{status}", depth, 0, pass_num, elapsed_ms)
            substep(f"Pass {pass_num} HTTP {status} blocked · {_short_url(url)}")
            logger.info("[CRAWL] pass=%d HTTP %d blocked · %s", pass_num, status, _short_url(url))
            continue

        page_acts = _extract_act_links(html, url, portal_domain)
        session.log_page(url, "ok", depth, len(page_acts), pass_num, elapsed_ms)
        logger.info(
            "[CRAWL] pass=%d 200 OK (%dms) %d link(s) · %s",
            pass_num, elapsed_ms, len(page_acts), _short_url(url),
        )
        acts_found.extend(page_acts)
        pages_crawled += 1

        if depth < max_depth:
            for act in page_acts:
                child_norm = _normalise_url(act["url"])
                if child_norm not in session.seen_urls:
                    queue.append((act["url"], depth + 1))

        # Follow pagination at the same depth when the portal declares it
        if do_pagination:
            next_href = _has_next_page(html)
            if next_href:
                next_url = _make_absolute(next_href, url)
                if _normalise_url(next_url) not in session.seen_urls:
                    queue.insert(0, (next_url, depth))

    return acts_found


# ── Search URL construction ────────────────────────────────────────────────────

def _build_search_urls(
    portal: ProbeResult,
    economy_config: EconomyConfig,
    taxonomy: list[dict],
) -> list[str]:
    pattern: str | None = None
    for p in economy_config.portals:
        if _normalise_url(str(p.url)) == _normalise_url(portal.url):
            pattern = p.search_url_pattern
            break

    keywords: list[str] = []
    for ind in taxonomy:
        keywords.extend(ind.get("probe_keywords", []))

    urls: list[str] = []
    seen: set[str] = set()
    for kw in keywords[:15]:  # cap to avoid excessive requests
        encoded = urllib.parse.quote_plus(kw)
        if pattern:
            url = pattern.replace("{keyword}", encoded)
        else:
            url = f"{portal.url.rstrip('/')}?q={encoded}"
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


# ── Known URL loader ───────────────────────────────────────────────────────────

def load_known_urls(xlsx_path: str, economy_name: str | None = None) -> set[str]:
    """Load known act URLs from a Round 1 Database XLSX file."""
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:
        raise CrawlerError("openpyxl required: pip install openpyxl") from exc

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    urls: set[str] = set()
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            row_text = " ".join(str(c) for c in row if c is not None).lower()
            if economy_name is not None and economy_name.lower() not in row_text:
                continue
            for cell in row:
                if cell and isinstance(cell, str) and cell.startswith("http"):
                    urls.add(cell.strip())
    return urls


# ── Public entry point ─────────────────────────────────────────────────────────

async def run_crawler(
    probe_results: list[ProbeResult],
    economy_config: EconomyConfig,
    taxonomy: list[dict],
    known_urls: set[str],
    output_dir: str = "logs",
) -> list[CandidateAct]:
    """
    Run Pass 1 (KNOWN) then Pass 2 (NEW) across all active portals.
    Returns deduplicated CandidateAct list — KNOWN acts sorted before NEW.
    Raises CrawlerError if no candidate acts are found.
    """
    iso = _economy_iso(economy_config)
    pillar = "P6+P7"
    session = _CrawlSession(iso, pillar, output_dir)
    t0 = time.monotonic()
    candidates: list[CandidateAct] = []
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CRAWLS)
    known_norm = {_normalise_url(u) for u in known_urls}

    async with httpx.AsyncClient() as client:

        # ── Pass 1: KNOWN ──────────────────────────────────────────────────────
        for portal in probe_results:
            if not portal.is_active:
                continue
            portal_domain = _registered_domain(portal.url)
            seed = [u for u in known_urls if _registered_domain(u) == portal_domain]
            if not seed:
                continue
            seed = _dedupe_seed_urls(seed, _CRAWL_KNOWN_MAX_SEEDS)
            logger.info("[CRAWL] Pass 1 %s: %d known seed URL(s) after dedupe", portal_domain, len(seed))
            portal_cfg = _lookup_portal_cfg(portal.url, economy_config)
            async with semaphore:
                try:
                    # depth 0: confirm the known act pages; don't BFS-expand them
                    acts = await _crawl_bfs(seed, portal.url, 1, session, client, portal_cfg, max_depth=0)
                except Exception as exc:
                    logger.error("Pass 1 error on %s: %s", portal.url, exc)
                    session._errors += 1
                    continue
            for act in acts:
                doc_type = await _detect_document_type(act["url"], client)
                candidates.append(CandidateAct(
                    act_title=act["title"],
                    act_url=act["url"],
                    description_snippet=act["snippet"][:500],
                    document_type=doc_type,
                    discovery_tag="KNOWN",
                    portal_source=portal.url,
                    economy=iso,
                    pillar=pillar,
                    pass_number=1,
                ))

        # ── Pass 2: NEW ────────────────────────────────────────────────────────
        pass1_norm = {_normalise_url(c.act_url) for c in candidates}
        known_titles_lower = {c.act_title.lower() for c in candidates}

        for portal in probe_results:
            if not portal.is_active:
                continue
            search_urls = _build_search_urls(portal, economy_config, taxonomy)
            if not search_urls:
                continue
            portal_cfg = _lookup_portal_cfg(portal.url, economy_config)
            async with semaphore:
                try:
                    acts = await _crawl_bfs(search_urls, portal.url, 2, session, client, portal_cfg)
                except Exception as exc:
                    logger.error("Pass 2 error on %s: %s", portal.url, exc)
                    session._errors += 1
                    continue
            for act in acts:
                norm = _normalise_url(act["url"])
                if norm in pass1_norm:
                    continue  # already captured in Pass 1
                tag = "KNOWN" if (norm in known_norm or act["title"].lower() in known_titles_lower) else "NEW"
                doc_type = await _detect_document_type(act["url"], client)
                candidates.append(CandidateAct(
                    act_title=act["title"],
                    act_url=act["url"],
                    description_snippet=act["snippet"][:500],
                    document_type=doc_type,
                    discovery_tag=tag,
                    portal_source=portal.url,
                    economy=iso,
                    pillar=pillar,
                    pass_number=2,
                ))
                pass1_norm.add(norm)

    # Both passes done — release the reused Chromium.
    await _close_shared_crawler()

    elapsed = time.monotonic() - t0
    known_count = sum(1 for c in candidates if c.discovery_tag == "KNOWN")
    new_count = sum(1 for c in candidates if c.discovery_tag == "NEW")
    session.write_summary(len(candidates), known_count, new_count, elapsed)

    if not candidates:
        raise CrawlerError(f"No candidate acts found for {iso}")

    candidates.sort(key=lambda c: (0 if c.discovery_tag == "KNOWN" else 1))
    return candidates
