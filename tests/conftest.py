"""Shared pytest fixtures for all test modules."""

import os

# Must be set before any library loads OpenMP (faiss loads one copy at import
# time; torch/sentence-transformers loads a second one, which the runtime
# rejects with SIGABRT on macOS).  Setting this env var tells the Intel OMP
# runtime to tolerate multiple instances in the same process.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# Prevent HuggingFace tokenizers from spawning a parallelism thread pool inside
# a pytest worker process — avoids deadlocks on fork.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Single-threaded OMP avoids the race that triggers the duplicate-lib check on
# some macOS Python builds.
os.environ.setdefault("OMP_NUM_THREADS", "1")

import pytest

from src.config.economy_config import EconomyConfig

# ── Economy config fixtures ────────────────────────────────────────────────────

_SG_PORTAL_PRIMARY = {
    "name": "Singapore Statutes Online",
    "url": "https://sso.agc.gov.sg",
    "type": "primary",
    "js_required": True,
    "playwright_wait_for": "css:a[href*='/Act/']",
    "playwright_timeout_ms": 20000,
    "follow_pagination": True,
}
_SG_PORTAL_SECONDARY = {"name": "Singapore Government Gazette", "url": "https://www.egazette.gov.sg", "type": "secondary"}

_MY_PORTAL_PRIMARY = {"name": "Attorney General's Chambers", "url": "https://agc.gov.my", "type": "primary"}
_MY_PORTAL_SECONDARY = {"name": "Federal Gazette", "url": "https://www.federalgazette.agc.gov.my", "type": "secondary"}


@pytest.fixture
def sg_economy() -> EconomyConfig:
    """Singapore economy config — English only, no translation."""
    return EconomyConfig.model_validate({
        "economy_name": "Singapore",
        "iso_code": "SG",
        "un_name": "Singapore",
        "script_type": "latin",
        "languages": ["en"],
        "portals": [_SG_PORTAL_PRIMARY, _SG_PORTAL_SECONDARY],
    })


@pytest.fixture
def malaysia_economy() -> EconomyConfig:
    """Malaysia economy config — Bahasa + English, DeepL translation active."""
    return EconomyConfig.model_validate({
        "economy_name": "Malaysia",
        "iso_code": "MY",
        "un_name": "Malaysia",
        "script_type": "latin",
        "languages": ["ms", "en"],
        "translation_provider": "deepl",
        "portals": [_MY_PORTAL_PRIMARY, _MY_PORTAL_SECONDARY],
    })


# ── Taxonomy fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def full_taxonomy() -> list[dict]:
    """All 10 indicators with probe_keywords — mirrors the real taxonomy.json."""
    return [
        {"indicator_id": "P6-I1", "name": "General prohibition / restriction",
         "probe_keywords": ["cross-border data transfer", "personal data transfer restriction"]},
        {"indicator_id": "P6-I2", "name": "Adequacy standard",
         "probe_keywords": ["adequacy decision data transfer", "comparable protection personal data"]},
        {"indicator_id": "P6-I3", "name": "Contractual safeguards",
         "probe_keywords": ["standard contractual clauses", "binding corporate rules"]},
        {"indicator_id": "P6-I4", "name": "Consent exception",
         "probe_keywords": ["consent personal data transfer", "individual consent data overseas"]},
        {"indicator_id": "P6-I5", "name": "Other exceptions",
         "probe_keywords": ["personal data transfer exceptions", "vital interests data transfer"]},
        {"indicator_id": "P7-I1", "name": "Legal basis for processing",
         "probe_keywords": ["personal data protection act", "lawful basis data processing"]},
        {"indicator_id": "P7-I2", "name": "Purpose limitation",
         "probe_keywords": ["purpose limitation personal data", "data use restriction"]},
        {"indicator_id": "P7-I3", "name": "Data subject rights",
         "probe_keywords": ["right to access personal data", "data subject rights"]},
        {"indicator_id": "P7-I4", "name": "Data breach notification",
         "probe_keywords": ["data breach notification", "mandatory breach notification"]},
        {"indicator_id": "P7-I5", "name": "Enforcement and penalties",
         "probe_keywords": ["data protection authority", "personal data penalty"]},
    ]


@pytest.fixture(autouse=True)
def reset_wayback_latch_fixture():
    """Reset the process-wide Wayback latch so it can't leak across tests."""
    from src.output import validator as _v
    _v.reset_wayback_latch()
    yield
    _v.reset_wayback_latch()


@pytest.fixture(autouse=True)
def clear_translation_memory(monkeypatch):
    """
    Clear the in-session translation cache between tests and prevent disk I/O.
    Mocking _load/_save ensures tests are not affected by leftover cache files.
    """
    from src.crawler import probe as probe_module
    probe_module._translation_memory.clear()
    monkeypatch.setattr(probe_module, "_load_translation_cache", lambda *a, **kw: None)
    monkeypatch.setattr(probe_module, "_save_translation_cache", lambda *a, **kw: None)
    yield
    probe_module._translation_memory.clear()
