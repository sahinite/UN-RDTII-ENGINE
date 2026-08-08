# PRD — Google Sign-In & Multi-Tenant Access for Gradio UI

**Status:** ready-for-agent
**Owner:** MHarisMumtaz
**Target branch:** `phase-2`

---

## Problem Statement

The RDTII Extraction Engine Gradio UI is currently a single-tenant, unauthenticated app. Anyone with the URL can run pipelines, view every past run's outputs, and edit the shared `.env` API keys. This blocks a public/VPS deployment because:

- API keys cost money — a stranger clicking Run drains the operator's LLM credits.
- All users share one set of provider keys, so cost attribution and per-user provider preference are impossible.
- Pipeline runs are compute-heavy — an unrestricted URL is a trivial DoS/brute-force target.
- Output files and logs mix across every user, exposing one user's regulatory work to another.

## Solution

Gate the entire Gradio UI behind Google Sign-In using Google Identity Services (GIS) with server-side `id_token` verification. Convert the app to multi-tenant:

- Each signed-in user has an isolated profile (email, name, contact), their own encrypted API keys, their own LLM provider selection, and their own `outputs/` and `logs/` directories.
- Regular users see Run, Results, and My Settings tabs. Admins (email in `ADMIN_EMAILS` env var) also see Configure.
- Every pipeline run is enqueued through a global SQLite-backed queue with `Semaphore(5)` concurrency and per-user rate limits, preventing brute-force abuse.
- Pipeline subprocesses receive the requesting user's decrypted keys via `subprocess.Popen(env=…)` — no `os.environ` mutation, no cross-user leakage.
- Custom HTML overlay modal (non-dismissible) appears on any gated action when the viewer is signed out.

## User Stories

### Sign-in & session

1. As an unauthenticated visitor, I want to see a non-dismissible modal with a Google Sign-In button and the tagline *"Law/Regulations and Provisions Extraction engine on UN RDTII Framework"* the moment I try any gated action, so I understand I need to sign in.
2. As an unauthenticated visitor, I want the Run and Results tabs visible but their actions gated, so I can preview the app before signing in.
3. As an unauthenticated visitor, I want the My Settings tab entirely hidden until I sign in, so the UI stays uncluttered.
4. As a signed-in user, I want the modal to close and my identity (name + picture) to appear in the header, so I know the session is active.
5. As a signed-in user, I want my session to survive a page reload without re-signing in, so the UX feels persistent.
6. As a signed-in user whose id_token has expired, I want a silent re-auth attempt via GIS `prompt: 'none'` before the modal returns, so the refresh is usually invisible.
7. As a signed-in user, I want a sign-out button in My Settings that clears my session immediately, so I can hand off the browser.
8. As a first-time signed-in user, I want to auto-land on My Settings with a toast prompting me to add an API key, so I know what to do next.

### API key management (My Settings)

9. As a signed-in user, I want to see my Google profile (name, email, picture) as read-only fields, so I confirm which account I'm using.
10. As a signed-in user, I want to edit my contact field, so administrators can reach me if needed.
11. As a signed-in user, I want to enter API keys for any of the 8 supported LLM providers (OpenAI, Anthropic, Gemini, DeepSeek, Groq, DashScope, plus two Ollama variants), so I can pick my provider.
12. As a signed-in user, I want API key inputs to be masked (password-style), so shoulder-surfers can't read them.
13. As a signed-in user, I want my API keys stored encrypted at rest (Fernet), so a filesystem breach doesn't leak keys in plaintext.
14. As a signed-in user, I want to select my active LLM provider from a dropdown, and have that selection persist across sessions.
15. As a signed-in user with no key stored for my selected provider, I want the Run button to remain clickable but open a popup linking me to My Settings when I click it, so I'm not silently blocked.

### Running pipelines (isolation + queue)

