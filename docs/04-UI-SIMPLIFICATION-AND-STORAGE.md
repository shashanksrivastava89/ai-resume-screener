# ADDENDUM 04 — Storage Explained + HR-Simple UI Redesign (P15–P17)

Two topics: (A) where every piece of data lives and why there's no database, and (B) a redesign of the Streamlit UI for HR operators, delivered as three prompts that pick up where P14 left off.

---

## Part A — Where everything is stored (and the database question)

### A1. The storage map (PoC, today)

| Data | Lives at | Format | Notes |
|---|---|---|---|
| **Secrets (API keys)** | Azure Key Vault (`ReesumeScreenerKV`) | — | Fetched at runtime; never on disk |
| **Raw resumes** | `data/resumes/` | PDF/DOCX | Gitignored; your copy of the OneDrive share |
| **Frozen artifacts** (profiles, findings) | `output/<role>/artifacts/` | JSON | The audit substrate — Layer-1 replays from these |
| **Screening checklists (rubrics)** | `jds/*.rubric.vN.yaml` | YAML, versioned | Human-edited; every run records which version it used |
| **Judgment rules (signal registry)** + proposals | `signals/registry.yaml`, `signals/proposals.jsonl` | YAML / JSONL | HR/Legal-reviewable |
| **Policy packs** | `policies/*.yaml` | YAML | Floor + per-jurisdiction dials |
| **Results & reports** | `output/<role>/` | xlsx, csv, html, JSON | shortlist + per-candidate detail + `run_meta.json` |
| **Human decisions (feedback)** | `output/feedback/decisions.jsonl` | JSONL, append-only | The learning-loop substrate |
| **Re-identification map** | `output/` (private file) | JSON | candidate_id → source file; gitignored |
| **Cache** | `.cache/` | JSON by content hash | Deletable anytime; only costs a re-run |

Nothing is stored anywhere else: no database, no cloud storage beyond Key Vault, nothing in Git (`.gitignore` covers `.env`, `data/`, `output/`, `.cache/`).

### A2. Why no managed database for the PoC

One operator, one machine, ~500 candidates, a few runs. Flat files at this scale are strictly better: you can open every artifact and audit it by eye, they cost nothing, need no networking/firewall/admin setup, and Streamlit reads them instantly. A managed database (Azure PostgreSQL etc.) would add ~30–60 minutes of setup, a monthly cost, and an ops surface — for zero PoC benefit.

Crucially, **the file layout is the database schema in waiting**: every Pydantic contract (§4 of CLAUDE.md) maps 1:1 to a future table, and the frozen artifacts map to blob objects. Migration is mechanical, not a redesign.

### A3. When a database becomes necessary (production triggers)

> **✅ REALIZED (post-PoC, 2026-07-10).** The **structured half of this table is now built**: decisions, audit, run index, jobs, and config-as-data (registry, policy packs, rubrics, JDs/requisitions) live in **PostgreSQL** via a SQLAlchemy layer, behind per-domain config switches whose defaults keep the flat-file behavior. The **Blob half (raw résumés + artifacts + reports) is still deferred to P26** — large files stay on disk for now. Full schema/integrity/region-model/prod-auth: **[10-DATA-MODEL.md](10-DATA-MODEL.md)**. The rows below are the target; the paragraph after this table is the original PoC rationale, kept for context.

Any of: multiple HR users operating concurrently · the engine moves off a laptop into an Azure service (FastAPI + workers) · ATS webhooks writing asynchronously · thousands of candidates/runs needing queries · formal retention/backup/point-in-time-restore obligations. At that point:

| PoC file | Production home |
|---|---|
| Raw resumes + frozen artifacts + html reports | **Azure Blob Storage** (immutable containers; or fetch resumes from the ATS on demand and store only artifacts) |
| Rubrics, registry, policies, runs, results, **decisions** | **Azure Database for PostgreSQL (Flexible Server)** — decisions as an append-only table |
| Embeddings | pgvector in the same Postgres, or Azure AI Search |
| Cache | Redis, or simply drop it (recompute) |

For production India deployment, place both in an Indian region (Central India) per the region note in `03-SETUP`.

