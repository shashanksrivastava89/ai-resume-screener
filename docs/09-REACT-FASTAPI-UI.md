# ADDENDUM 09 — React + FastAPI Review UI (the off-plan work)

**Status: shipped on branch `feature/react-fastapi-ui` (commits after the P0–P25 initial commit). Read this before touching the UI or the API.**

The build plan in `02-PROMPT-LIBRARY.md` and addenda 04/05/08 ends at **P25** with a **Streamlit** app (`ui_app.py` + `ui_theme.py`). Everything in this addendum happened **after** P25 and is **outside** the numbered plan: the review UI was lifted off Streamlit and rebuilt as a **FastAPI + React (Vite + TypeScript)** app on the vendored Acme design system. The scoring **engine did not change** — this is the §12 production seam (FastAPI + a real web UI) brought forward. `CLAUDE.md` already carries the one-paragraph architecture-update banner; **this file is the detail** so future sessions extend the React app instead of rebuilding it.

> **The one rule that survived intact:** the Python engine (extraction, redaction, policy, Layer 1/2, critic, scoring, registry, governance, RBAC, feedback) is the single source of truth. The API only **reads** the artifacts a `screener run` wrote and **shells out** to that same tested command to screen. No scoring logic lives in `api/` or `web/`. Guardrails §6, the copy rules (Addendum 04 §B3), and the data contracts (§4) all still hold verbatim.

---

## Part A — What changed, at a glance

| Before (P0–P25) | After (this branch) |
|---|---|
| `src/resume_screener/ui_app.py` (1,910 lines) + `ui_theme.py` — Streamlit | **deleted**; replaced by `src/resume_screener/api/` (FastAPI) + `web/` (React SPA) |
| `streamlit` dependency | **dropped**; added `fastapi`, `uvicorn`, `python-multipart` |
| `screener ui` → `streamlit run …` | `screener ui` → `uvicorn` serving the API **and** the built React app (`web/dist`) on port **8000** |
| Two-mode UI via a sidebar "Recruiter \| Admin" toggle | Left sidebar: **Roles · Screen · Review** always; a **Governance** group (Rules & Signals · Jurisdictions · Access & Roles · Runs & Audit) shown only when `has_admin` |
| Streamlit theming (`config.toml` + injected CSS) | **Vendored** Acme design system under `web/design-system/`; app imports its token CSS directly |
| `.streamlit/config.toml` drove the theme | Still present but **no longer authoritative** for the web app (leftover from the Streamlit era) |

**Terminology carry-over (so the older docs still read correctly):** where addenda 04/05/08 say *Streamlit*, *`ui_app.py`*, *`ui_theme.py`*, or *"Admin mode toggle"*, read them as the React app and its **Governance** nav group. The **copy dictionary** (Addendum 04 §B3) and the **Acme tokens** (Addendum 05 Part C) are unchanged and still authoritative.

---

## Part B — Backend: the FastAPI layer (`src/resume_screener/api/`)

Four modules, deliberately thin:

| Module | Responsibility |
|---|---|
| `app.py` | The FastAPI app + **all routes** (~26). Reads never touch Azure; the two model-backed actions (rubric draft, screening) fail with a plain 503 message when Azure is unreachable, exactly like the CLI doctor. Also mounts the built SPA (`web/dist`) with a catch-all fallback for client-side routing. |
| `service.py` | Engine **glue**: maps on-disk artifacts → the plain dicts React renders; computes the six-stage **role lifecycle** from disk (no new state files); the sanctioned runtime read of the private candidate map; jurisdiction display labels. **No scoring here.** |
| `review.py` | Review **view builders** — the ranked-list cards and the detail-drawer shape, in recruiter language only (no layer numbers, signal ids, weights, or hashes leak out). |
| `jobs.py` | Screening **runner**: shells out to the exact, tested `screener run` subprocess in a background thread, parses `N/M` progress lines, exposes `{status, done, total, message, error}`. Jobs live **in memory** (single-process PoC); a run is resumable from cache after a restart. |

### Route map (grouped as in `app.py`)

- **Meta:** `GET /api/me` (identity, capabilities, `has_admin`, `can_edit_rules`, `evaluation_mode`, `review_pending`, `last_review`) · `GET /api/jurisdictions` (effective *and* overlay-own fields, for the editor) · `GET /api/rbac`
- **Roles:** `GET /api/roles` (cards + dashboard metrics) · `GET/POST /api/roles[/{id}]` · rubric: `GET /…/rubric`, `POST /…/rubric/draft` (✨ AI), `PUT /…/rubric` · batch: `GET /…/batch`, `POST /…/batch/upload`, `POST /…/batch/import`, `DELETE /…/batch/{file}`, `DELETE /…/batch` · screening: `POST /…/screen` (✨ AI), `GET /api/screen/jobs/{job_id}`
- **Review:** `GET /…/review` · `GET /…/review/{candidate_id}` · `POST /…/decisions` · `GET /…/candidates/{cid}/resume` (original file via the private map; **never** in blind mode)
- **Governance:** `GET/PUT /api/signals` · `PUT /api/jurisdictions/{code}` · `POST /api/review-signoff` · `GET /api/audit-log`
- **Runs:** `GET /api/runs` · `GET /…/audit` (plain-text audit pack)