16. As a signed-in user, I want my pipeline runs to use *my* API keys, not another user's or a shared pool, so cost attribution is clean.
17. As a signed-in user, I want my pipeline outputs written to my private directory (`outputs/{user_hash}/`), so other users cannot see my results.
18. As a signed-in user, I want my logs written to `logs/{user_hash}/`, so log-scanning stays scoped to me.
19. As a signed-in user clicking Run while 5 global runs are in progress, I want to see *"Queued — position X of Y. Estimated start: ~M min"* updated every 5 seconds, so I know when to expect my run.
20. As a signed-in user, I want my run's ETA computed from the rolling average of the last 10 completed runs for the same (economy, pillar), so the estimate is realistic.
21. As a signed-in user with no historical samples for my (economy, pillar), I want to see "ETA unavailable" rather than a fabricated number, so I'm not misled.
22. As a signed-in user, I want to be capped at 1 running + 1 queued pipeline at a time; a third click shows a toast *"You already have 2 runs pending; cancel or wait."*
23. As a signed-in user, I want to be capped at 10 runs per rolling hour; exceeding this shows *"Rate limit — try again in X minutes."*
24. As a signed-in user, I want to close my browser tab while queued or running and still find my output in Results later, so I don't have to babysit long runs.
25. As a signed-in user, I want to cancel any of my own queued or running pipelines from the Results tab, so I can stop mistakes without waiting.
26. As a signed-in user, I want to only see *my own* runs in the Results tab, so I never accidentally load another user's data.

### Admin

27. As an admin (email in `ADMIN_EMAILS`), I want the Configure tab visible so I can edit economy YAMLs and pillar indicators through the UI.
28. As a non-admin user, I want the Configure tab entirely hidden, so I can't accidentally break the shared domain model.
29. As an operator, I want `ADMIN_EMAILS` in `.env` (comma-separated) to be the sole source of truth for admin status — no DB flag to toggle — so bootstrap has no chicken-and-egg problem.

### Developer / operator

30. As a developer, I want an `AUTH_DEV_BYPASS=1` env flag that skips Google OAuth and treats me as `DEV_USER_EMAIL`, so I can iterate locally without hitting Google.
31. As a developer, I want the app to print a loud red startup warning if `AUTH_DEV_BYPASS=1` is set while binding a non-localhost address, so I never accidentally deploy with bypass on.
32. As an operator, I want `data/users.db` schema initialized via `CREATE TABLE IF NOT EXISTS` on startup and future changes gated by `PRAGMA user_version`, so I don't need Alembic for 3–4 tables.
33. As an operator, I want the app to fail loud at startup if `SECRET_ENCRYPTION_KEY` is missing, so encrypted secrets are never silently stored in plaintext.
34. As an operator, I want existing pre-auth files in `outputs/` and `logs/` to be ignored by the UI (no migration), so I don't have to backfill.
35. As an operator, I want the CLI (`python main.py …`) to keep working unchanged (using shared `.env`), so command-line workflows are unaffected.
36. As an operator running on Hostinger VPS with nginx, I want the app to bind `127.0.0.1:7860` and rely on nginx + Let's Encrypt for TLS, so GIS accepts the origin.

## Implementation Decisions

### Authentication

- **Approach**: Google Identity Services (GIS) client-side button + server-side `id_token` verification via `google-auth`'s `verify_oauth2_token`. No FastAPI wrapper, no redirect flow, no callback URL.
- **Client ID**: `GOOGLE_OAUTH_CLIENT_ID` in `.env`; also passed into the GIS JS init.
- **Session storage**: `gr.State` per browser tab holds a `RunContext` dataclass (`email`, `name`, `picture_url`, `is_admin`, `user_hash`, `secrets` dict, `config` dict). Browser `localStorage` caches raw id_token so page reload re-verifies without user action; on expiry, GIS `prompt: 'none'` silent refresh is attempted first, then the modal returns.
- **Admin resolution**: `is_admin = email.lower() in [e.lower() for e in ADMIN_EMAILS.split(",")]`. Recomputed per session; never persisted.
- **No domain restriction** (any Google account may sign in).

### Modal

- **Custom HTML overlay** in a `gr.HTML` component with CSS `position:fixed`, semi-transparent backdrop, non-dismissible until sign-in succeeds.
- Content: tagline *"Law/Regulations and Provisions Extraction engine on UN RDTII Framework"* + GIS button. No "why sign in?" collapsible.
- Triggered by: `app.load` (if not signed in), any tab switch to a gated tab, any gated action handler (defense in depth).

