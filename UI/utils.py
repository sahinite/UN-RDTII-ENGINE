"""
Shared helpers for the RDTII UI package.

Everything here is screen-agnostic: project paths, config/taxonomy readers,
run-output discovery, and the per-run artifact paths (cost snapshot + run report)
that several screens read.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import dotenv_values

# ── Project paths ─────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
ECONOMY_DIR = PROJECT_ROOT / "economies"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
TAXONOMY_PATH = PROJECT_ROOT / "taxonomy.json"
SHARED_COST_REPORT = PROJECT_ROOT / "logs" / "cost_report.json"
ENV_PATH = PROJECT_ROOT / ".env"
ROUND1_DB = PROJECT_ROOT / "data" / "database" / "ESCAP-RDTII-2.1_ Round 1 Database.xlsx"


# ── Small generic helpers ─────────────────────────────────────────────────────

def read_json(path: Path, default=None):
    """Parse a JSON file, returning `default` on any error (missing/corrupt)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_env_file() -> dict:
    """Current .env contents as a dict ({} when the file doesn't exist)."""
    return dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}


def format_elapsed(seconds: float) -> str:
    return f"{seconds:.0f}s" if seconds < 60 else f"{int(seconds // 60)}m {seconds % 60:.0f}s"


def shorten_document_label(raw: str) -> str:
    """Readable document label. Acts without a discovered title arrive as a bare
    URL (Australia's API path), which is unreadable in a chip or table."""
    if not raw.startswith("http"):
        return raw
    from urllib.parse import urlparse
    parsed = urlparse(raw)
    segments = [s for s in parsed.path.split("/") if s]
    host = parsed.netloc.replace("www.", "")
    return f"{host}/{segments[-1]}" if segments else host


def detect_fetch_kind(url: str) -> str:
    """Whether a source URL was fetched as a PDF or an HTML page."""
    u = (url or "").lower().split("?")[0]
    return "PDF" if (u.endswith(".pdf") or u.endswith("/pdf") or "/pdf/" in u) else "HTML"


# ── Economies & pillars ───────────────────────────────────────────────────────

def list_economies() -> list[str]:
    names = []
    for f in sorted(ECONOMY_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if data.get("economy_name"):
                names.append(data["economy_name"])
        except Exception:
            continue
    return names


def load_taxonomy() -> list[dict]:
    return read_json(TAXONOMY_PATH, default=[]) or []


def list_pillars() -> list[int]:
    pillars = set()
    for entry in load_taxonomy():
        m = re.match(r"P(\d+)-", entry.get("indicator_id", ""))
        if m:
            pillars.add(int(m.group(1)))
    return sorted(pillars)


# Words that describe the shape of a category rather than its subject — dropping
# them keeps the two-word pillar hint informative ("Domestic Data Protection &
# Privacy" → "Data Protection", not "Domestic Data").
_GENERIC_CATEGORY_WORDS = {"and", "&", "policies", "policy", "issues", "issue", "measures",
                           "general", "domestic", "other", "requirements", "framework"}


def pillar_hint(pillar: int) -> str:
    """Two words summarising a pillar, taken from its indicators' category."""
    categories = [e.get("category", "") for e in load_taxonomy()
                  if e.get("indicator_id", "").startswith(f"P{pillar}-") and e.get("category")]
    if not categories:
        return ""
    category = max(set(categories), key=categories.count)
    words = [w for w in category.replace("/", " ").split()
             if w.lower() not in _GENERIC_CATEGORY_WORDS]
    return " ".join(words[:2]) if words else category.split()[0]


def pillar_choices() -> list[tuple[str, int]]:
    """(label, value) pairs so the dropdown says what each pillar covers."""
    choices = []
    for p in list_pillars():
        hint = pillar_hint(p)
        choices.append((f"Pillar {p} — {hint}" if hint else f"Pillar {p}", p))
    return choices


# ── Run outputs & per-run artifacts ───────────────────────────────────────────

def user_output_dir(user_hash: str | None = None, run_id: str | None = None) -> Path:
    """Output root for either legacy shared runs or one tenant/run."""
    path = OUTPUT_DIR
    if user_hash:
        path = path / user_hash
    if run_id:
        path = path / run_id
    return path


def list_runs(user_hash: str | None = None) -> list[str]:
    """Run output CSV filenames, newest first.

    Pre-auth runs live directly under outputs/. Authenticated UI runs live under
    outputs/{user_hash}/{run_id}/ and are only listed for that user.
    """
    root = user_output_dir(user_hash)
    pattern = "*.csv" if user_hash is None else "*/*.csv"
    files = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return [f.name for f in files]


def find_run_csv(csv_name: str, user_hash: str | None = None) -> Path:
    """Resolve a run CSV name to the matching legacy or tenant path."""
    if user_hash:
        matches = sorted(
            user_output_dir(user_hash).glob(f"*/{csv_name}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if matches:
            return matches[0]
        return user_output_dir(user_hash) / csv_name
    return OUTPUT_DIR / csv_name


def parse_run_name(csv_name: str) -> tuple[str | None, int | None]:
    """'Singapore_P7_<ts>.csv' → ('Singapore', 7); (None, None) if unparseable."""
    m = re.match(r"(.+)_P(\d+)_", csv_name)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def run_basename(csv_name: str) -> str:
    return csv_name[:-4] if csv_name.lower().endswith(".csv") else csv_name


# Per-run artifacts live next to the CSV so each run keeps its own cost + report
# instead of overwriting a single shared file. Basename = "<economy>_P<pillar>_<ts>".
def cost_report_path(csv_name: str, user_hash: str | None = None) -> Path:
    csv_path = find_run_csv(csv_name, user_hash)
    return csv_path.with_name(f"{run_basename(csv_name)}_cost.json")


def run_report_path(csv_name: str, user_hash: str | None = None) -> Path:
    csv_path = find_run_csv(csv_name, user_hash)
    return csv_path.with_name(f"{run_basename(csv_name)}_runReport.md")


def load_run_cost(csv_name: str, user_hash: str | None = None) -> dict:
    """Per-run cost snapshot. Falls back to the shared logs/cost_report.json only
    when it clearly belongs to this run (same economy + pillar)."""
    per_run = read_json(cost_report_path(csv_name, user_hash))
    if per_run is not None:
        return per_run
    shared = read_json(SHARED_COST_REPORT)
    if shared:
        economy, pillar = parse_run_name(csv_name)
        try:
            if economy and str(shared.get("economy", "")).lower() == economy.lower() \
                    and int(shared.get("pillar", -1)) == int(pillar):
                return shared
        except (TypeError, ValueError):
            pass
    return {}


def placeholder_cost_report(csv_name: str) -> dict:
    """Structurally-valid placeholder cost report for a run whose per-run cost was
    never recorded (older runs). Flagged synthetic so the UI can say so."""
    economy, pillar = parse_run_name(csv_name)
    zero = {"calls": 0, "cost_usd": 0.0, "latency_ms": 0.0,
            "input_tokens": 0, "output_tokens": 0}
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "economy": (economy or "").lower(), "pillar": pillar,
        "model_version": "unknown", "processing_time_seconds": 0,
        "total_cost_usd": 0.0, "synthetic": True,
        "components": {
            "llm": dict(zero),
            "ocr": {**zero, "pages_processed": 0},
            "embedding": dict(zero),
            "crawling": {**zero, "pages_fetched": 0},
        },
    }