### Guardrails preserved in the API (do not regress)

- **Decisions are the same contract** as the old review UI: `forward=agree`, `discuss=rerank`, `not=override`; the recruiter action is written as a **reason prefix** so the raw action survives; `action=clear` records a `"Cleared …"` line and `role_decisions()` treats it as undecided (append-only log, never rewritten).
- **RBAC gates writes:** `PUT /api/signals`, `PUT /api/jurisdictions/{code}`, and `POST /api/review-signoff` all require `RULES_EDIT` (HR Head) and 403 otherwise; publishing runs floor protection + version bump + `audit_log.jsonl` append via `governance.py`.
- **Path safety:** batch delete uses the basename only and asserts the parent resolves to the role folder (no traversal).
- **Stale-data reconciliation** (see Part D) lives in `service`/`review` — a run's stored output is always filtered against the résumés **currently attached** to the role.

---

## Part C — Frontend: the React app (`web/`)

Vite + React 18 + TypeScript, **React Router**, no state library (a tiny `MeContext` + fetch hooks in `hooks.ts` / `api.ts`). Dependencies are intentionally minimal (`react`, `react-dom`, `react-router-dom`).

```
web/
  index.html  vite.config.ts  tsconfig.json  package.json
  public/                       acme-logo(.svg / -white.svg)
  design-system/                VENDORED Acme DS (see below) — referenced, not reinvented
    tokens/{base,colors,spacing,typography}.css   styles.css
    _ds_bundle.js  _ds_manifest.json  Component Library.dc.html  readme.md
  src/
    main.tsx                    imports the vendored DS token CSS directly
    App.tsx                     routes (see below)
    MeContext.tsx               /api/me + refreshMe() (banners update live)
    api.ts  hooks.ts            fetch helpers
    types.ts                    all API response types
    stages.ts                   the six lifecycle stages (mirrors service.STAGES)
    layout/AppShell.tsx         sidebar + eval-mode / pending-review banners + Page wrapper
    components/  ui.tsx (DS-matched primitives) · icons.tsx · RolePicker · ChecklistEditor
    styles/  tokens.css (DS tokens VERBATIM) · app.css
    pages/  Roles · RoleDetail · NewRole · Checklist · Screen · Review · ReviewDrawer
            · NextSteps · Rules · Jurisdictions · Access · RunsAudit
```

### Routes (`App.tsx`)

`/roles` · `/roles/new` · `/roles/:reqId` · `/roles/:reqId/checklist` · `/screen[/:reqId]` · `/review[/:reqId]` · `/review/:reqId/next-steps` · `/rules` · `/jurisdictions` · `/access` · `/runs`. Unknown → `/roles`. The four `/rules`,`/jurisdictions`,`/access`,`/runs` pages are only linked from the sidebar when `has_admin`, but are **not** route-guarded on the client — the API enforces RBAC on writes.

### Design system — vendored and referenced (not reimplemented)

`web/design-system/` is copied **as-is** from the Claude Design project "Atomic design project improvement" so the app references the exact brand tokens/components. The app consumes `tokens/*.css` directly; `src/styles/tokens.css` carries those DS tokens **verbatim** (colours, full type scale, spacing, radii, shadows incl. `--shadow-focus`, weights, durations, ease). `src/components/ui.tsx` mirrors the DS's compiled `.br-*` components (`Button`, `Badge`, `Card`, `Stat`, `Callout`, `Input`, `Select`, `Checkbox`, `Radio`, `Switch`, `Tabs`) **value-for-value**. Brand rules that are **locked**: Arial only; closed green-anchored palette (`#00A651` brand, `#008943` deeper/hover); green never used for body text; rounded corners (cards 12–24px, buttons/logo full pill); soft neutral (never coloured) shadows. Full token table is Addendum 05 Part C.

**✨ Magic actions** (Addendum 05 Part B4) carry into the React app: buttons that trigger AI/LLM/DI cost — **Create screening checklist** and **Screen résumés** — get the ✨ + green-gradient treatment; everything else is standard.

---

## Part D — Behavioural / data-integrity refinements (from testing the running app)

These landed in the polish commits and are easy to accidentally undo — keep them:

1. **Stale-data reconciliation.** A stored run can outlive the résumés it scored (batch cleared/changed). Both the Roles stage and Review filter candidate records to those whose content-hash id is **still attached** to the role (`service.batch_candidate_ids`); counts/bands/stage recompute from the attached set, **not** from `run_meta`. A run whose résumés are all gone drops back to a pre-screen stage.
2. **Role titles come from the rubric.** `service.display_title()` prefers the rubric's `role_title` over the JD-file slug, so cards never read `sales-manager-ii-lsg`. The requisition id is still shown to disambiguate same-titled roles.
3. **Decision toggle-off.** Clicking the already-active decision (Move forward / Discuss / Not a fit) clears it back to undecided via `action=clear`.
4. **Résumé download.** `GET /…/candidates/{cid}/resume` serves the original file from the private map; the drawer shows a "Download résumé" link — **hidden with a note during blind review** (revealing the file would defeat blinding).
5. **Review page fidelity** (1:1 with the mockup): per-row multi-select + bulk-decision bar; band-filter pills (All / Strong / Possible / Weak) + sort; decision-status tabs (All / Awaiting / Move forward / Discuss / Not a fit) with counts, AND-combined; chip colours (met = green, gap = neutral) with "Only partly shown:" / "Missing:" prefixes; band labels **Strong / Possible / Weak match** (note: `Not a fit` renders as "Weak match"); drawer sections start partially collapsed with "Show all N".
6. **Jurisdiction editor separates own vs effective.** `GET /api/jurisdictions` returns the overlay's **own** obligations/retention/monitoring (`own_*` + `version`) alongside the merged effective view, so editing writes only the overlay and never duplicates base-floor obligations into it.
7. **Review sign-off** flips `review_pending` off; any later rule/policy publish reopens it; `MeContext.refreshMe()` updates the shell banners immediately.

---

## Part E — Run · dev · build · test

```bash
# One process (production-style): build the frontend, then serve API + UI together
cd web && npm run build && cd ..
uv run screener ui                 # http://127.0.0.1:8000

# Two processes (development): API + Vite dev server with hot reload
uv run screener ui --reload        # API on :8000
cd web && npm run dev              # UI on :5173, proxies /api → :8000 (CORS allows :5173)

# Tests
uv run pytest                      # ~120 Python tests, fully offline
cd web && npm run build            # type-checks (tsc) + builds the frontend — the only FE gate today
```

The CLI `ui` command (`cli.py`) runs `uvicorn resume_screener.api.app:app` on `127.0.0.1:8000`; `--port` and `--reload` are options. The engine's CLI (`check`, `rubric`, `run`, `explain`) is unchanged.

---

## Part F — Open items / not-yet-migrated (context for the next session)

Know these before planning further work — they are the honest gaps left by an off-plan migration:

> **DB backend landed (post-PoC, 2026-07-10):** a PostgreSQL + SQLAlchemy layer now backs the structured/mutable data (decisions, audit, run index, jobs, config-as-data) + a region/identity model, behind per-domain config switches (defaults keep the flat-file behavior). Two Part-F items below are resolved by it (jobs; the region base for RBAC enforcement). See **[10-DATA-MODEL.md](10-DATA-MODEL.md)**.