### Storage schema (`data/users.db`, SQLite)

```
users
  email          TEXT PRIMARY KEY
  name           TEXT
  picture_url    TEXT
  contact        TEXT
  user_hash      TEXT INDEXED            -- sha256(email)[:16] hex, for FS paths
  created_at     TIMESTAMP
  updated_at     TIMESTAMP

user_secrets
  email          TEXT
  key_name       TEXT                    -- e.g. 'OPENAI_API_KEY'
  value_encrypted BLOB                   -- Fernet(value), key from SECRET_ENCRYPTION_KEY
  PRIMARY KEY (email, key_name)

user_configs
  email          TEXT PRIMARY KEY
  config_json    TEXT                    -- last-used economy, pillar, LLM_PROVIDER

runs
  run_id         TEXT PRIMARY KEY        -- uuid4
  email          TEXT INDEXED
  economy        TEXT
  pillar         INTEGER
  status         TEXT                    -- queued|running|completed|failed|cancelled
  enqueued_at    TIMESTAMP
  started_at     TIMESTAMP
  finished_at    TIMESTAMP
  pid            INTEGER
  output_dir     TEXT
  duration_s     REAL
```

- Schema initialized via `CREATE TABLE IF NOT EXISTS` in `init_db()` on app startup.
- Future migrations gated by `PRAGMA user_version` bumps + guarded `ALTER TABLE` blocks.

### Multi-tenant isolation via subprocess env

- The UI already spawns `main.py` as a subprocess (`UI/run_screen.py:69`). This makes per-user isolation trivial: build a fresh env dict per Run click.
- Env dict = parent env minus any provider keys + the requesting user's decrypted `user_secrets` + `RDTII_OUTPUT_DIR=outputs/{user_hash}/{run_id}` + `RDTII_LOG_DIR=logs/{user_hash}/{run_id}`.
- Parent process `os.environ` is never mutated → concurrent runs by different users have kernel-level isolation.
- `main.py` requires a small change to honor `RDTII_OUTPUT_DIR` / `RDTII_LOG_DIR` env vars, falling back to the current hardcoded `outputs/` / `logs/` when unset (CLI behavior preserved).

### Queue & scheduler

- **SQLite-backed FIFO queue** on the `runs` table. Background worker thread polls `WHERE status='queued' ORDER BY enqueued_at LIMIT 1`, acquires a `threading.Semaphore(MAX_CONCURRENT=5)`, marks `status='running'`, spawns the subprocess, releases semaphore on exit.
- **Streaming behavior**: `run_pipeline_streaming` is already a Python generator; add a "queued" phase that yields queue-status updates every 5 seconds until slot is granted, then transitions seamlessly to normal pipeline log stream.
- **On server restart**: all rows with `status='running'` are reset to `'queued'` in `init_db()` (their subprocesses died with the parent).
- **Cancellation**: owner-only. Queued → `UPDATE status='cancelled'`, worker skips when it comes up. Running → `os.kill(pid, SIGTERM)` → wait 5s → `SIGKILL` if still alive.
- **Tab close during queued/running**: subprocess continues; output appears in the user's Results on next load.

### Rate limits (per user)

- **Concurrent cap**: max 1 with `status='running'` AND max 1 with `status='queued'`. Third enqueue attempt rejected with toast.
- **Hourly cap**: sliding-window `COUNT(*) WHERE email=? AND enqueued_at > now()-1h < 10`. Exceeded → toast.
- Both caps enforced in the enqueue handler, before insert.

### ETA

- Rolling average of `duration_s` from the last 10 rows where `status='completed'` AND `(economy, pillar)` matches.
- `eta = (position_in_queue / MAX_CONCURRENT) * avg_duration + remaining_time_of_current_slot`.
- If <3 completed samples exist for that (economy, pillar): display "ETA unavailable" instead of a fake number.

### UI screen changes

