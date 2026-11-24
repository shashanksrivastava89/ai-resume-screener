# 10 — Data Model (PostgreSQL backend)

> **Status:** the structured/mutable half of the target storage design
> ([04-UI-SIMPLIFICATION-AND-STORAGE.md §A3](04-UI-SIMPLIFICATION-AND-STORAGE.md)),
> brought forward. A PostgreSQL datastore + a SQLAlchemy data-access layer now sit
> **beneath the unchanged scoring engine**. Scoring behavior (Layer 1/2, critic,
> scoring, redaction, findings) did not change — only where persisted state lives.
>
> **Large files stay on disk** (raw résumés, frozen artifacts, per-candidate JSON,
> reports, `.cache/`, the private candidate map). As shipped, **P26** keeps them
> on the hosted Azure Files share (`DATA_ROOT=/data`); moving them to Azure Blob
> stays a **§12** item. This document is the reference for the schema, the
> integrity rules, the region model, and the production database-auth path.

---

## 1. Design principles

- **Postgres everywhere the data is structured/mutable.** Azure Database for
  PostgreSQL Flexible Server (prod), local Docker Postgres (dev). SQLAlchemy 2.0
  (sync) + Alembic + psycopg3.
- **Engine unchanged.** The DB is a *seam under* the engine. Modules that read/write
  persisted state (`feedback`, `governance`, `registry`, `policy`, `rubric`,
  `ats/local_folder`, `api/service`, `api/jobs`) each gained a **config-switched
  backend**; the scoring code did not move.
- **Contract-oriented repositories.** `src/resume_screener/db/repositories/` is the
  only place that touches ORM models. Every function takes a `Session` and
  accepts/returns the §4 Pydantic contracts (or plain dicts), so engine/API code
  stays ORM-unaware.
- **Integrity in the right layer.** Surrogate PKs, natural `UNIQUE` keys, FKs with
  explicit `ON DELETE`, and indexes live in the schema. Rules that must *evolve
  freely* — append-only decision/audit logs, the required-reason rule, locked base
  exclusions, floor protection — are enforced in the **repository/governance layer**,
  not as rigid DB `CHECK`/trigger constraints. There are **no DB `ENUM` types**
  (adding a value must never need a migration) — enum-like columns are plain strings
  validated in code.
- **Portable types.** String UUID PKs (`uuid4().hex`), `JSON` (not `JSONB`-specific),
  `BigInteger().with_variant(Integer, "sqlite")` for the append-only log ids — so the
  offline test suite can run on SQLite and the same code runs on Postgres.

---

## 2. Backend switches (per domain, default = zero infra)

Each domain flips independently via an env var, so the DB can be adopted
domain-by-domain. **Every default keeps the app running with no database at all.**
The contract (what callers see) is identical on either side of the switch.

| Env var | Default | `db` reads/writes | Introduced |
|---|---|---|---|
| `FEEDBACK_BACKEND` | `jsonl` | `decision` | P-Decisions |
| `GOVERNANCE_BACKEND` | `jsonl` | `audit_entry`, `review_signoff` (region-scoped) | P-Governance |
| `RUN_INDEX_BACKEND` | `file` | `run` (index; artifacts stay on disk) | P-Runs |
| `JOBS_BACKEND` | `memory` | `screening_job` (survives restart) | P-Jobs |
| `REGISTRY_BACKEND` | `file` | `signal`, `excluded_signal`, `registry_version` | P6a |
| `POLICY_BACKEND` | `file` | `policy_pack`, `policy_signal_override` | P6b |
| `RUBRIC_BACKEND` | `file` | `rubric`, `requirement`, `hard_filter` | P6c |
| `REQUISITIONS_BACKEND` | `file` | `jd_document`, `requisition` | P6d |

The region base framework (`region`, `jurisdiction`, `feature`, `region_feature`,
`app_user`, `user_region_role`) is **DB-only** and read through
`api/service.py` helpers that degrade to empty/unrestricted when unseeded — so it
adds no required infra.

