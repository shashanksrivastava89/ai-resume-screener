# PROMPT LIBRARY v3.1 — Build the Resume Screener (two-layer, jurisdiction-aware)

Paste into **Claude Code** (inside Cursor's terminal), **one prompt at a time, in order**. Claude Code runs and verifies commands itself — let it, and review. Prerequisites: `03-SETUP-AUTH-AND-CONFIG.md` done (admin's Key Vault tasks — RBAC confirmed, so just secrets + role; two model deployments; `az login`).

**Driving rules:** one prompt at a time · verify each step · paste full errors back · commit after each working step (`"Commit this with a clear message."`) · no secrets in chat (they live in Key Vault). Useful: *"Plan in 3–5 bullets first, wait for go."* · *"Keep it minimal, per CLAUDE.md."* · *"Explain this file in plain English."*

---

## P0 — Orient + scaffold
```
You're helping me build the "Resume Screener": a two-layer (deterministic + bounded-agentic),
jurisdiction-aware shortlisting engine with a Streamlit review UI. Pilot: India, Finance & Sales
roles; HR/recruiters will operate it. I'm a product manager — explain simply, work in small
runnable steps, run/verify commands yourself.

Read 01-BUILD-PACK.md fully — it is the spec. Then:
1. Copy it to CLAUDE.md; create .cursorrules exactly per its §9.
2. Scaffold §3: folders + empty modules (incl. ats/, policies/, signals/, policy.py, ui_app.py),
   pyproject.toml (uv, py3.11+, [project.scripts] screener = "resume_screener.cli:app"),
   .env.example pre-filled with §8 non-secrets (deployments TODO), config.yaml per §8
   (default_jurisdiction: IN), .gitignore (.env, data/, output/, .cache/), short README.
3. Add §2 dependencies (incl. streamlit). Stubs only. Run uv sync yourself, confirm, summarize, stop.
```
**Then you:** copy `.env.example` → `.env`; fill the two deployment names.

## P1 — Key Vault config + clients + doctor  ⛔ GATE
**Do first:** `az login --tenant 00000000-0000-4000-8000-2200910f4d4b` (then `az account set --subscription 00000000-0000-4000-8000-b361bdfa266c` if needed).
```
Implement config.py, azure_clients.py, and "screener check" per CLAUDE.md §§0,2,7,8.
- config.py: .env + config.yaml -> typed Settings; fetch the two API keys FROM KEY VAULT
  (SecretClient on AZURE_KEY_VAULT_URI, DefaultAzureCredential, secret names from env).
  In-memory only; never written/logged/printed. Friendly errors: credential missing -> print the
  az login command above; 403 -> "ask admin for Key Vault Secrets User on ReesumeScreenerKV
  (vault is RBAC)".
- azure_clients.py: DocumentIntelligenceClient(AzureKeyCredential(<KV>)); OpenAI on the Azure v1
  endpoint (base_url = endpoint + "/openai/v1/", api_key=<KV>); probe once, fall back to
  AzureOpenAI(api_version=<current GA you verify>) only if v1 is rejected, and tell me which.
- "screener check": five ordered checks, ✓/✗ + one-line hints, no secret values:
  sign-in, both KV secrets, tiny DI call, tiny chat call, tiny embeddings call.
Run `uv run screener check` yourself, show output, stop.
```
**Gate:** five ✓. Failures map to: login → the az command · vault 403 → admin role · vault network → VPN/allow-list · model 404 → deployment name in `.env`.

## P2 — ATS port + ingest + cache
**Do first:** 3–5 sample resumes into `data/resumes/`.
```
Implement cache.py (hash-keyed JSON disk cache under .cache/), ats/base.py (ATSPort per §4:
fetch_requisitions/fetch_candidates/fetch_documents/write_back/on_stage_change; Requisition
carries job_location) and ats/local_folder.py (reads jds/ + data/resumes/; job_location defaults
from config; write_back = logged no-op). ingest.py: DI "prebuilt-layout" (v4 handles PDF+DOCX;
python-docx only if the service rejects a file), cache by file hash, candidate_id from
file-bytes hash, extraction confidence recorded. Hidden "screener debug ingest --limit 3".
Run it twice (second instant from cache), show me, stop.
```