- **Run screen**: unchanged UI, handler wrapped with `require_auth`. Streaming panel now shows queue-status lines before pipeline output. Run button always clickable; missing-key case shows popup on click.
- **Results screen**: `list_runs()` filtered to `outputs/{ctx.user_hash}/`. Cancel button added per row where `status IN (queued, running)`.
- **My Settings screen** (renames + replaces Environment screen): Profile section (read-only Google fields + editable contact + sign-out), API Keys section (one masked field per provider), LLM Provider section (dropdown persisted to `user_configs`).
- **Configure screen**: file unchanged; visibility toggled by `is_admin` in `app.py`.
- **Header**: shows user's name + picture when signed in; sign-in button when signed out.

### Security posture (acknowledged compromises)

- id_token stored in browser `localStorage` — XSS surface. Acceptable for hackathon; a `httpOnly` cookie would require the FastAPI wrapper we deferred.
- `SECRET_ENCRYPTION_KEY` in `.env` — anyone with filesystem access on the VPS can decrypt user secrets. Mitigated by `chmod 600 .env` and never committing. Not KMS-grade.
- No CSRF token — GIS returns id_token via JS callback verified server-side; no state-changing GET routes exist.

### Environment variables (`.env` additions)

- `GOOGLE_OAUTH_CLIENT_ID` — Google Cloud OAuth 2.0 Client ID (public).
- `SECRET_ENCRYPTION_KEY` — 32-byte urlsafe base64 (`openssl rand -base64 32`). Required; app fails loud if missing.
- `ADMIN_EMAILS` — comma-separated, case-insensitive.
- `AUTH_DEV_BYPASS` — `0`/`1`, defaults `0`.
- `DEV_USER_EMAIL` — used only when bypass is on.
- `MAX_CONCURRENT_RUNS` — default `5`.

### Dependencies (new)

- `google-auth` — id_token verification.
- `cryptography` — Fernet.

## Testing Decisions

**Testing philosophy**: test observable behavior at the highest possible seam. Unit-test pure helpers (crypto round-trip, ETA math, rate-limit predicates, subprocess env builder, hash function). Integration-test handlers via Gradio's test client with `AUTH_DEV_BYPASS=1`. Do not test private methods, do not assert on `gr.State` internals.

### Seams to test

| Seam | Test type | What it verifies |
|---|---|---|
| `verify_id_token(token) -> email\|None` | Unit + mocked `google-auth` | Valid token yields email; expired/wrong-audience token yields None |
| `encrypt(value) / decrypt(blob)` | Unit | Round-trip identity; missing `SECRET_ENCRYPTION_KEY` raises at import |
| `sha256_hash(email) -> str` | Unit | Deterministic 16-char hex; case-insensitive on email |
| `init_db()` on empty file | Unit | All 4 tables created; `PRAGMA user_version=1` |
| `init_db()` re-run | Unit | Idempotent; no duplicate tables; existing rows preserved |
| `init_db()` requeues orphaned running rows | Unit | All `status='running'` → `'queued'` on startup |
| `build_run_context(email)` after upsert | Integration (in-memory DB) | Returns RunContext with decrypted secrets, admin flag correct against fixture ADMIN_EMAILS |
| `build_subprocess_env(ctx)` | Unit | Contains user's decrypted keys; strips provider keys from parent env; contains RDTII_OUTPUT_DIR/LOG_DIR pointing under `{user_hash}` |
| `check_rate_limits(email, db) -> ok\|reason` | Unit against seeded DB | 1 running + 1 queued blocks 3rd; 10 in past hour blocks 11th |
| `compute_eta(economy, pillar, position, db)` | Unit against seeded DB | Returns None when <3 samples; correct arithmetic with samples |
| `enqueue(email, economy, pillar, db)` | Integration (in-memory DB) | Inserts row with status=queued; respects rate limits; returns run_id |
| Scheduler worker loop with 5 semaphore slots and 7 jobs | Integration (in-memory DB + mocked Popen) | Exactly 5 concurrent; remaining wait; FIFO order preserved |
| Cancel queued run | Integration | `status='cancelled'`; worker skips it |
| Cancel running run | Integration (mocked Popen with fake pid) | SIGTERM sent; status transitions |
| `require_auth` decorator | Unit | Missing ctx raises/returns modal signal; valid ctx invokes wrapped handler |
| `run_pipeline_streaming` via Gradio test client with bypass | Integration | Yields queue-status lines, then subprocess output; output lands in `outputs/{user_hash}/{run_id}/` |
| `list_runs(user_hash)` | Unit | Only returns rows/dirs under matching user_hash |
| Admin visibility toggling | Integration via Gradio test client | Configure tab hidden for non-admin, visible for admin fixture |