`DATABASE_URL` selects the target (see §7). One switch or all: e.g. run everything
on files, or set all eight to `db` for a fully database-backed deployment.

---

## 3. Entity-relationship overview

```mermaid
erDiagram
  REGION ||--o{ JURISDICTION : contains
  REGION ||--o{ REGION_FEATURE : offers
  FEATURE ||--o{ REGION_FEATURE : listed_in
  REGION ||--o{ USER_REGION_ROLE : scopes
  APP_USER ||--o{ USER_REGION_ROLE : granted
  JURISDICTION ||--o{ REQUISITION : governs
  JD_DOCUMENT ||--o{ REQUISITION : basis_of
  REQUISITION ||--o{ RUBRIC : has_versions
  RUBRIC ||--o{ REQUIREMENT : contains
  RUBRIC ||--o{ HARD_FILTER : contains
  REQUISITION ||--o{ REQUISITION_CANDIDATE : batch
  CANDIDATE ||--o{ REQUISITION_CANDIDATE : attached_to
  REQUISITION ||--o{ REQUISITION_CONTACT : staffed_by
  APP_USER ||--o{ REQUISITION_CONTACT : acts_as
  REQUISITION ||--o{ HR_PREFERENCE_SET : per_role_prefs
  REGION ||--o{ HR_PREFERENCE_SET : regional_prefs
  HR_PREFERENCE_SET ||--o{ HR_PREFERENCE_OVERRIDE : tunes
  REQUISITION ||--o{ RUN : screened_by
  RUN ||--o{ DECISION : yields
  CANDIDATE ||--o{ DECISION : about
  APP_USER ||--o{ DECISION : made_by
  REQUISITION ||--o{ SCREENING_JOB : tracked_by
  SIGNAL ||--o{ POLICY_SIGNAL_OVERRIDE : dialed_by
  POLICY_PACK ||--o{ POLICY_SIGNAL_OVERRIDE : contains
  JURISDICTION ||--o{ POLICY_PACK : versioned_as
  REGION ||--o{ AUDIT_ENTRY : scoped_to
  APP_USER ||--o{ AUDIT_ENTRY : actor
  REGION ||--o{ REVIEW_SIGNOFF : per_region
```

> Tables present in the schema but not yet populated by a migration path are called
> out in §8 (candidate↔requisition mappings, contacts, HR preferences) — the schema
> is designed up front so integrity holds across the whole model.

---

## 4. Entities

### Identity / region (`db/models/region.py`, `identity.py`)
- **region** — top scope users are granted against (`code` UNIQUE: `IN`, `APAC`,
  `EMEA`, `AMER`).
- **jurisdiction** — a legal jurisdiction (`code` UNIQUE: `IN`, `US-CA`, `EU`, …),
  `region_id` → its **governing region** (many-to-one; a job's governing region is
  *derived* from its `job_location`, never stored on the job).
- **feature** / **region_feature** — the feature catalog (which capabilities are on
  in which region).
- **app_user** — `email` UNIQUE, optional `entra_object_id` (SSO, P27).
- **user_region_role** — `(user, region, role)`; **`region_id = NULL` means all
  regions** (IT admin). Roles are strings: `HRHead, IT, TASpecialist, HRSpecialist`
  + `HiringManager, Legal`.

### JD / role (`db/models/requisition.py`)
- **jd_document** — `content_hash` UNIQUE (a re-uploaded identical JD is one document;
  an edit is a new one), `source_ref` (path/ATS id), `text`.
- **requisition** — `req_code` UNIQUE (the human handle the API routes on), FKs to
  `jd_document` + soft `job_location` (jurisdiction code). Two roles may share a
  *title* and still be distinct — identity is the key, never the title.
- **requisition_contact** — HM / HR-SPOC / recruiter (schema present; see §8).

### Criteria (`db/models/rubric.py`)
- **rubric** — versioned per role, `UNIQUE(req_code, version)` and
  `UNIQUE(requisition_id, version)`; a UI edit saves a new version.
- **requirement** / **hard_filter** — owned children (ORM cascade delete-orphan).

