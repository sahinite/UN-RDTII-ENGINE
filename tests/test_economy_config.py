"""Schema validation tests — happy path + failure modes. [Z1-1.4]"""

import pytest
from pydantic import ValidationError

from src.config.economy_config import (
    EconomyConfig,
    InvalidEconomyConfigError,
    UnknownEconomyError,
    load_economy,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

_SG_DICT = {
    "economy_name": "Singapore",
    "iso_code": "SG",
    "un_name": "Singapore",
    "script_type": "latin",
    "languages": ["en"],
    "portals": [{"name": "SSO", "url": "https://sso.agc.gov.sg"}],
}

# ── Happy path — model_validate (no filesystem) ────────────────────────────────


def test_singapore_model_validates():
    cfg = EconomyConfig.model_validate(_SG_DICT)
    assert cfg.economy_name == "Singapore"
    assert cfg.script_type == "latin"
    assert cfg.ocr_engine == "tesseract"
    assert cfg.be_year_conversion is False


def test_ocr_engine_derived_latin():
    cfg = EconomyConfig.model_validate(_SG_DICT)
    assert cfg.ocr_engine == "tesseract"


def test_ocr_engine_override_respected():
    data = {**_SG_DICT, "ocr_engine_override": "azure"}
    cfg = EconomyConfig.model_validate(data)
    assert cfg.ocr_engine == "azure"


def test_portals_unlimited():
    many_portals = [
        {"name": f"Portal {i}", "url": f"https://example{i}.gov.sg"}
        for i in range(10)
    ]
    cfg = EconomyConfig.model_validate({**_SG_DICT, "portals": many_portals})
    assert len(cfg.portals) == 10


# ── Happy path — load_economy (filesystem) ────────────────────────────────────


def test_load_singapore():
    cfg = load_economy("singapore")
    assert cfg.economy_name == "Singapore"
    assert cfg.ocr_engine == "tesseract"
    # SSO (primary), PDPC regulator (html_js), Gazette (secondary)
    assert len(cfg.portals) == 3
    pdpc = next(p for p in cfg.portals if "pdpc.gov.sg" in str(p.url))
    assert pdpc.fetch == "html_js"


def test_load_economy_case_insensitive():
    cfg = load_economy("Singapore")
    assert cfg.economy_name == "Singapore"


# ── Failure modes — missing required fields ────────────────────────────────────


def test_missing_economy_name_rejected():
    data = {k: v for k, v in _SG_DICT.items() if k != "economy_name"}
    with pytest.raises(ValidationError, match="economy_name"):
        EconomyConfig.model_validate(data)


def test_missing_script_type_rejected():
    data = {k: v for k, v in _SG_DICT.items() if k != "script_type"}
    with pytest.raises(ValidationError, match="script_type"):
        EconomyConfig.model_validate(data)


def test_missing_portals_rejected():
    data = {k: v for k, v in _SG_DICT.items() if k != "portals"}
    with pytest.raises(ValidationError, match="portals"):
        EconomyConfig.model_validate(data)


# ── Failure modes — invalid values ────────────────────────────────────────────


def test_malformed_portal_url_rejected():
    data = {**_SG_DICT, "portals": [{"name": "Bad", "url": "not-a-url"}]}
    with pytest.raises(ValidationError):
        EconomyConfig.model_validate(data)


def test_unsupported_script_type_rejected():
    data = {**_SG_DICT, "script_type": "cyrillic"}
    with pytest.raises(ValidationError):
        EconomyConfig.model_validate(data)


def test_unsupported_ocr_override_rejected():
    data = {**_SG_DICT, "ocr_engine_override": "abbyy"}
    with pytest.raises(ValidationError):
        EconomyConfig.model_validate(data)


def test_empty_portals_list_rejected():
    data = {**_SG_DICT, "portals": []}
    with pytest.raises(ValidationError, match="portals"):
        EconomyConfig.model_validate(data)


def test_empty_languages_list_rejected():
    data = {**_SG_DICT, "languages": []}
    with pytest.raises(ValidationError, match="languages"):
        EconomyConfig.model_validate(data)


def test_invalid_language_code_rejected():
    data = {**_SG_DICT, "languages": ["english"]}
    with pytest.raises(ValidationError, match="ISO 639"):
        EconomyConfig.model_validate(data)


def test_extra_yaml_field_rejected():
    data = {**_SG_DICT, "pillar_hint": 7}
    with pytest.raises(ValidationError):
        EconomyConfig.model_validate(data)


# ── Named exceptions from load_economy ────────────────────────────────────────


def test_unknown_economy_raises_named_error():
    with pytest.raises(UnknownEconomyError) as exc_info:
        load_economy("narnia")
    msg = str(exc_info.value)
    assert "not supported yet" in msg
    assert "Adding a new economy" in msg


# ── Fuzzy economy name matching ───────────────────────────────────────────────


def test_misspelled_economy_suggests_correction():
    """load_economy('singpore') should suggest 'Singapore' in the error message."""
    with pytest.raises(UnknownEconomyError) as exc_info:
        load_economy("singpore")
    assert "Did you mean: Singapore" in str(exc_info.value)


def test_whitespace_stripped_before_lookup():
    """load_economy('  singapore  ') should resolve correctly."""
    cfg = load_economy("  singapore  ")
    assert cfg.economy_name == "Singapore"


def test_uppercase_economy_resolves():
    """load_economy('SINGAPORE') should resolve correctly."""
    cfg = load_economy("SINGAPORE")
    assert cfg.economy_name == "Singapore"


def test_trailing_whitespace_malaysia_resolves():
    """load_economy('malaysia ') should resolve correctly."""
    cfg = load_economy("malaysia ")
    assert cfg.economy_name == "Malaysia"


# ── ISO code short-form resolution ────────────────────────────────────────────


def test_iso_lowercase_sg_resolves():
    cfg = load_economy("sg")
    assert cfg.economy_name == "Singapore"


def test_iso_uppercase_SG_resolves():
    cfg = load_economy("SG")
    assert cfg.economy_name == "Singapore"


def test_iso_au_resolves():
    cfg = load_economy("au")
    assert cfg.economy_name == "Australia"


def test_iso_my_resolves():
    cfg = load_economy("my")
    assert cfg.economy_name == "Malaysia"


def test_unknown_iso_code_raises_unsupported_message():
    with pytest.raises(UnknownEconomyError) as exc_info:
        load_economy("vn")
    assert "not supported yet" in str(exc_info.value)


# ── Portal type field (added Z1-2) ─────────────────────────────────────────────


def test_portal_type_defaults_to_primary():
    cfg = EconomyConfig.model_validate(_SG_DICT)
    assert cfg.portals[0].type == "primary"


def test_portal_type_secondary_accepted():
    data = {**_SG_DICT, "portals": [{"name": "Gazette", "url": "https://gazette.gov.sg", "type": "secondary"}]}
    cfg = EconomyConfig.model_validate(data)
    assert cfg.portals[0].type == "secondary"


def test_portal_type_invalid_rejected():
    data = {**_SG_DICT, "portals": [{"name": "X", "url": "https://x.gov", "type": "unknown"}]}
    with pytest.raises(ValidationError):
        EconomyConfig.model_validate(data)


def test_portal_search_url_pattern_accepted():
    data = {**_SG_DICT, "portals": [
        {"name": "SSO", "url": "https://sso.agc.gov.sg",
         "search_url_pattern": "https://sso.agc.gov.sg/Search?SearchAct={keyword}"}
    ]}
    cfg = EconomyConfig.model_validate(data)
    assert cfg.portals[0].search_url_pattern is not None


def test_portal_search_url_pattern_defaults_to_none():
    cfg = EconomyConfig.model_validate(_SG_DICT)
    assert cfg.portals[0].search_url_pattern is None


def test_invalid_config_raises_named_error(tmp_path, monkeypatch):
    bad_yaml = tmp_path / "broken.yaml"
    bad_yaml.write_text("economy_name: Broken\nscript_type: latin\n")  # missing portals
    import src.config.economy_config as mod
    monkeypatch.setattr(mod, "_ECONOMIES_DIR", tmp_path)
    with pytest.raises(InvalidEconomyConfigError) as exc_info:
        load_economy("broken")
    assert "Broken" in str(exc_info.value) or "broken" in str(exc_info.value).lower()