### Prior art

- `tests/test_economy_config.py` — Pydantic loader unit tests; pattern for stdlib + fixture-file unit tests.
- Other `tests/test_*.py` — pytest style, no async, no Gradio fixtures currently (we introduce Gradio test client).

## Parallelization Plan (for parallel subagent execution)

Grouped into workstreams with explicit dependency arrows. Streams within the same wave have **no shared file dependencies** and can be developed simultaneously by independent subagents.

### Wave 1 — Foundational primitives (fully parallel, no deps on each other)

| # | Stream | Deliverable | Files created | Independent because |
|---|---|---|---|---|
| **1A** | Crypto | `src/auth/crypto.py` + tests | `src/auth/crypto.py`, `tests/test_crypto.py` | Pure Fernet wrapper; only depends on `SECRET_ENCRYPTION_KEY` env var |
| **1B** | Hash util | `sha256_hash(email)` helper + tests | `src/auth/hash_util.py`, `tests/test_hash_util.py` | Pure stdlib; no deps |
| **1C** | DB schema | `src/auth/db.py` (init_db, connection helpers, migrations) + tests | `src/auth/db.py`, `tests/test_db.py` | Only stdlib `sqlite3`; schema is self-contained |
| **1D** | Google verify | `src/auth/google_oauth.py` (verify_id_token, mocked-google tests) | `src/auth/google_oauth.py`, `tests/test_google_oauth.py` | Depends only on `google-auth` and `GOOGLE_OAUTH_CLIENT_ID` |
| **1E** | Config additions | Update `.env.example`, `requirements.txt` | `.env.example`, `requirements.txt` | Standalone; no logic |
| **1F** | `main.py` env-var honor | Add `RDTII_OUTPUT_DIR`/`RDTII_LOG_DIR` respect | `main.py` (small patch) | Independent of auth work; touches an existing file |

### Wave 2 — Composition layer (each depends on ≥1 Wave-1 output)

| # | Stream | Deliverable | Depends on | Independent from |
|---|---|---|---|---|
| **2A** | RunContext + session | `src/auth/session.py` (RunContext dataclass, upsert_user, load_user_secrets, is_admin resolution) + tests | 1A, 1B, 1C, 1D | 2B, 2C, 2D |
| **2B** | Dev bypass | `src/auth/dev_bypass.py` + startup-warning integration | 1B | 2A, 2C, 2D |
| **2C** | Subprocess env builder | `src/queue/env_builder.py` (build_subprocess_env(ctx, run_id)) + tests | 1B | 2A, 2B, 2D |
| **2D** | Rate limit + ETA pure fns | `src/queue/rate_limit.py`, `src/queue/eta.py` + tests | 1C | 2A, 2B, 2C |

### Wave 3 — Queue engine

| # | Stream | Deliverable | Depends on |
|---|---|---|---|
| **3A** | Scheduler worker | `src/queue/scheduler.py` (Semaphore worker loop, enqueue, cancel, SIGTERM/SIGKILL) + tests with mocked Popen | 1C, 2C, 2D |

### Wave 4 — UI integration (each touches a different UI file; parallel-safe within wave)

| # | Stream | Deliverable | Depends on | Independent from |
|---|---|---|---|---|
| **4A** | Auth modal component | `UI/auth_modal.py` (HTML overlay, GIS JS init, verification wiring) | 2A, 1E | 4B, 4C, 4D, 4E |
| **4B** | Gate decorators | `UI/gate.py` (`require_auth`, `require_admin`) + tests | 2A, 2B | 4A, 4C, 4D, 4E |
| **4C** | Settings screen | `UI/settings_screen.py` (profile, keys, LLM provider); delete `UI/environment_screen.py` | 2A | 4A, 4B, 4D, 4E |
| **4D** | Results screen updates | Filter `list_runs()` to user_hash, add Cancel column | 2A, 3A | 4A, 4B, 4C, 4E |
| **4E** | Run screen updates | Wrap handler with `require_auth`, enqueue via 3A, stream queue-status lines, missing-key popup | 2A, 2C, 3A | 4A, 4B, 4C, 4D |