- **~~The Streamlit-era Admin "Data" page (P16) was NOT ported.~~ — RESOLVED (P47, 2026-07-13).** The engine deletion logic moved to `datamgmt.py` (`delete_cache` / `delete_candidate_data` — now also clearing the per-role batches + stray `.lock` sidecars — / `storage_map`); three `data_manage`-gated routes (`GET /api/data/summary`, `POST /api/data/delete-cache`, `POST /api/data/delete-candidates`, each requiring a typed confirm phrase and doubly recording a `data_deleted` event + a `data_delete` governance audit entry) back a **Governance → Data** page (`web/src/pages/DataPage.tsx` at `/data`): the §A1 storage map + two typed-confirm danger cards, keeping `output/feedback` as the audit trail.
- **~~Hosting/SSO/RBAC-enforcement (P26–P28) predate this migration and assume Streamlit~~ — RESOLVED (2026-07-11).** P26–P28 shipped against this app exactly as prescribed here: the `Dockerfile` builds `web/dist` in a node stage and runs `uvicorn` on **8000** (`containerapp.patch.yaml` pins target port 8000 + the `/data` Azure Files mount = `DATA_ROOT`); `rbac.get_identity(headers)` parses `X-MS-CLIENT-PRINCIPAL`; RBAC is enforced server-side on **every** `/api` route (capability dependencies, friendly 403s) and the client both hides Governance nav and guards its routes (`RequireAdmin`), with an "access not configured" page for role-less users. *Still open from the region framework: the SSO region-picker, per-region page filtering, feature-flag gating and the HM portal (10-DATA-MODEL §6's deferred list).*
- **No automated frontend tests.** `npm run build` (type-check) is the only gate; there's no Vitest/Playwright. `streamlit.testing` coverage from the Streamlit era is gone.
- **~~Screening jobs are in-memory~~ — RESOLVED (Phase 5).** `jobs.py` now has a `JOBS_BACKEND=db` switch persisting to `screening_job` (survives restart, worker-ready); the default stays in-memory for zero-infra dev. Same `{status, done, total}` interface. A real queue + workers is still the production endpoint, now behind a DB-backed job store.
- **`.streamlit/config.toml` is dead weight** for the web app now — safe to ignore/remove once nothing references it.
- **`docs index` / `00-START-HERE.md` still describe the Streamlit PoC** narratively; they're historical build-order records, left as-is on purpose. This addendum + `CLAUDE.md`'s banner + `README.md` are the current truth.

---

## Part G — Prompting guidance for further changes

Use these in the house style (paste into the coding agent, one at a time). They assume this addendum + `CLAUDE.md` as the spec.

### Adding or changing an API route
```
Add/modify a route in src/resume_screener/api/app.py per Addendum 09. Rules:
- Reads = file I/O over output/<role>/ artifacts via service.py; NEVER call Azure on a read
  and NEVER put scoring logic in api/ (Layer 1/2, critic, scoring stay in their modules).
- If the action costs a model/DI call, mirror the CLI doctor: fail with HTTPException(503, plain
  message) when Azure is unreachable, and mark the frontend button ✨ (Addendum 05 B4).
- Any write to registry/policy/decisions/audit goes through governance.py / feedback.py and
  requires the right RBAC capability (rbac.can(role, …)) → 403 otherwise.
- Return recruiter-language dicts only: no signal ids, layer numbers, weights or candidate hashes
  (Addendum 04 §B3). Add/adjust the matching type in web/src/types.ts.
Run `uv run pytest` and `cd web && npm run build`; both must stay clean. Stop.
```

### Adding or changing a React page/component
```
Work in web/ per Addendum 09. Constraints:
- Reuse the DS-matched primitives in web/src/components/ui.tsx and tokens in styles/tokens.css;
  do NOT introduce off-palette colours, non-Arial fonts, or new component libraries. Green is for
  emphasis/brand only — never body text.
- Wire to the real API (api.ts/hooks.ts) — no mock data. Gate admin-only nav/pages behind
  me.has_admin / me.can_edit_rules from MeContext.
- Keep recruiter copy rules (Addendum 04 §B3) and the ✨ treatment for AI-triggering buttons.
- Preserve the Part D refinements (stale-data reconciliation, decision toggle/clear, blind-mode
  résumé hiding, band-filter/tabs, own-vs-effective jurisdiction editing).
Then `cd web && npm run build` must pass (it's the only FE gate). Stop.
```

### Porting the Admin "Data" page (the P16 gap)
```
Add a Governance → Data page in the React app (was Addendum 04/P16, never migrated). Mirror the
storage map (Addendum 04 §A1) and add "Delete cache" and "Delete all candidate data" behind
confirm dialogs, reusing the existing engine-side deletion logic via new RBAC-gated API routes
(DATA_MANAGE capability). Recruiter-plain copy; one sentence of consequences per action. Update
this addendum's Part F to remove the gap. Stop.
```

### Doing P26–P28 (hosting / SSO / RBAC) against React (not Streamlit)
```
Implement P26–P28 (Addendum 08) but for the FastAPI+React app, per Addendum 09 Part F:
- Dockerfile: build web/dist (node stage) + uv deps; CMD = uvicorn on 0.0.0.0:8000; EXPOSE 8000;
  containerapp target-port 8000 (NOT 8501). DATA_ROOT bootstrap unchanged.
- Easy Auth: parse X-MS-CLIENT-PRINCIPAL in get_current_user()/rbac.get_identity(); Decisions and
  audit entries record the real email; local dev still uses DEV_USER/DEV_ROLE.
- Enforce rbac.yaml on admin ROUTES server-side AND hide/guard admin nav+pages on the client.
- Update README + Addendum 08's Streamlit-specific wording (port, CMD) to match.
Add tests for the principal parser and the permission matrix. Stop.
```

---

**Bottom line:** the engine and every guardrail are unchanged; only the presentation moved from Streamlit to a FastAPI-served React app on the vendored Acme design system. Extend `api/` (thin, read-only-except-governed-writes) and `web/` (DS primitives, real API, recruiter copy) — and close the Part F gaps rather than reintroducing Streamlit.