### Candidates / batch (`db/models/candidate.py`)
- **candidate** — natural PK = the content-hash `candidate_id`.
- **requisition_candidate** — the JD↔résumé batch, `UNIQUE(requisition_id,
  candidate_id)` (schema present; see §8).

### HR preference (`db/models/preference.py`)
- **hr_preference_set** (`scope ∈ global|region|role`) + **hr_preference_override**
  (schema present; see §8).

### Governance config (`db/models/governance.py`)
- **signal** — `key` UNIQUE (registry signal id), definition/direction/cap/kind/…
- **excluded_signal** — `key` UNIQUE, `is_base_locked` marks the base 8 (never
  removable — a rail enforced in `governance.py`, not the DB).
- **registry_version** — one row per published registry version.
- **policy_pack** — `UNIQUE(jurisdiction_code, version)`, `is_current`,
  floor/obligations/retention/monitoring; **policy_signal_override** children.

### Runtime (`db/models/runtime.py`)
- **run** — the screening-run index (region + jurisdiction + versions + counts +
  bands + cost stamped). Artifacts stay on disk; this is the queryable index.
- **decision** — append-only feedback log; `reason` required (repo-enforced).
- **screening_job** — replaces the in-memory job dict (survives restart).
- **audit_entry** / **review_signoff** — region-scoped governance trail + the
  per-region "pending HR + Legal review" state.

---

## 5. Integrity rules & rails

- **Append-only logs.** `decision` and `audit_entry` key on a monotonic
  autoincrement id (`BigInteger`/`Integer`-on-SQLite). "Latest wins" and the
  review-pending banner order by that id — **never wall-clock** (the OS clock's
  coarse resolution can tie a sign-off and a following publish in the same second).
- **Required reason.** An empty `decision.reason` is rejected in the repo — the same
  guarantee the JSONL log made.
- **Floor protection.** A policy overlay can never disable a floor item (redaction,
  human decision, evidence, exclusions, logging); the base exclusion list (gaps,
  salary history, age proxies, …) can never be shrunk. Enforced identically on the
  file and DB publish paths (`governance._apply_registry_edits` /
  `_assert_pack_floor`), validated before any write.
- **Versioning.** Every registry/pack/rubric edit bumps a version and appends an
  audit entry; runs stamp the exact versions used.
- **`req_code` soft links.** `run`, `screening_job` and `rubric` carry a `req_code`
  string so a row is addressable **before** the `requisition` table is populated.
  Importing requisitions (P6d) backfills the real `requisition_id` FK via
  `requisitions.backfill_requisition_links` (idempotent).