### Wave 5 — Final assembly (sequential; only one file)

| # | Stream | Deliverable | Depends on |
|---|---|---|---|
| **5A** | `UI/app.py` wiring | Mount modal, `gr.State(RunContext)`, tab-visibility toggles, `app.load` re-verify id_token from localStorage, admin-gated Configure tab | 4A, 4B, 4C, 4D, 4E |

### Wave 6 — Ops (parallel with Wave 5)

| # | Stream | Deliverable | Depends on |
|---|---|---|---|
| **6A** | Deployment docs | `docs/deploy/vps-setup.md` (nginx config, systemd unit, Certbot, `chmod 600 .env`, Google Cloud Console steps) | none |
| **6B** | End-to-end test | `tests/test_e2e_auth_flow.py` via Gradio test client with `AUTH_DEV_BYPASS=1` | 5A |

### Dependency graph summary

```
Wave 1 (parallel, no deps)
   ├─ 1A crypto ──────────────┐
   ├─ 1B hash ────────┬───────┼──────┐
   ├─ 1C db ──────────┼───────┼──────┼──┐
   ├─ 1D google ──────┼───────┤      │  │
   ├─ 1E env/deps ────┼───────┼──────┼──┘
   └─ 1F main.py ─── independent
                     ▼       ▼      ▼
Wave 2 (parallel among themselves)
   ├─ 2A session ────┐
   ├─ 2B bypass ─────┼─┐
   ├─ 2C env_builder ┤ ├─┐
   └─ 2D rate/eta ───┘ │ │
                       ▼ ▼
Wave 3
   └─ 3A scheduler
       │
       ▼
Wave 4 (parallel among themselves)
   ├─ 4A modal
   ├─ 4B gate
   ├─ 4C settings
   ├─ 4D results
   └─ 4E run
       │
       ▼
Wave 5
   └─ 5A app.py wiring     ── parallel with ──   6A docs

Wave 6
   └─ 6B e2e tests
```

**Practical guidance for spawning subagents**:

- Spawn 6 subagents in parallel for Wave 1 (each writes 1–2 files; no merge conflicts).
- After Wave 1 lands, spawn 4 subagents in parallel for Wave 2.
- Wave 3 is a single stream (one agent).
- Spawn 5 subagents in parallel for Wave 4 (each touches a different UI file).
- Wave 5 is a single stream; Wave 6A can run in parallel with any wave.

## Out of Scope

- Editing `economies/*.yaml` or `taxonomy.json` through the UI (developer-only; code path).
- Multi-region deployment or distributed queue (single VPS assumption).
- Password recovery, non-Google auth providers, or Google Workspace domain restriction.
- User-facing admin console (managing other users, quotas, revoking sessions) — `ADMIN_EMAILS` is the only admin surface.
- Migrating pre-auth `outputs/`/`logs/` files into per-user directories.
- Real KMS integration for `SECRET_ENCRYPTION_KEY` (AWS/GCP secret managers).
- CSRF tokens, `httpOnly` cookies (requires FastAPI wrapper).
- Cost-attribution dashboards, per-user billing.
- Automated tests for the GIS button rendering (browser-driven E2E is deferred).

## Further Notes

- The subprocess-env isolation approach is a happy accident of the existing `subprocess.Popen` invocation in `UI/run_screen.py:69`. It sidesteps the need for `contextvars`-based in-process isolation entirely and is materially safer (kernel-level).
- The `.env` API keys used by CLI (`python main.py …`) are **entirely separate** from user-stored keys. The CLI keeps working with shared `.env` credentials; the UI runs strictly per-user. This is intentional — do not add a fallback path from user context to shared `.env` (would leak credits across users).
- If HF Spaces or another ephemeral deploy target is later chosen, `data/users.db` persistence becomes a problem — the plan assumes durable disk (the Hostinger VPS provides this).
- The tagline text for the modal is fixed: *"Law/Regulations and Provisions Extraction engine on UN RDTII Framework"*.
