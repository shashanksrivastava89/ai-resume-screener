# Technical Explainer & Runbook — Resume Screener (for the project owner)

*Your reference for understanding the system well enough to explain, operate, and troubleshoot it. Pairs with `06-HR-TEAM-EXPLAINER.md` (the non-technical version), Addendum 04 (storage map), `09-REACT-FASTAPI-UI.md` (the current FastAPI + React UI — the review UI is no longer Streamlit), and `10-DATA-MODEL.md` (the optional PostgreSQL backend for structured data).*

---

## Part 1 — How it works (the 10-minute mental model)

### The pipeline, in order

Every "✨ Screen resumes" run does this, per candidate:

| # | Step | Module | Model call? | What it produces |
|---|---|---|---|---|
| 0 | Resolve policy | `policy.py` | No | Which judgment rules/obligations apply for this role's job location (floor + jurisdiction overlay) |
| 1 | Extract text | `ingest.py` → `localextract.py` (PDF/Word with their own text, read here) or **Document Intelligence** (scans and photos only, since 2026-10-14) | Only for scans (1st time per file; cached after) | Raw resume text + read quality, and where it was read (`run_meta.extraction`) |
| 2 | Redact | `redact.py` | No | Text with name/contact/age/gender/proxies removed |
| 3 | Parse profile | via Azure OpenAI | Yes (cached) | Structured `ResumeProfile` (skills, roles, education…) — **frozen to disk** |
| 4 | Check the checklist | `findings.py` | Yes — ONE call for all requirements | Per-requirement ✓/~/✗ with **verbatim quotes**, verified as real substrings — frozen |
| 5 | Compute features | `features.py`, `embed.py` | Embeddings only (cached) | Tenure stats, skill recency, domain-fit similarity — pure code, thresholds from the policy pack |
| 6 | **Layer 1** score | `layer1.py` | **No — pure code** | Eligibility (hard filters, must-have gate) + base score 0–100 from checklist weights |
| 7 | **Layer 2** judgment | `layer2.py` | Yes (gate-passers only) | Capped point adjustments, each = approved rule id + quote + rationale |
| 8 | Fairness critic | `critic.py` | Yes (gate-passers only) | pass / flag / **suspend** per adjustment; suspended ones removed from the score |
| 9 | Final + report | `scoring.py`, `report.py` | No | Band, xlsx/html/JSON, `run_meta.json` audit record |
| — | Human decision | UI → `feedback.py` | No | `decisions.jsonl` — suggestion vs. your decision + reason + all versions |

**Cost intuition:** ~5–6 model calls per *new* resume; everything is cached by content hash, so re-runs, checklist edits and calibration are near-free.

### The four ideas that make it defensible

1. **Frozen artifacts + pure Layer 1 = replayability.** The extracted profile and findings are saved to disk; the Layer-1 decision is plain code over those files. Any past result can be reproduced exactly — that's your audit answer.
2. **Bounded Layer 2 = governed judgment.** The agent may only apply rules from the approved registry, each capped (±points) with a total cap, evidence required, enforced *in code* — autonomy over process, never over criteria.
3. **Floor + dial policy engine.** A global floor (redaction, human decision, excluded topics, logging) that no setting can switch off, plus per-job-location packs that dial which rules apply, their parameters, and the local obligations.
4. **Versions everywhere.** Every run records model, prompt, checklist, registry and policy versions — so every decision is attributable to an exact configuration.

### Where everything lives

See Addendum 04 §A1 for the full table. Short version: secrets → Key Vault only; resumes → `data/roles/<role>/resumes/`; frozen artifacts + results + decisions → `output/`; checklists/rules/policies → versioned YAML; cache → `.cache/` (safe to delete). After hosting (Addendum 08), all mutable folders move under a mounted Azure Files share (`DATA_ROOT`), same layout.

**The database (optional, off by default).** The structured/mutable data — decisions, audit log, review sign-off, run index, screening jobs, and config-as-data (rules registry, policy packs, versioned checklists, JDs/requisitions) plus the region/access model — can each be flipped from files to **PostgreSQL** with a per-domain env switch (`FEEDBACK_BACKEND=db`, `GOVERNANCE_BACKEND=db`, `RUN_INDEX_BACKEND=db`, `JOBS_BACKEND=db`, `REGISTRY_BACKEND=db`, `POLICY_BACKEND=db`, `RUBRIC_BACKEND=db`, `REQUISITIONS_BACKEND=db`). **Every default stays file/memory**, so nothing changes until you opt in, and scoring behavior is identical either way. Large files (resumes, frozen artifacts, reports, cache) always stay on disk. Full reference: `10-DATA-MODEL.md`.

---

## Part 2 — Runbook

### Daily operation