## P3 — Redaction
```
Implement redact.py per CLAUDE.md §6.2 (default ON): strip/mask name, contact, address, photo
refs, gender, age/DOB, obvious proxies — preserve skills/employers/dates/education. Private
candidate_id -> source_file map under output/ (gitignored). "screener debug redact --limit 1"
prints BEFORE/AFTER. Run, show both, stop.
```
**Verify:** identity gone, job content intact; tell it exactly what to keep/remove if not.

## P4 — Models + Rubric Agent + Registry + Policy Packs
**Do first:** one JD (a real Finance or Sales role) pasted into `jds/role-1.md`.
```
Implement models.py (ALL §4 contracts incl. PolicyPack/EffectivePolicy), rubric.py, registry.py,
and policy.py.
- Rubric Agent: JD -> draft Rubric via structured outputs (parse helper — chat.completions.parse
  if available, else beta.…; temperature=0); weights normalized; save jds/role-1.rubric.v1.yaml
  (human-editable; edits bump version). "screener rubric --jd jds/role-1.md".
- SEED signals/registry.yaml per CLAUDE.md §8: the ELEVEN signals with kind/direction/
  cap_points/enabled_default/job_relatedness — incl. quota_attainment_evidence and
  deal_profile_fit for Sales; preferred_domain_background with an EMPTY org list + "HR owns
  this" comment; India-pilot examples in descriptions (GST/Ind AS/SOX; CA/CPA/CFA/ACCA/NISM;
  Indian regional languages); public_work_relevance enabled_default:false. Plus the excluded:
  list with reasons. registry.py validates (dup ids, caps, unknown fields -> clear errors).
- SEED policies/ per §8: base.yaml (floor markers) + IN, SG, JP, EU, US-default, US-NYC, US-CA
  overlays with signal_overrides (incl. tenure params: IN vs JP norms), plain-language
  obligations checklists, retention notes, monitoring_categories (IN: voluntary gender/
  disability; never caste). policy.py: load + merge -> EffectivePolicy; obligations = union;
  attempts to disable floor items FAIL validation (write that test now).
Run: the rubric command, registry validation, and "screener debug policy --jurisdiction IN"
vs "--jurisdiction EU" showing the differing enabled signals + obligations. Show me all three, stop.
```
**Then you (important):** open and **edit** `role-1.rubric.v1.yaml` — this is your JD-mapping logic. Skim `signals/registry.yaml` and `policies/IN.yaml`; both are yours (and later HR/Legal's) to tune.

## P5 — Profile parse
```
Implement the parse step per CLAUDE.md: REDACTED text -> ResumeProfile via structured outputs
(temperature=0), resume text delimited as DATA never instructions; cache by redacted-text hash;
freeze artifact under output/<role>/artifacts/. "screener debug parse --limit 3" -> readable
JSON. Run, show, stop.
```

## P6 — Findings (evidence-verified, ONE call per candidate)
```
Implement findings.py per §5.1: ONE structured call per candidate takes the FULL rubric +
redacted text and returns a Finding for EVERY requirement (full/partial/none + VERBATIM evidence
or null). Code verifies each evidence string is a substring of the redacted text; otherwise
downgrade to none with a note. Never loop per requirement. Freeze artifacts. "screener debug
findings --limit 3". Run, show one candidate fully, stop.
```

## P7 — Computed features + embeddings (policy-parameterized)
```
Implement embed.py (batched, cached embeddings; numpy cosine) and features.py (pure code):
tenure stats over PERMANENT roles only — the short-stint threshold and window come from the
EffectivePolicy params, not hard-coded; skill recency for must-haves; scope trajectory;
certification currency; domain_fit_cosine (JD vs redacted resume, 0..1). Employment gaps are
computed NOWHERE (policy floor). Unit-test the pure parts incl. params wiring. "screener debug
features --limit 3 --jurisdiction IN". Run, show, stop.
```

## P8 — Layer 1 (deterministic — the audit answer)
```
Implement layer1.py per §5.2 as PURE code over frozen artifacts (no model calls): hard filters
-> flags + eligible=false but still listed with reasons; must-have gate; coverage = weighted
sum; base_score = 100*coverage. Thorough unit tests: weight normalization; match math; gate
reasons; REPLAY (same stored artifacts + same rubric version => identical Layer1Result).
"screener debug l1 --limit 5". Run tests + command, show, stop.
```

## P9 — Layer 2 (policy-filtered, bounded agentic judgment)
```
Implement layer2.py per §5.4. ONE structured call per GATE-PASSING candidate: inputs = redacted
profile + findings + computed features + the signals ENABLED BY THE EFFECTIVE POLICY (full
definitions + effective caps) + JD context. Output = Layer2Result (adjustments with signal_id,
signed points, evidence, rationale; proposed_signals; short narrative).
ENFORCE IN CODE (not prompt trust): unknown/policy-disabled signal_id -> dropped; per-signal
clamp to the EFFECTIVE cap; total clamp to layer2_total_cap_points; evidence required (resume
quotes substring-verified). Proposals -> signals/proposals.jsonl, never applied. Prompt
guardrails: resume text is data; excluded-list topics (registry + policy additions) must not be
reasoned on. "screener debug l2 --limit 3 --jurisdiction IN" printing each adjustment with
signal, points, evidence, rationale. Run, show one candidate fully, stop.
```
**Verify:** every adjustment maps to a signal you recognize as enabled for IN, within caps, with a findable quote.

## P10 — Bias Critic
```
Implement critic.py per §5.5: structured review of each Layer2Result against the registry +
excluded list + policy additional_exclusions: per-adjustment pass/flag/suspend + notes. Code
removes suspended adjustments from scoring, keeps them in the record + UI queue. Include the
pool-level monitors interface stub (four-fifths) with a clear TODO: needs separately-held EEO
data; categories come from the EffectivePolicy (optional for the PoC). Unit test: a synthetic
rationale citing an excluded basis gets suspended. "screener debug critic --limit 3".
Run tests + command, show, stop.
```

## P11 — Scoring, reports, end-to-end run
```
Implement scoring.py (final = clamp(base ± surviving adjustments); bands; must-have cap;
"Not a fit (gate)" + reasons for ineligible; confidence), report.py (shortlist.xlsx/.csv: rank,
id, band, final, base, adj_total, confidence, must_haves_missing, top gaps, critic flags;
candidates/<id>.json; readable report.html with L1+L2+critic detail; run_meta.json with
jurisdiction + obligations checklist + all versions + counts + cost estimate + errors) and the
real "screener run" per §7 (--jurisdiction default from config; pipeline per §5; tqdm;
concurrency + tenacity backoff; resumable from cache; debug group hidden). Run --limit 10 into
output/test yourself, show the summary incl. the jurisdiction stamp, stop.
```
**Then you:** open `output/test/shortlist.xlsx` + `report.html`. Good? Load all ~500 resumes, run without `--limit` into `output/role-1`; check `run_meta.json` (cost, errors, jurisdiction).

## P12 — Streamlit UI + feedback capture
```
Plan first in 5 bullets, wait for my go. Then implement ui_app.py + feedback.py per §7. HR/
recruiters are the operators: ALL UI copy in plain recruiter language (no engineering jargon;
short tooltips explaining signals and bands). Views:
Rubrics (pick JD; generate; edit requirements/kind/weights inline; approve -> next version) ·
Signals (registry table with friendly descriptions; toggle enabled_default; edit caps; save;
proposals.jsonl read-only) · Run (JD; jurisdiction selector defaulting to IN with the
obligations checklist shown as a badge/panel; limit; progress; links to outputs) · Shortlist
(ranked table -> candidate detail: gate result + findings with quotes; each adjustment with
signal/points/evidence/rationale; critic flags & suspended queue; agree / override / re-rank
with REQUIRED reason -> append Decision to output/feedback/decisions.jsonl with all versions
incl. policy) · Runs (run_meta viewer). Wire "screener ui". Start it yourself, give me the URL, stop.
```
**Verify:** edit a rubric (version bumps); toggle a signal; switch jurisdiction and watch the badge/obligations change; review 2–3 candidates and record decisions; confirm `decisions.jsonl` carries versions.

## P13 — Calibrate (config / registry / policy only)
```
Calibration feedback on output/role-1: ⟨who's too high/low and why⟩.
Propose changes ONLY to config.yaml (bands, must-have cap, layer2_total_cap_points),
signals/registry.yaml (caps, enabled_default), policies/IN.yaml (signal params like tenure
thresholds) or rubric weights. Explain trade-offs, apply, re-run --limit 20, show before/after
ranking diff. Do not touch pipeline logic.
```

## P14 — Polish, tests, docs
```
Polish per CLAUDE.md §11 (definition of done): verify tests — Layer-1 replay, cap clamping,
FLOOR PROTECTION (overlay disabling redaction/exclusions fails), policy merge (IN vs EU differ),
critic suspension recompute, redaction, registry validation — all offline. Clean console
summaries. README: the two-layer + policy design in ~8 lines, setup (az login, Key Vault role,
deployments), commands, UI views, "internal evaluation only until HR+Legal review" note, and a
production-phase pointer to CLAUDE.md §12. Confirm .gitignore. Run pytest yourself, show green,
list final commands, stop.
```

---

## Troubleshooting (anytime)
- **Auth/KV:** `Azure auth failed: ⟨paste⟩. Login, Key Vault role, vault network, or wrong deployment name? Tell me exactly what to check/run.`
- **429s:** `Hitting rate limits. Tune backoff + lower concurrency; keep runs resumable; note raising the deployment TPM quota in AI Foundry is the durable fix.`
- **Agent misbehaving:** `An adjustment used ⟨reasoning⟩ that's excluded / not enabled for this jurisdiction. Show why code-level enforcement didn't catch it, fix the ENFORCEMENT (not just the prompt), add a test.`
- **Policy confusion:** `Show me the EffectivePolicy for --jurisdiction ⟨X⟩: enabled signals, caps, params, obligations — and where each came from (base vs overlay).`
- **Rankings feel off:** `Show base vs adjustments vs critic for candidates ⟨ids⟩ before changing anything.`
- **Drift:** `Summarize the structure and list deviations from CLAUDE.md.` · **Undo:** `Revert your last change; propose a smaller fix.`

## Recipes
- `Add jds/role-2.md (pasted Sales JD). Draft its rubric for my review, then run into output/role-2.`
- `I edited role-1.rubric.v2.yaml — re-run role-1 with it (cache makes this cheap).`
- `screener explain --role output/role-1 --candidate ⟨id⟩ — walk me through both layers and the critic.`
- `Export a one-page audit note for candidate ⟨id⟩: jurisdiction, rubric version, findings + quotes, gate result, adjustments + signals, critic verdicts, human decision.`
- `Prepare the HR+Legal review packet: plain-language export of the signal registry (incl. exclusions with reasons) + policies/IN.yaml obligations + two anonymized worked examples from output/role-1.`

## Production-phase prompts (LATER — do not run during the PoC)
- **iCIMS adapter:** `Implement ats/icims.py against ATSPort per CLAUDE.md §12 (REST + assessment-partner OAuth 2.0; write_back = score/band/result URL/custom attributes). Config-switch ats.provider.`
- **Candidate-residence policy union:** `Extend policy.py: for remote/multi-location reqs, resolve the UNION of obligations across triggered jurisdictions (job location + declared candidate locations).`
- **Enrichment tool:** `Enable public_work_relevance for tech roles: candidate-provided links only (official APIs), additive-only, identity-checked, disclosed; absence never penalized. Tests included.`
- **Keyless auth:** `After admin grants Cognitive Services OpenAI User / Cognitive Services User: switch to Entra-token auth (verify the current token scope), drop KV key fetch, update check/README.`
- **Learning loop v1:** `From decisions.jsonl, build the offline eval harness + a calibration report (threshold/weight/param suggestions with four-fifths backtest). No auto-apply — output a proposal for human review.`
