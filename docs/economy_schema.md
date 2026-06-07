# Economy Config Schema Reference

Every economy is described by a single YAML file in `economies/`.
The file is loaded and validated by `src/config/economy_config.py`.

---

## Fields

### Required

| Field | Type | Description |
|-------|------|-------------|
| `economy_name` | `str` | Full display name, e.g. `Singapore` |
| `script_type` | `"latin"` \| `"asian"` | Determines default OCR engine (see below) |
| `languages` | `list[str]` | ISO 639 codes, 2–3 lowercase letters, at least one |
| `portals` | `list[Portal]` | At least one government portal (unlimited, no pillar tagging) |

Each **Portal** entry has:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Human-readable portal name |
| `url` | `HttpUrl` | Base URL — must be a valid URL |

### Optional (all default to `null` / `false`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ocr_engine_override` | `"tesseract"` \| `"paddleocr"` \| `"azure"` \| `"mistral_ocr"` \| `null` | `null` | Force a specific OCR engine instead of the derived default |
| `be_year_conversion` | `bool` | `false` | Convert Buddhist Era years to Gregorian (Thailand, Cambodia, etc.) |
| `llm_override` | `str` \| `null` | `null` | Pin a specific model string; `null` uses the global 5-tier cascade |
| `translation_provider` | `"deepl"` \| `"google"` \| `null` | `null` | Force a translation provider; `null` uses DeepL with Google fallback |

### Derived (do NOT set in YAML)

| Property | Derived from | Values |
|----------|-------------|--------|
| `ocr_engine` | `script_type` (unless `ocr_engine_override` is set) | `latin` → `tesseract`, `asian` → `paddleocr` |

---

## Rules

- **Extra fields are forbidden.** Unknown keys raise `InvalidEconomyConfigError` immediately.
- **Portal list has no pillar tags.** Which portals are relevant for a given pillar is determined at runtime by the auto-probe (`src/crawler/probe.py`).
- **File name must be `{economy_name.lower()}.yaml`** so that `load_economy("Singapore")` resolves to `economies/singapore.yaml`.

---

## Add a New Economy in One File

Walk-through: adding Malaysia.

**Step 1** — Create `economies/malaysia.yaml`:

```yaml
economy_name: Malaysia
script_type: latin
languages: [ms, en]
portals:
  - name: Attorney General's Chambers
    url: https://agc.gov.my
  - name: MyUndang-Undang (Laws of Malaysia)
    url: https://www.lawnet.com.my
```

That's it. You don't set `ocr_engine` — it's derived as `tesseract` from `script_type: latin`.

**Step 2** — Verify it loads:

```bash
python - <<'EOF'
from src.config.economy_config import load_economy
cfg = load_economy("malaysia")
print(cfg.ocr_engine)   # tesseract
print(cfg.portals)
EOF
```

**Step 3** — Run the engine:

```bash
python main.py --economy Malaysia --pillar 7
```

No code changes required in any other module.

---

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `UnknownEconomyError: No economy config found for 'xyz'` | File `economies/xyz.yaml` doesn't exist | Create the YAML file |
| `InvalidEconomyConfigError: ... portals` | `portals` list is missing or empty | Add at least one portal entry |
| `InvalidEconomyConfigError: ... extra inputs are not permitted` | YAML contains an unrecognised field | Remove the unknown field; check the schema above |
| `InvalidEconomyConfigError: ... not a valid ISO 639 language code` | Language code is not 2–3 lowercase letters | Use codes like `en`, `ms`, `th`, `zh` |
