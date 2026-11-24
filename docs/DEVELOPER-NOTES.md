# Resume Screener

A two-layer, jurisdiction-aware resume screening engine with a **FastAPI** API and
a **React** review UI (Acme design system). **Decision-support only — it never
auto-rejects or auto-hires**; a human records every decision, with the evidence in
front of them. Deterministic Layer 1 (rubric coverage) + bounded, evidence-quoted
Layer 2 adjustments + a bias critic; per-jurisdiction policy packs decide what may
be scored. Pilot: India, Finance & Sales roles.

> **Standing constraint:** until the post-PoC **HR + Legal review**, all outputs are
> for **internal evaluation only** — never used to progress or reject candidates on
> live requisitions. (The UI shows this banner until the sign-off is recorded.)

Build spec: [CLAUDE.md](CLAUDE.md) · rationale: [ARCHITECTURE-TARGET-DESIGN.md](docs/ARCHITECTURE-TARGET-DESIGN.md)

## Quickstart — local

Prereqs: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 20+, Azure CLI, access
to the Azure resources in [CLAUDE.md §0](CLAUDE.md).

```bash
uv sync && cd web && npm install && npm run build && cd ..
cp .env.example .env         # fill in the two TODO deployment names
az login                     # runtime auth via DefaultAzureCredential
uv run screener check        # 5-point doctor — all five must show ✓
uv run screener ui           # http://127.0.0.1:8000
```

Your Azure identity needs the **Key Vault Secrets User** role on `ReesumeScreenerKV`
(really spelled with the double "e"). API keys are fetched at runtime and held in
memory only — **never** in `.env`, code, logs, or Git. Frontend dev loop:
`uv run screener ui --reload` + `cd web && npm run dev` (Vite on :5173).

## Quickstart — hosted (Azure Container Apps)

The same app runs on Azure Container Apps behind the company **Entra ID login**
(Easy Auth): URL `https://⟨resume-screener⟩.⟨region⟩.azurecontainerapps.io` — ask IT
for the current link and for a **role assignment** (below); without one you'll see
an "access not configured" page. Deployment (admin, ~45 min): Addendum 08 Part B —
`az containerapp up --source .` builds the [Dockerfile](Dockerfile) (uvicorn on
**8000**), [containerapp.patch.yaml](containerapp.patch.yaml) mounts the Azure Files
share at `/data` (`DATA_ROOT`), Managed Identity reads Key Vault, and the portal
wizard adds authentication. State survives restarts on the share; the container is
disposable.

## CLI

```bash
uv run screener check            # environment doctor (incl. optional DB probe)
uv run screener rubric --jd jds/<role>.md          # draft a checklist for human review
uv run screener run --jd jds/<role>.md --out output/<role> [--jurisdiction IN] [--limit 10]
uv run screener explain --role output/<role> --candidate <id>   # walk one candidate
uv run screener ui [--port 8000] [--host 0.0.0.0]  # serve API + built React UI
uv run screener db upgrade|import|seed             # Postgres setup (optional, see below)
```

## The review UI

The app is **requisition-centric**: a **Requisitions** list (each a real req with an
iCIMS Req ID, place, and lifecycle status) opens a **per-requisition workspace**.

- **Requisition workspace (all recruiter roles):** tabs **Overview · Checklist ·
  Candidates · Review · Next steps** — the **Overview** carries the full job
  description; the **Checklist** is the design's two-column editor (one card per
  requirement with a Nice-to-have/Must-have/Hard-gate pill, "Check & add" runs the
  bias critic before a row lands, flagged publishes route to HR-Head approval in
  one click, an Activity + publish rail on the right; drafts autosave locally,
  rows undo); manage the candidate batch (drag-drop/import with dedupe, one ✨
  button, live progress — screening stays disabled with a plain reason until an
  approved checklist exists), then the ranked review (evidence quotes,
  blind-review toggle, fairness-suspended queue, **optimistic** batch decisions
  with required reasons → feedback log, a **resizable** detail pane, a rich CSV
  export, **Ask Claude** redacted context bundle). Scores answer for themselves:
  hover for the one-line tip, click for the full "How this score adds up" math.
- **My preferences / Approvals:** user-scoped soft preferences, each run through the
  fairness check (governed phrase lists, folded for spelling, plus an AI intent check
  that can only make it stricter) and signed off by an HR Head — never its own author —
  before it applies as a bounded nudge (the cap and re-review period come from the
  governed scoring model). The unified **Approvals** page also
  queues every configuration change awaiting a second leader.