- **Region scoping.** `run`/`decision` carry their **governing** region (derived from
  the job's jurisdiction); `audit_entry`/`review_signoff` carry the region whose
  config was touched. A regional query returns its own rows **plus** global (NULL)
  ones; a `None` query is the whole-org view.

---

## 6. Region model, feature catalog & region-scoped RBAC

Seeded by `screener db seed` (`db/seed.py`), idempotent:

- **Regions:** `IN` (India — the pilot), `APAC`, `EMEA`, `AMER`.
- **Jurisdiction → region:** `IN→IN`, `SG/JP→APAC`, `EU→EMEA`, `US-default/US-NYC/US-CA→AMER`.
- **Feature catalog:** `blind_review`, `audit_export`, `jurisdiction_editor` (on by
  default per region); `candidate_enrichment`, `learning_loop` (§12 — seeded off).
- **Dev grant:** the dev user gets an **all-regions** grant so local dev is
  unchanged (sees every region).

APIs: `GET /api/regions`, `GET /api/features`, `GET /api/access/grants` (the
administration surface), and a `regions` block on `GET /api/me` carrying the
accessible codes, their display names, and where this person's lens should OPEN
(`home` / `home_source`).

**Status (2026-09-12).** The P27 deferrals have landed, in pieces, over several
passes — recorded here because the original list read as outstanding long after it
was not:

- *Enforcement* — P38. `_req_authorized` → `service.region_allows` on every
  per-requisition route; `?region=` on `/api/roles` and the eight Audit Explorer
  routes.
- *The picker* — P38, on the sidebar rather than at login; it now opens on a
  derived **home region** (chosen > the country the directory holds > a sole
  grant > All regions) and carries an explainer of what a switch does and does
  not change.
- *Administration* — 2026-09-12. `access_edit` (Super Admin) + `/api/access/grants`
  + the Access & Roles section. Before this, `grant_role` had no caller outside
  `screener db seed`, so every real SSO user was unscoped — which
  `service._region_context_query` treats as UNRESTRICTED rather than locked out,
  and that default is deliberately unchanged.
- *The Hiring-Manager portal* and *feature-flag gating* — shipped in P37/P41. A
  flag now also declares `scope: requisition | app`, since only a
  requisition-scoped one can be read with a region in hand.
- *`identity.roles_in_region`* was never adopted as a capability hook and remains
  unused: capabilities come from Entra app roles and are global, while a region
  grant narrows only WHICH requisitions they reach. The two axes are deliberately
  separate — see [14-GLOBAL-ROLLOUT.md](14-GLOBAL-ROLLOUT.md) §2.

---

## 7. Connection & production auth (passwordless)

`DATABASE_URL` (in `.env`; **localhost creds only, never a secret**) selects the
target. Local dev matches `docker-compose.yml`:

```
DATABASE_URL=postgresql+psycopg://screener:screener@localhost:5432/screener
```

**Production is passwordless via Entra / Managed Identity** (`db/engine.py`): when
`DATABASE_URL` targets an Azure Database for PostgreSQL host
(`*.postgres.database.azure.com`) **and carries no password**, the engine attaches
an Entra access token as the connection password at connect time via
`DefaultAzureCredential` (scope `https://ossrdbms-aad.database.windows.net/.default`)
— dev uses your `az login` token, prod uses Managed Identity, the same code path as
the Key Vault design. **No database secret ever lives in `.env`, code, or Git.**

```
# prod (no password in the URL — token attached at connect time)
DATABASE_URL=postgresql+psycopg://<app-identity>@<server>.postgres.database.azure.com:5432/screener?sslmode=require
```

The engine is lazy (importing it opens no connection) and caches one pooled engine
per resolved URL with `pool_pre_ping`.

---

## 8. Migrations & operational setup

Alembic chain (single head `d4e9f2a1b6c3`):

| Revision | Adds |
|---|---|
| `c2625f42970a` | initial schema (all tables + indexes) |
| `b7f1c9a24d80` | `run.req_code` |
| `a3d5e8b1c7f2` | `screening_job.req_code` |
| `d4e9f2a1b6c3` | `rubric.req_code`, nullable `requisition_id`, `UNIQUE(req_code, version)` |

Setup sequence:

```bash
docker compose up -d db          # local Postgres 16 (creds match the default URL)
uv run screener db upgrade       # apply all migrations
uv run screener db import        # one-time backfill of flat-file history/config -> rows
uv run screener db seed          # region framework (regions, features, dev grant)
```

`screener check` includes a DB-connectivity probe (✓/✗, no creds shown). `db import`
is guarded per domain (skips a table that already has rows) and idempotent; `db
seed` is idempotent (upserts).

**Not yet populated (schema present, integrity designed):** `requisition_candidate`
(the batch mapping — files stay on disk, so this is a dual-store to add deliberately),
`requisition_contact` (HM/HR-SPOC — net-new, no file data to migrate), and
`hr_preference_set`/`hr_preference_override` (net-new tuning layer + its "can't
re-enable a pack-disabled signal" rail). These lean toward §12/production features
rather than flat-file migration and are scoped separately.

---

## 9. Testing

The repository suite runs **offline against SQLite** (in-memory, `StaticPool`, FK
pragma on) so it stays fast and DB-free in CI. The schema avoids PG-only features so
the same models/migrations run on both. An **opt-in `TEST_DATABASE_URL`** runs the
same suite against a real Postgres for a production-fidelity pass (migrations as
direct `ALTER`s, `BigInteger` identity, stricter type coercion) — the path SQLite
cannot exercise.