| Task | How |
|---|---|
| Start the app (local) | build the UI once (`cd web && npm run build`), then `uv run screener ui` → open http://127.0.0.1:8000; stop with Ctrl+C. Dev with hot reload: `uv run screener ui --reload` + `cd web && npm run dev` (see Addendum 09) |
| Health check | `uv run screener check` → five ✓ (sign-in, Key Vault ×2 secrets, Document Intelligence, chat, embeddings) + an informational database probe (a "not reachable" there is fine unless you've switched backends to `db`) |
| Start the database (optional) | `docker compose up -d db` → `uv run screener db upgrade` (migrations) → `uv run screener db import` (one-time backfill of file history/config; safe to re-run) → `uv run screener db seed` (regions/features). Then flip the `*_BACKEND=db` switches you want in `.env` |
| New role | UI → Roles → New role → paste JD → ✨ Create screening checklist → edit → Approve |
| Add candidates | UI → Screen → upload files or Import from folder (copies + dedupes) |
| Screen | UI → ✨ Screen resumes (re-runs of already-screened files are free) |
| Explain one candidate (CLI) | `uv run screener explain --role output/<role> --candidate <id>` |
| Back up the audit trail | Copy `output/` (and `jds/`, `signals/`, `policies/`) to the corporate OneDrive folder. If any `*_BACKEND=db` is on, also dump the database (`docker exec resume-screener-db pg_dump -U screener screener > backup.sql`) |
| Clear cache (safe) | Admin → Data → Delete cache, or delete `.cache/` — only costs a re-run |
| End-of-PoC data deletion | Admin → Data → Delete all candidate data (honors the retention story) |

### Changing behavior safely (no code)

- **Checklist per role:** Roles page — edits create a new version automatically.
- **Judgment rules:** Admin → Rules (HR Head) — every publish bumps the registry version and is logged with author + rationale; the fairness floor can't be edited off.
- **Per-location settings:** Admin → Rules → policy pack for that jurisdiction (rule on/off, caps, parameters like tenure thresholds, obligations text).
- **Bands & caps:** `config.yaml` (`band_strong`, `band_possible`, `layer2_total_cap_points`).

Golden rule: behavior changes live in **YAML/config via the UI**, never by editing pipeline code. If a change needs code, it goes through Claude Code with CLAUDE.md as the spec.

The same rails apply whether a domain runs on files or the database: every publish runs floor protection, can't remove a base exclusion, bumps a version, and lands in the change log (`signals/audit_log.jsonl`, or the audit tables when `GOVERNANCE_BACKEND=db`). Switching a backend never changes what's allowed — only where the rows live.

### Update / redeploy

Local: `git pull` → `uv sync` → `uv run screener db upgrade` (only if you use the database — applies any new migrations; harmless to skip otherwise) → `uv run screener check` → start. Hosted (after Addendum 08): make changes → `az containerapp up --name resume-screener --resource-group GIT-Infra-DevOps-Dev-Resource --source .` (rebuilds and rolls the container; data persists on the share). Production database auth is **passwordless** — `DATABASE_URL` points at Azure PostgreSQL with no password and the app attaches a Managed-Identity token at connect time (no DB secret anywhere, same mandate as Key Vault).

### Troubleshooting quick table

| Symptom | Likely cause → fix |
|---|---|
| `screener check` ✗ on sign-in | Not logged in → `az login --tenant 00000000-0000-4000-8000-2200910f4d4b` (hosted: Managed Identity role missing) |
| ✗ on Key Vault | Missing **Key Vault Secrets User** role, or vault network rules → Cloud Admin |
| ✗ on chat/embeddings | Deployment name in `.env` doesn't match AI Foundry → fix name |
| 429 / throttling on big runs | Burst limits → runs auto-retry and resume from cache; durable fix = raise deployment TPM quota |
| A candidate looks mis-scored | Open their detail: ✗ rows show what wasn't *evidenced*; check the quote expanders before blaming the model |
| "Hard to read" banners everywhere | Scanned/photographed batch → expected; DI handled it, confidence is low — human-check those |
| UI shows stale data | Refresh the browser (the API reconciles each run against the role's current batch; see Addendum 09 Part D); rebuild `web/dist` if you changed frontend code; worst case restart the app |
| Blank page after deploy (hosted) | Volume not mounted / `DATA_ROOT` unset → see Addendum 08 checklist |
| `check` says "Database — not reachable" | Only matters if a `*_BACKEND=db` switch is on. Fix: `docker compose up -d db` then `uv run screener db upgrade`. With everything on the default file backends, ignore it |
| Switched a backend to `db` but history looks empty | The one-time backfill hasn't run → `uv run screener db import` (idempotent; carries `decisions.jsonl`, audit log, runs, registry/packs/checklists/JDs into rows) |
| DB errors after `git pull` | New migrations pending → `uv run screener db upgrade` |

### Escalation path

1. Copy the exact error + `run_meta.json` of the failing run → paste to Claude Code with "diagnose, fix minimally, per CLAUDE.md."
2. Azure resource problems (roles, quotas, networking) → Cloud Admin with the resource name from Addendum 03/08.
3. Behavior/fairness questions → the design doc (`ARCHITECTURE-TARGET-DESIGN.md`) is the rationale of record.

### Mini-glossary (for when the docs get jargony)

**Frozen artifact** — a saved snapshot (profile/findings) that decisions replay from. **Gate-passer** — met all hard filters; only these get Layer-2 judgment. **Registry** — the approved judgment-rules file. **Policy pack** — one jurisdiction's settings overlay. **Suspended** — an adjustment the fairness critic removed from the score. **run_meta** — the per-run audit record (versions, counts, cost, errors). **Backend switch** — a per-domain env var (`*_BACKEND=db`) that moves that domain's rows from files to PostgreSQL; the default is always the file/memory store and behavior is identical either way. **Migration** — a versioned schema change applied with `screener db upgrade`.
