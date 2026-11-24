# Resume Screener — Build Pack v3 (Start Here)

**v3 in one line:** the PoC is no longer a throwaway scorer — it's the **production skeleton**: a two-layer engine (deterministic audit layer + bounded agentic judgment), a bias critic, a signal registry, an ATS port (iCIMS ⇄ Greenhouse ⇄ Lever later by config), a Streamlit review UI, and feedback capture from day 1. Secrets stay in Key Vault; your real Azure config is baked in.

## The files

| File | Purpose |
|---|---|
| **ARCHITECTURE-TARGET-DESIGN.md** | The design: your brief, my three corrections, the two layers, the signal registry (incl. example + excluded signals), critic, learning done right, ATS port, UX, phasing. **Read this first** — it's the "why". |
| **architecture-v3.png** | The diagram (solid = PoC now, dashed = production with seams stubbed). |
| **00-START-HERE.md** | This file. |
| **01-BUILD-PACK.md** | The build spec → save as `CLAUDE.md`. The "what". |
| **02-PROMPT-LIBRARY.md** | Prompts **P0–P14** with gates and fix-it prompts. The "how". |
| **03-SETUP-AUTH-AND-CONFIG.md** | Manual steps: deployments, admin's Key Vault tasks (email template), `az login`, `.env`, data. |

Post-PoC addenda: **04** UI simplification & storage · **05** batches/theme · **06** HR explainer · **07** tech explainer/runbook · **08** hosting/SSO/RBAC · **09** React+FastAPI UI · **10** data model · **11** v2 requisition workspace · **12-ASK-CLAUDE-MCP.md** — the Ask-Claude MCP connector (local, read-only, RBAC-guarded, audited; `screener mcp`).

## Order of play
1. Read `ARCHITECTURE-TARGET-DESIGN.md` (15 min) — especially §1 (the three corrections) and §5 (signals) since you'll approve those.
2. Do `03-SETUP-AUTH-AND-CONFIG.md` (admin email, two model deployments, `az login`).
3. Open the project folder in Cursor → run **Claude Code** in its terminal → `02-PROMPT-LIBRARY.md` from **P0**, one prompt at a time. **P1's five ✓ checks are a hard gate.**

## Tooling (unchanged from v2, still right)
**Claude Code = primary agent** (Fable 5 available; reads `CLAUDE.md`; runs/fixes commands itself) inside **Cursor = editor/reviewer**. Models: leave Claude Code on **Fable 5** throughout; if optimizing, Sonnet 4.6 for P0/P2/P3/P5/P7/P11/P14 and Fable 5 (or Opus 4.8) for P1, P4, P6, P8–P10, P12–P13 and gnarly debugging. Planning stays on Fable 5.

## What's genuinely new in v3 (vs v2)
- **Two-layer scoring**: Layer 1 = pure, replayable code over frozen evidence (the audit answer) · Layer 2 = agent applying **only registry signals** as **bounded** adjustments (caps enforced in code) · **Bias Critic** can suspend adjustments.
- **Signal Registry** (`signals/registry.yaml`): your "job-hopper" and "competitor-background" ideas live here as governed, capped, evidence-required signals — plus seven more, and an explicit **excluded** list (gaps, unemployment, proxies…). HR/Legal-reviewable.
- **ATS port** with a local adapter now; iCIMS/Greenhouse/Lever are config-switched adapters later — no core changes.
- **Streamlit UI**: rubric edit/approve (per-JD, versioned), registry view, run, shortlist review with all evidence, **agree/override + reason** → feedback log — the substrate for the (offline, human-gated) learning loop later.
- **Semantic similarity** moved from a hidden blended term into a declared signal (`domain_transfer_distance`) — cleaner audits.
- **Jurisdiction Policy Engine** (`policies/`): floor + dial — a global floor (redaction, human decision, evidence, exclusions) that never varies, and per-job-location packs (IN·SG·JP·EU·US-NYC·US-CA·US-default) that dial which signals apply, their caps/params (e.g., tenure norms India vs Japan), obligations checklists, retention and monitoring categories. Pilot default: **IN**.
- Cost: ~5–6 model calls/resume (Layer 2 + critic run on gate-passers only); caching keeps re-runs near-free.

## Decisions locked (5 Jul 2026)
EEO/monitoring data **optional** in the PoC (interface ships) · preferred-competitor list is **HR-owned** and HR is the primary operator (UI copy in plain recruiter language) · enrichment **held for v1** (pilot roles are non-tech: **India Finance & Sales**) · **Streamlit** confirmed for the PoC UX · HR + Legal review **deferred until after the PoC**, with one safeguard: until then, outputs are internal evaluation only — never used on live requisitions. Also confirmed: Key Vault access model is **Azure RBAC** (admin just adds two secrets + one role).

## Three corrections you should be able to repeat to stakeholders
1. **Layer 1 doesn't exempt Layer 2 from audits** — anything that changes ranking is in scope (LL144, EU AI Act, GDPR 22, EEOC). Layer 2 is therefore built auditable: declared signals, caps, evidence, critic, logs.
2. **No LinkedIn scraping.** Enrichment (production phase) uses **candidate-provided links only**, additive-only, disclosed; absence of a web presence never counts against anyone.
3. **No raw RL on hiring outcomes** (the Amazon lesson). We capture feedback now and learn **offline** with fairness release-gates and human approval; "great hires" refine *rubrics* and power a *regression eval set* — never a similarity target.

## Non-negotiables (unchanged)
Human decides · nothing auto-rejects or hides · redact before scoring · verbatim, verified evidence · resumes are untrusted input · secrets only in Key Vault · personal data never in Git.

➡️ Start with **ARCHITECTURE-TARGET-DESIGN.md**, then **03-SETUP**.