- **Governance pages (admin roles):** **Scoring model** (the design's four tabs —
  How it works · The score · Bias critic · Versions — over ONE governed artifact
  that now carries the bands, caps, credits, the 12 AI factors and the never-score
  topics with their trigger phrases; a sticky formula strip, staged drafts, the
  live "Watch it work" calculator computed by the real engine, and a version rail)
  + **Jurisdictions** — every scoring-affecting
  edit becomes a **change proposal a second leader approves** before it applies ·
  **Access & Roles** (the matrix below) · **Feature flags** (Super Admin) · **Audit
  & History** (searchable audit explorer: feed, candidate timelines, rules log with
  proposer → approver, retention queue, CSV reports) · **Data** (storage map +
  typed-confirm cleanup). A sidebar **region picker** sets an access lens. Scores
  produced under older published rules surface everywhere as **stale** (icons,
  banners, a paused decision gate) until a one-click re-screen refreshes them.

## Access (RBAC)

Roles come from **Entra app role assignments** when hosted (local dev: `DEV_ROLE`,
a comma list for multi-role, default HRHead). Mapping lives in [rbac.yaml](rbac.yaml);
a user may hold several roles and the effective grant is the **union**. The API
enforces it on every route. Seven roles: HR Head, Legal, IT, TA Specialist,
HR Specialist, Hiring Manager, Super Admin.

| Capability | HR Head | Legal | IT | TA Specialist | HR Specialist | Hiring Manager | Super Admin |
|---|---|---|---|---|---|---|---|
| Screen & review (decisions) | ✓ | — | ✓ | ✓ | ✓ | — | — |
| Own screening preferences | ✓ | — | ✓ | ✓ | ✓ | ✓ | — |
| Approve preferences | ✓ | — | — | — | — | — | — |
| Rules & factors | propose | propose | view | — | — | — | propose |
| Scoring model (policy knobs) | propose | propose | — | — | — | — | propose |
| Scoring model (structural) | — | — | — | — | — | — | propose |
| Approve configuration changes² | ✓ | ✓ | — | — | — | — | ✓ |
| Runs & audit | ✓ | ✓ | ✓ | — | — | — | — |
| Data management | ✓ | — | ✓ | — | — | — | — |
| Hiring-manager takes¹ | — | — | — | — | — | ✓ | — |
| Feature flags | view | — | view | — | — | — | edit |

¹ The hiring-manager portal is dark-launched behind `FEATURE_HM_PORTAL`.
² Never one's own proposal — the second-leader rule is enforced server-side.

## Where data lives

Everything mutable sits under `DATA_ROOT` (default: the repo; hosted: the Azure
Files share): `jds/` (JDs + checklists), `data/roles/<slug>/resumes/` (per-role
batches), `output/` (artifacts, reports, decisions, the private candidate map),
`signals/` + `policies/` (rules + packs + audit log), `.cache/`. Storage map:
[Addendum 04](docs/04-UI-SIMPLIFICATION-AND-STORAGE.md). Optionally, the structured data
(decisions, audit, runs, jobs, config-as-data, regions) moves to **PostgreSQL** via
per-domain `*_BACKEND=db` switches — zero-DB defaults, identical contracts, and
passwordless Entra auth in prod: [10-DATA-MODEL.md](docs/10-DATA-MODEL.md).

```bash
docker compose up -d db && uv run screener db upgrade && uv run screener db import && uv run screener db seed
```

## Testing

```bash
uv run pytest             # ~480 Python tests, fully offline — no Azure, no DB server
cd web && npm run build   # type-checks + builds the frontend
cd web && npm run test    # ~77 React component/page tests (Vitest)
```

Covers Layer-1 replayability, cap clamping, floor protection, policy merges, critic
suspension, redaction, governance rails, RBAC + the SSO principal parser, DATA_ROOT
bootstrap, and the DB layer (SQLite by default; set `TEST_DATABASE_URL` for a real
Postgres pass).

## Docs index

- [06-HR-TEAM-EXPLAINER.md](docs/06-HR-TEAM-EXPLAINER.md) — share with HR testers
- [07-TECH-EXPLAINER-AND-RUNBOOK.md](docs/07-TECH-EXPLAINER-AND-RUNBOOK.md) — operations + **§Troubleshooting**
- [08-HOSTING-SSO-RBAC.md](docs/08-HOSTING-SSO-RBAC.md) — hosting/SSO plan + admin steps
- [09-REACT-FASTAPI-UI.md](docs/09-REACT-FASTAPI-UI.md) / [10-DATA-MODEL.md](docs/10-DATA-MODEL.md) — UI + database reference
- [11-V2-REQUISITION-WORKSPACE.md](docs/11-V2-REQUISITION-WORKSPACE.md) — the v2 requisition workspace program
- [ARCHITECTURE-TARGET-DESIGN.md](docs/ARCHITECTURE-TARGET-DESIGN.md) — why it's built this way

Production phase (§12, not built here): real ATS adapters (iCIMS first), residence
policy union, EEO/four-fifths monitoring, the governed learning loop, the Azure Blob
move for large files, Indian-region processing.