Optional middle step if you ever want two or three users on a shared machine *before* production: SQLite (a single-file database, no server) — one prompt away, but don't do it unless file contention actually bites.

### A4. Two housekeeping points worth doing now

1. **Backups:** `output/` (decisions + artifacts) is your only copy of the audit trail. Either place the whole project folder inside your corporate OneDrive-synced directory (keeps personal data inside the org boundary) or copy `output/` there after significant runs. Never to personal cloud storage or Git.
2. **Keep the coding agent out of the data.** During vibe-coding sessions, Claude Code/Cursor can read any file in the repo — including real resumes under `data/` — and file contents it reads are sent to the model. Add a standing rule (P16 below does this): the agent must never open files under `data/`, `output/`, or `.cache/`; when debugging, it works from synthetic samples you provide. *(Superseded 2026-09-25 by CLAUDE.md §6.12: candidate data may be read when testing, on UAT, or investigating an issue — only what the task needs, and never into commits, tests, fixtures or docs.)*

---

## Part B — The HR-simple UI redesign

### B1. What went wrong, in one line

The current UI shows the *system's* mental model (layers, signals, jurisdictions, critic verdicts) — HR needs the *task's* mental model: set up a role → screen → review and decide. Compliance machinery must still exist, but behind the curtain.

### B2. Design principles for the rebuild

