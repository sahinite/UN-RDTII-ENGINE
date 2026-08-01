# Economy YAML schema

Create one lowercase file per economy, for example
`economies/singapore.yaml`. The loader is
`src/config/economy_config.py`; unknown fields and invalid option values fail
validation.

## Minimal example

```yaml
economy_name: Thailand
iso_code: TH
un_name: Thailand
script_type: asian                 # latin | asian
languages: [th, en]                # 2–3 lowercase letters per code
portals:
  - name: Official legislation portal
    url: https://example.gov.th
    type: primary                   # primary | secondary
    discovery: auto
    fetch: auto
be_year_conversion: true
```

Required fields are `economy_name`, `script_type`, `languages`, and at least
one `portals` entry. `iso_code` and `un_name` are technically optional but
should be included for production economies.

## Economy fields

| Field | Allowed value / default | Purpose |
|---|---|---|
| `economy_name` **(required)** | Text | Display name; normally matches the YAML filename in title case. |
| `iso_code` | 2–3 letters; default `""` | ISO-style code used for lookup and output. Stored uppercase. |
| `un_name` | Text; default `""` | Official UN name used in output. |
| `script_type` **(required)** | `latin` \| `asian` | Selects the default OCR engine. |
| `languages` **(required)** | Non-empty list of 2–3 lowercase letters | Document language codes, such as `[en]` or `[ms, en]`. |
| `portals` **(required)** | Non-empty list of portal objects | Sources used for discovery and document fetching. |
| `ocr_engine_override` | `null` \| `tesseract` \| `paddleocr` \| `azure` \| `mistral_ocr` | Overrides the script-based OCR choice. |
| `be_year_conversion` | `true` or `false`; default `false` | Converts Buddhist Era years to Gregorian years. |
| `llm_override` | `null` or model-name text; default `null` | Pins an economy-specific LLM; `null` uses global settings. |
| `translation_provider` | `null` \| `deepl` \| `google`; default `null` | Selects translation provider. |
| `seed_url_remap` | URL-to-URL mapping; default `{}` | Redirects stale seed or mirror URLs to authoritative sources. |

`ocr_engine` is derived and must not be added to YAML:

| `script_type` | Default OCR |
|---|---|
| `latin` | `tesseract` |
| `asian` | `paddleocr` |

`ocr_engine_override` takes precedence.

## Portal fields

Each item under `portals:` supports the following fields.

| Field | Allowed value / default | Purpose |
|---|---|---|
| `name` **(required)** | Text | Human-readable portal label. |
| `url` **(required)** | Valid HTTP(S) URL | Portal home or starting URL. |
| `type` | `primary` \| `secondary`; default `primary` | Identifies authoritative versus supporting sources; not a pillar tag. |
| `search_url_pattern` | `null` or text; default `null` | Optional search URL pattern. |
| `js_required` | `true` or `false`; default `false` | Marks a portal requiring JavaScript. |
| `playwright_wait_for` | `null` or CSS/XPath selector | Element to wait for before parsing. |
| `playwright_timeout_ms` | `null` or integer milliseconds | Browser timeout override. |
| `follow_pagination` | `true` or `false`; default `false` | Follows `Next` links in indexes. |
| `anti_bot` | `none` \| `header_spoof` \| `playwright_stealth`; default `none` | Request method for bot-protected portals. |
| `discovery` | `index` \| `api` \| `sitemap` \| `auto` \| `search` \| `search_js` \| `seed_only` \| `TBD`; default `auto` | How relevant instruments are found. |
| `fetch` | `pdf_endpoint` \| `api_versioned_pdf` \| `html` \| `html_wholedoc` \| `html_js` \| `pdf_link` \| `auto` \| `TBD`; default `TBD` | How complete document text is obtained. |
| `transport_fallback` | `null` or `playwright_stealth`; default `null` | Escalates blocked HTTP requests to stealth Playwright. |

### Strategy-specific portal fields

| Field | Allowed value / default | Use / purpose |
|---|---|---|
| `index_urls` | List of URLs; default `[]` | Browse pages for `discovery: index`. |
| `index_link_pattern` | List of text fragments; default `[]` | Link path/query fragments identifying instruments, e.g. `[/Act/, /SL/]`. Empty uses direct document links. |
| `sitemap_url` | `null` or URL | Sitemap for `discovery: sitemap`. |
| `pdf_view_suffix` | `null` or suffix text | URL suffix for `fetch: pdf_endpoint` or whole-document views, e.g. `?WholeDoc=1`. |
| `pdf_link_selector` | `null` or CSS selector | Fallback selector for `fetch: pdf_link`. |
| `api_base` | `null` or URL | API root for `discovery: api` or `fetch: api_versioned_pdf`. |
| `api_collection` | `null` or text | API collection, e.g. `Act`. |
| `pdf_path_suffix` | `null` or path text | PDF path for `fetch: api_versioned_pdf`, e.g. `text/original/pdf`. |

### Strategy choices

| Field | Value | Meaning |
|---|---|---|
| `discovery` | `index` | Browse an index page. |
|  | `api` | Query a legislation API. |
|  | `sitemap` | Read a sitemap, often for a JS site. |
|  | `auto` | Let the engine detect a best-effort route. |
|  | `search` / `search_js` | Use server-side / JavaScript search. |
|  | `seed_only` | Use only known seed URLs. |
|  | `TBD` | Skip active discovery. |
| `fetch` | `pdf_endpoint` | Add a suffix to reach a PDF. |
|  | `api_versioned_pdf` | Build a dated PDF URL from API data. |
|  | `html` / `html_wholedoc` / `html_js` | Fetch ordinary, whole-document, or JS-rendered HTML. |
|  | `pdf_link` | Resolve a PDF embedded or linked from the page. |
|  | `auto` | Let the router choose. |
|  | `TBD` | Leave fetching unconfigured. |

## Full example

```yaml
economy_name: Exampleland
iso_code: EX
un_name: Exampleland
script_type: latin
languages: [en, fr]
ocr_engine_override: null
be_year_conversion: false
llm_override: null
translation_provider: null
seed_url_remap:
  "https://old.example.org/law.pdf": "https://laws.example.gov/act/123"
portals:
  - name: Exampleland legislation portal
    url: https://laws.example.gov
    type: primary
    anti_bot: none
    discovery: index
    fetch: pdf_link
    index_urls: [https://laws.example.gov/acts]
    index_link_pattern: [/act/]
```

## Rules

- Do not add `ocr_engine`; it is derived.
- Do not add pillar tags to portals.
- Use `TBD` for an unverified strategy and `auto` for best-effort detection.
- Strategy-specific fields may be omitted when unused.
- YAML booleans are `true` and `false`; optional values may be omitted or set
  to `null`.
- Run `python main.py --economy Exampleland --pillar 7` after validation.