1. **Two modes:** *Recruiter* (default: Roles → Screen → Review, three pages, that's all) and *Admin* (sidebar toggle: Rules & Signals, Runs & Audit, Data).
2. **Progressive disclosure:** verdict first (colored chip + one plain sentence), evidence one click away (expanders), machinery two clicks away (Admin). Max ~40 visible words per candidate before any expander.
3. **Visuals over prose:** chips, score bars, ✓/~/✗ icons, metric cards. No raw JSON, no IDs, no YAML anywhere in Recruiter mode.
4. **Names, not hashes — with a blind option.** Redaction protects the *scoring models*; the human decision-maker may see who they're advancing. Default: show the source filename (usually contains the name) from the private map; a **"Blind review"** toggle hides it for fairness-first first passes.
5. **Decisions in two taps:** big buttons + reason chips (free text optional). Mandatory essay-writing kills adoption; chips still satisfy the audit trail.

### B3. Copy dictionary (system term → what HR sees)

| System | HR-facing |
|---|---|
| fit band Strong / Possible / Not a fit | **Strong match / Possible match / Not a match** |
| eligible=false (gate) | "Didn't meet must-haves (still listed)" |
| coverage_score | "Requirements matched: 7 of 9" |
| Layer-2 adjustments | **"Context notes"** (e.g., "+ Relevant competitor experience", "− Very short recent stints") |
| critic suspended | "Set aside by fairness check ⓘ" (collapsed; excluded from score) |
| confidence: low | "Resume was hard to read — please double-check" |
| rubric | **"Screening checklist"** |
| signal registry | "Judgment rules" (Admin only) |
| jurisdiction/obligations | small caption "Hiring rules applied: India ⓘ" (details in Admin) |
| run | "Screening" |

Never shown in Recruiter mode: "Layer 1/2", signal_id, weights as decimals, semantic/coverage components, candidate_id hashes, model/prompt versions.

---

### P15 — Recruiter mode overhaul (paste into Claude Code)

```
The Streamlit UI is too complex for HR operators. Rebuild ui_app.py per the addendum
04-UI-SIMPLIFICATION-AND-STORAGE.md (in the repo root — read it first), keeping ALL engine code,
contracts and the feedback log unchanged. Structure:

SIDEBAR: app title, mode switch "Recruiter | Admin" (Recruiter default). Recruiter mode has
exactly three pages: Roles, Screen, Review.

ROLES: role cards (title, candidates screened, checklist status "Approved v2" or "Draft").
"New role" -> paste JD -> "Create screening checklist" (rubric agent). Checklist editor: one row
per requirement -> plain-text requirement, a "Must-have" toggle, an Importance slider Low..High
(5 steps, mapped to normalized weights internally — never show decimals), add/remove rows,
"Approve checklist" (saves next version). No YAML, no ids, no weight numbers on screen.

SCREEN: pick role -> "N resumes found" -> big "Screen resumes" button -> progress bar ->
"Done — review N candidates ->". Small caption: "Hiring rules applied: India ⓘ" (tooltip: one
sentence + 'details in Admin'). No token/cost/jurisdiction machinery here.

REVIEW (the heart): summary metric cards (Strong match / Possible / Not a match / Needs a
second look). Table: Candidate (source filename via the private map), Match chip
(green/amber/grey), Score bar, Top strengths (up to 2 chips), Watch-outs (up to 2 chips, e.g.
"Missing: CA certification"), Decision status. A "Blind review" toggle in the header replaces
names with "Candidate 1..N". Row click -> detail:
 - header: name/file + match chip + ONE plain-English sentence (rewrite summary_rationale to
   <= 25 words, no jargon);
 - "Requirements" list: ✓ / ~ / ✗ per checklist item, each with an expander "see where we found
   this" showing the verbatim quote;
 - "Context notes": each surviving adjustment as a plain sentence with a +N/−N chip and a quote
   expander — translate per the addendum's copy dictionary (never show signal_id);
 - collapsed section "Set aside by fairness check ⓘ" listing suspended notes (excluded from
   the score);
 - if confidence is low: an amber banner "Resume was hard to read — please double-check";
 - decision bar: [Move forward] [Don't move forward] [Discuss] + reason chips generated from
   this candidate's gaps/strengths + "Other…" optional free text. Writing a Decision to the
   feedback log (with versions) works exactly as before; chips count as the reason.

Apply the copy dictionary EVERYWHERE in Recruiter mode. Target: a recruiter who has never seen
this tool can review a candidate in under 60 seconds. After building, run the app yourself,
walk through Roles -> Screen (--limit 5) -> Review, and give me the URL plus screenshots or a
description of each page. Stop.
```

### P16 — Admin mode, Data page, and guardrails update

```
Now Admin mode (sidebar toggle), reusing existing logic:
- RULES & SIGNALS: the registry as a friendly table — rule name in plain English, what it does
  (one sentence), On/Off, and "How much it can move the score" shown as Small/Medium/Large
  (mapped to cap_points). Proposals list read-only. Saving edits registry.yaml as before.
- RUNS & AUDIT: run history from run_meta.json files (date, role, jurisdiction, candidates,
  cost estimate, errors) + "Export audit pack" button (plain-language registry + obligations +
  a candidate audit note) reusing the existing recipe logic.
- DATA: a storage summary page mirroring the addendum's table A1 — where each kind of data
  lives, with folder sizes; plus two buttons with confirm dialogs: "Delete cache" (.cache/) and
  "Delete all candidate data" (data/resumes contents + output artifacts/results, honoring the
  private map), each explaining consequences in one sentence.
Also: (1) update CLAUDE.md §7 to describe this two-mode UI so future sessions build against it;
(2) add to CLAUDE.md guardrails and .cursorrules: "The coding agent must never open files under
data/, output/, or .cache/; use synthetic samples for debugging." Run the app, verify both
modes, summarize what changed, stop.
*(That rule was superseded 2026-09-25 by CLAUDE.md §6.12.)*
```

### P17 — Optional: 10-minute hallway test before showing HR

Sit one recruiter (or a colleague standing in) in front of the app with **no explanation** and ask them to: (1) create a role from a JD and approve its checklist; (2) screen the sample resumes; (3) find the strongest candidate and say *why* in their own words; (4) record a decision on two candidates; (5) find a candidate who's missing a must-have. Note every hesitation. Then paste to Claude Code:

```
Usability findings from a first-time HR user: ⟨your notes, e.g. "didn't understand 'Watch-outs'",
"couldn't find the approve button"⟩. Propose the smallest UI/copy changes that fix each, apply
them, and summarize. Do not change engine logic.
```

---

**Bottom line:** flat files are correct until production (then Blob + PostgreSQL in an Indian region, mapped 1:1 from today's contracts), and the UI complexity is a presentation-layer fix — P15/P16 restructure it around HR's task without touching the audited engine underneath.
