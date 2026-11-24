# Resume Screener — Target Design v3 (PoC → Production)

**Purpose.** You've decided the PoC is the seed of the production system, and you've proposed major design ideas: a two-layer deterministic + agentic architecture, per-JD rubrics with human review, an autonomous recruiter-like agent with tools, a bias-review agent, learning loops, and ATS flexibility (iCIMS → Greenhouse/Lever). This document is my response as your architect: **what I'm adopting as-is, what I'm reshaping and why, and the resulting design** — plus how the PoC build changes so nothing is throwaway.

Companion diagram: `architecture-v3.png` (solid = PoC now, dashed = production phase with interfaces stubbed in the PoC).

---

## 1. Verdict on the brief — three corrections before we build

Your two-layer instinct is genuinely good architecture. Three parts of the brief, however, would create legal or quality problems *as stated*, and I'd be failing you to build them silently:

**Correction 1 — Layer 1 does not put Layer 2 outside audit scope.** The premise "autonomous decision-making is allowed by the agent *because #1 exists and covers for audits*" doesn't hold. If Layer 2 changes candidate rankings (it does — that's its job), it is inside the scope of NYC LL144 (it "substantially assists" the decision), the EU AI Act (recruitment *ranking* is high-risk, whichever layer does it), GDPR Art. 22 and EEOC disparate-impact analysis. **The regulators audit the system's output, not your favorite module.** So we keep the two layers — but for the *right* reasons: Layer 1 gives you a reproducible, rule-based eligibility record; Layer 2 gives you contextual intelligence. Both are logged, evidence-cited, bias-monitored, and covered by the bias audit. The design below makes Layer 2 *auditable by construction* (bounded adjustments, declared signals, evidence per adjustment) rather than pretending it's out of scope.

**Correction 2 — "the agent visits LinkedIn" cannot ship as described.** Automated collection from LinkedIn violates LinkedIn's User Agreement (the hiQ litigation ended with the scraper losing on breach-of-contract), and third-party profile hunting raises GDPR Art. 14 notice duties, DPDP obligations, wrong-person risk, and — if done via a third-party screening service in the US — FCRA consumer-report rules (consent + adverse-action process). There's also a quieter fairness problem: not everyone has a public web presence, and presence correlates with age, gender (in some fields) and socioeconomics. **The compliant version, which we adopt:** the agent may enrich only from **candidate-provided links** (GitHub, portfolio, publications, LinkedIn URL *they* listed on the resume or application form — production adds an explicit ATS application field for this), via official/ToS-compliant access, identity-checked, **additive-only**: public work can add positive evidence; **absence of a web presence can never count against anyone.** All enrichment is disclosed in the candidate notice.

**Correction 3 — raw reinforcement learning on hiring outcomes is the Amazon failure mode.** Amazon's experimental recruiting model famously learned to penalize the word "women's" because it was trained on historical hiring outcomes. Learning directly from recruiter decisions or from "who we hired" imports every historical bias, and the EU AI Act's data-governance article explicitly requires bias examination of training data. **What we build instead — governed learning loops:** capture rich preference data from day 1 (agent suggestion vs. human decision + override reason — the PoC UI does this); learn **offline and periodically** (recalibrate weights/thresholds, refine rubric templates and agent prompts); pass every candidate change through an **evaluation harness** (accuracy vs. a human panel, four-fifths adverse-impact gate, counterfactual proxy tests); **human approval → versioned release**. Never live self-modification. Your "great selections" idea survives in two safe forms (§7): exemplars refine *rubrics* and power a *regression eval set* — they are never used as a similarity target for candidates, because "find people like our current staff" is a homogeneity engine and a legal trap.

Everything else in your brief — per-JD rubric with human edit, two layers, bias critic, ATS flexibility, a UX — is adopted directly.

---

## 2. The architecture in one paragraph

Resumes and JDs enter through an **ATS port** (local folder today; iCIMS/Greenhouse/Lever adapters later) and are normalized, extracted (Document Intelligence) and **redacted**. A **Rubric Agent** drafts a per-JD rubric that a **human reviews and edits in the UI** before it's versioned and used. **Layer 1** applies hard filters, the must-have gate and weighted rubric coverage as **pure, versioned code over frozen, evidence-verified findings** — every result reproducible on demand: this is your audit answer. Gate-passers flow to **Layer 2**, a recruiter-judgment agent that reasons over full context and applies **only signals from an approved Signal Registry**, as **bounded score adjustments** (per-signal and total caps), each with evidence and rationale — autonomy over *process*, governance over *criteria*. A **Bias Critic agent** reviews every Layer-2 rationale and can suspend adjustments; pool-level four-fifths monitoring runs on separately-held EEO data. A **human reviews the ranked, evidence-rich shortlist in the UI** and decides; every decision and override reason lands in the **feedback log**, which feeds the **governed learning loop**. Confirmed outcomes write back through the ATS port. Nothing auto-rejects, ever.

---

## 3. Layer 1 — Deterministic screening (the audit record)

**What it is.** Hard filters (work authorization, mandatory licences), the must-have gate, and a weighted rubric-coverage score — computed by **plain, versioned code** with no model call at decision time.

**The honest nuance (say this in audits, not a glossier version):** requirement findings ("does this resume evidence Python?") are produced upstream by an LLM extraction step. We make that safe and audit-proof by treating extraction as *measurement*: the extracted `ResumeProfile` and per-requirement `Findings` (each with a verbatim quote that the code verifies is really in the resume) are **frozen, stored artifacts**. Layer 1's decision logic is a pure function over those artifacts — so any past decision is **replayable exactly** from what's on disk: rubric vN + stored findings → same gate result, same base score, every time. "Deterministic given recorded measurements" is a claim that survives cross-examination; "no AI involved" would not.

**Behavioral rules:** a failed hard filter **flags, never silently drops**; non-passers stay on the list with their gate reasons; the base score is 0–100 from normalized rubric weights (full=1.0, partial=0.5, none=0).

---

## 4. Layer 2 — Agentic contextual judgment (bounded autonomy)

**The design key: autonomy over process, governance over criteria.** The agent freely decides *how* to investigate a candidate — which parts of the resume to interrogate, whether to open a candidate-provided GitHub, what to weigh in its narrative. It does **not** get to invent *what counts*: it may apply only signals from the **Signal Registry** (§5), and its effect on the score is **bounded** — each signal has a cap (e.g., ±5 points) and the total adjustment is capped (e.g., ±15) on top of the Layer-1 base. Layer 2 re-ranks within the eligible pool; it cannot rescue a gate failure or bury a strong Layer-1 result. Every adjustment is emitted as `(signal_id, direction, points, evidence, rationale)` — which is what makes an *agentic* layer auditable.

**Scope discipline:** Layer 2 runs only on gate-passers (saves ~40–60% of agent cost and focuses judgment where it matters). If the agent believes a *new* signal is warranted ("this JD implies X"), it **proposes** it — logged for human review — and proceeds without it.

**PoC vs production:** in the PoC, Layer 2 is a single structured agent pass over the redacted profile + computed features (no external tools). Production adds the tool belt: candidate-provided-links enrichment (GitHub via official API, portfolio, publications), identity-verified, additive-only, disclosed. The tool interface is stubbed in the PoC so this is a plug-in, not a rewrite.

---

## 5. The Signal Registry — where "thinks like a recruiter" becomes governable

Every contextual judgment the agent may make is a **declared, versioned signal**: definition, direction, cap, job-relatedness rationale, jurisdictions where it's enabled, approver, and monitoring notes. This registry is *the* answer to "how do we let an agent be smart without letting it be arbitrary" — and it's what you'll show a regulator, a works council, or a candidate who asks "what do you consider?"

### 5.1 Example approved signals (your two, made compliant, plus more)

| Signal | What the agent checks | Direction / cap | Why it's defensible |
|---|---|---|---|
| `tenure_pattern_vs_stability` *(your "job hopper")* | Median stint over the last N **permanent** roles when the JD explicitly declares a continuity need — after the agent excludes explainable context (contracting, layoffs, acquisitions, internal moves) | − up to 5 | Job-related only when the role declares it; context-excused; **never** triggered by employment gaps (gap ≠ hopping); adverse-impact monitored |
| `preferred_domain_background` *(your "Thermo Fisher")* | Experience at organizations on a **configured, HR-owned list** of direct competitors / adjacent regulated-domain players | + up to 5 | Legitimate business preference when explicit, configured and disclosed — a *config value*, not agent whim; HR owns the list (note: poaching/non-solicit etiquette is an HR policy matter, not an engine matter) |
| `regulated_environment_exposure` | Evidence of work under ISO 13485 / FDA 21 CFR / CLIA / GxP etc. when the JD requires a regulated environment | + up to 5 | Directly job-related for diagnostics/life-sciences roles |
| `skill_recency` | Core must-have skill last evidenced ≥ N years ago | − up to 3 (on confidence, surfaced) | Recency of critical skills is job-related; shown as "verify in interview," not a character judgment |
| `scope_trajectory` | Growing scope/leadership when the JD is a leadership role (IC→lead→manager progression) | + up to 4 | Direct evidence for the seniority the role demands |
| `hands_on_currency` | Long pure-management stretch when the JD is a hands-on IC role | − up to 4 | Role-fit, evidence-based, symmetric of the above |
| `domain_transfer_distance` | Semantic closeness of the candidate's domain to the role's (e.g., pharma QC → diagnostics QC = near) | ± up to 6 | Embedding-based domain fit as a *declared* signal instead of an opaque blended term |
| `public_work_relevance` *(production)* | Candidate-provided GitHub/publications/portfolio contain work relevant to the role's problem space | + up to 5, **additive-only** | Candidate-supplied, verified, disclosed; absence is never penalized |
| `certification_currency` | A JD-required certification is present but lapsed | − up to 3 (flag) | Factual, job-related, easily verified by the human |
| `language_evidence` | Evidence of a JD-required working language | + up to 3 | Job-related where client-facing need is declared |
| `quota_attainment_evidence` *(Sales)* | Explicit, quantified performance the candidate states — quota %, rankings, President's Club, awards | + up to 5 | Candidate-asserted and quoted verbatim; surfaced as "verify at interview"; job-related for quota-carrying roles |
| `deal_profile_fit` *(Sales)* | Match between the sales motion sold (ticket size, cycle length, segment, channel vs. direct) and the JD's motion | ± up to 4 | Direct role-fit evidence for revenue roles |

**India Finance & Sales pilot flavor.** For the immediate requisitions the same registry applies with pilot-appropriate readings: `regulated_environment_exposure` reads on GST, TDS, Ind AS, Companies Act and SOX (for US-listed entities) for Finance roles; `certification_currency` covers CA/CS/ICWA, CPA/CFA/ACCA and NISM/AMFI/IRDAI licences where the JD requires them; `language_evidence` covers JD-required Indian regional languages for field sales; and `public_work_relevance` stays disabled (non-tech roles, and enrichment is a v1 item anyway). Per-jurisdiction *parameters* — e.g., what counts as a "short stint" for `tenure_pattern_vs_stability` in Indian sales versus Japanese market norms — come from the policy packs in §6A, not from editing the signal definition itself.

### 5.2 Signals we deliberately exclude (and the registry records *why*)

These are the traps an "autonomous recruiter brain" walks into; excluding them explicitly is part of the audit story: **overqualification / "flight risk"** (age proxy), **employment gaps** (caregiving/disability proxy — gaps are *never* scored), **current-employment status** (penalizing the unemployed is banned in several jurisdictions), **commute distance / address** (redlining proxy), **school prestige as such** (class/ethnicity proxy — specific accreditations required by the JD are fine), **salary history** (banned in many US states), **"culture fit" free-form vibes** (bias laundering), and **similarity to current employees** (homogeneity engine — see §7).

### 5.3 Lifecycle

Propose (human or agent) → job-relatedness rationale documented → Legal/HR review → enable per jurisdiction with caps → monitor (per-signal firing rates and adverse-impact contribution) → retire or retune via the governed learning loop. In the PoC the registry is a YAML file edited in the UI; in production it's a governed table with approvals.

---

## 6. Bias Critic agent + fairness monitors

Two complementary mechanisms, matching your #3:

**Per-candidate critic (PoC).** A second model pass reviews each Layer-2 output: does any rationale rest on an impermissible or proxy basis (§5.2), does the evidence actually support the adjustment, is an unregistered criterion smuggled into the reasoning? Outcomes: pass / flag (human sees a warning) / **suspend** (the adjustment is removed from the score and queued for human review). The critic never sees protected attributes — it polices *reasoning*, not demographics.

**Pool-level monitors (interface in PoC, live in production).** Four-fifths impact-ratio monitoring over selection/score distributions using **separately-held EEO data** (never visible to the scoring path); per-signal contribution analysis (which signals drive adverse impact); and **counterfactual probes** — perturb proxy features (e.g., graduation year, university name) on a sample and alert if scores move. These feed the annual independent bias audit (NYC LL144) and EU AI Act monitoring.

---

## 6A. Jurisdiction Policy Engine — comply locally, without over-applying globally

You're right that regulation is regional and that applying every rule everywhere over-constrains the system: NYC LL144's bias-audit-plus-notice regime doesn't bind a California requisition, California's automated-decision rules don't bind an NYC one, and none of them bind a Bengaluru sales role. The design answer is a **policy engine keyed on job location**, built as **floor + dial**:

**The floor (never varies, by design, not by law).** Human decision on every candidate, no auto-reject, redaction before scoring, verbatim-verified evidence, injection defense, full logging, and the §5.2 excluded-signals list. These stay global not because every jurisdiction demands them but because (a) they are the product's ethics and quality bar, (b) several are illegal *somewhere* you operate and indefensible everywhere, and (c) one consistent floor is what lets a single codebase pass any region's audit. The floor is not "over-filtering" — it's what makes the dial safe to turn.

**The dial (varies by policy pack).** Per-jurisdiction YAML packs (`policies/IN.yaml`, `SG`, `JP`, `EU`, `US-default`, `US-NYC`, `US-CA`, …) that overlay the base and control: which approved signals are **enabled** and with what **caps and parameters** (e.g., tenure norms: the "short stint" threshold for `tenure_pattern_vs_stability` differs between Indian sales culture and Japanese long-tenure norms — same signal, different calibration); the **obligations checklist** surfaced on each run (LL144 audit + 10-business-day notice → NYC only; EU AI Act logging/oversight → EU only; DPDP notice → India; Singapore's PDPA + TAFEP/Tripartite fair-employment guidance — which, notably, discourages collecting age/race/religion/marital status and thus aligns with our redaction floor; Japan's APPI care around sensitive personal data); **retention periods**; **monitoring category sets** (US EEOC race/ethnicity/sex categories vs. India voluntary gender/disability — caste data is never collected); and **data-residency notes**.

**Resolution rules.** The requisition's `job_location` selects the pack (pilot default: `IN`). Two caveats keep this honest: some regimes attach to the **candidate**, not the job — LL144 reaches NYC-resident candidates even on remote roles, and GDPR follows EU data subjects — so remote/multi-country requisitions resolve to the **union of triggered obligations** (a production feature; the PoC applies the job-location pack and prints this caveat in `run_meta`). And when packs conflict, the merge is always the **most-protective union for obligations** — the engine can relax *which signals apply*, never *the floor*, and tests enforce that an overlay cannot disable floor items.

**PoC slice.** A `policy.py` loader/merger; `screener run --jurisdiction IN` (default from config); the registry filtered through the effective policy; feature parameters (tenure thresholds) taken from the pack; the active jurisdiction + obligations checklist stamped into `run_meta.json` and shown as a badge in the UI. Obligations are *displayed as a checklist*, not enforced actions, in the PoC.

**One caveat to keep us honest:** the packs are engineering scaffolding, not legal advice — each region's contents must be validated by counsel before that region goes live, which folds neatly into the same post-PoC HR + Legal session as the signal review (one meeting covers both).

---

## 7. Learning loops and "training on great hires" — done right

**Captured from day 1 (PoC):** every shortlist review writes `(candidate, layer1, adjustments, critic flags, agent suggestion, human decision, override reason, versions, timestamp)` to the decision log. This is the asset; without it there is nothing to learn from later.

**Governed learning (production, offline, periodic):**
1. **Calibration** — refit band thresholds and rubric/signal weights against human decisions (simple, transparent models: logistic/isotonic — not black-box policy learning).
2. **Prompt & rubric refinement** — recurring override reasons ("it keeps missing equivalent certifications") become rubric-template or agent-prompt improvements.
3. **Release gates** — any change ships only if it beats the current version on the eval harness **and** passes fairness gates (four-fifths on backtests, counterfactual proxy tests) **and** a human approves. Versioned like code.

**"Great selections" (current strong employees), the two safe uses:**
- **Rubric refinement:** analyze what actually differentiates strong performers in a role family (with Legal in the room — performance data has its own biases) and feed that into *requirement definitions and weights*, which humans then approve. The exemplars shape the *criteria*, not the comparison.
- **Regression eval set:** anonymized resumes of known-strong past hires become a standing test: *"would the current system have shortlisted our best people?"* Every release must pass it. This is the highest-value, lowest-risk use of exemplar data.
- **Never:** "score candidates by similarity to current employees." That is how you automate yesterday's demographics — the exact Amazon failure, and indefensible under the EU AI Act's data-governance duty.

True RL (online policy optimization) stays out of scope until there's a large decision corpus and a mature fairness harness — and honestly, calibration + prompt refinement captures most of the value at far lower risk.

---

## 8. ATS abstraction — iCIMS today, Greenhouse/Lever tomorrow

Hexagonal (ports & adapters): the core never sees vendor shapes. One `ATSPort` interface — `fetch_requisitions()`, `fetch_candidates(req)`, `fetch_documents(candidate)`, `write_back(result)`, `on_stage_change(hook)` — with adapters selected purely by config (`ats.provider: local | icims | greenhouse | lever`). Vendor notes for later: **iCIMS** = REST API + assessment-partner framework (OAuth 2.0), results as score + result-URL + custom attributes; **Greenhouse** = Harvest API + webhooks, custom fields/activity feed; **Lever** = API + webhooks, notes/tags. Each adapter owns its vendor's write-back quirks; swapping ATS is a config change plus one adapter, zero core changes. The PoC ships `LocalFolderATS` (your OneDrive-downloaded folders) behind the same interface — which is precisely what makes the PoC non-throwaway.

---

## 9. The UX (PoC: Streamlit → production: web app)

A single Streamlit app in the PoC — very vibe-codable, single-user, local — with five views: **Rubrics** (generate from JD, edit criteria/must-haves/weights inline, approve → versioned — your #1), **Signals** (view/enable/adjust caps on the registry), **Run** (pick JD + folder, limit, progress), **Shortlist review** (ranked table; per candidate: Layer-1 gate + findings with quotes, each Layer-2 adjustment with signal/evidence/rationale, critic flags; **agree / override / re-rank + reason** — captured to the feedback log), and **Runs/audit** (run_meta, errors, cost). Production replaces the shell with FastAPI + a proper front end and multi-user auth — the review-and-feedback *data model* built in the PoC carries over unchanged.

---

## 10. Compliance delta (what changes vs. the earlier DPS)

The DPS posture (human-in-the-loop, redaction, logging, four-fifths monitoring, per-geography compliance packs) stands. New obligations created by this design: **Layer 2 and the critic are inside the bias-audit and AI Act scope** (log and disclose them as part of the tool); **enrichment requires candidate notice** and, if any third-party screening service is ever used, FCRA handling in the US; the **signal registry becomes a disclosure artifact** (a plain-language version can back the LL144 notice and works-council information duties); and the **learning loop is itself governed** (documented data governance, release gates, versioning — which the EU AI Act expects for high-risk systems that continue to learn). Finally, the **jurisdiction policy packs (§6A) are engineering scaffolding, not legal advice** — each region's pack must be validated by counsel before that region goes live, folded into the same post-PoC HR + Legal session as the signal review.

---

## 11. Phasing and cost

**PoC (now, vibe-coded):** everything solid in the diagram — ATS port with local adapter, extraction/redaction, rubric agent + rubric-edit UI, Layer 1, Layer 2 single-agent with the starter registry, per-candidate critic, reports, Streamlit UI with feedback capture, monitors' interfaces. LLM budget rises from ~4 to ~5–6 calls per resume (parse, findings, Layer 2 + critic on gate-passers, embeddings) — still modest at 500 resumes, and caching keeps re-runs near-free.
**v1 (pilot hardening):** FastAPI extraction, first real ATS adapter (iCIMS), EEO-data pipeline + live four-fifths monitor, candidate-provided-links tool, notice templates, shadow-mode validation per the DPS.
**Production:** remaining adapters, multi-user UI + SSO, Managed Identity, governed learning loop live, independent bias audit, per-geography compliance packs.

---

## 12. Decisions — resolved (5 Jul 2026)

1. **EEO/monitoring data: optional for the PoC.** ✔ The pool-level four-fifths monitor ships as an interface; the per-candidate critic and counterfactual probes work without it. Note for later: monitoring categories are jurisdictional (§6A) — US EEOC categories vs. India voluntary gender/disability (caste data is never collected).
2. **Preferred-background list: HR owns it — and HR is the primary operator.** ✔ The list is a config value in the registry; because recruiters run the system, all UI copy uses plain recruiter language (no engineering jargon), and the Signals page is written to be readable by HR as-is.
3. **Enrichment held for v1.** ✔ Doubly right given the pilot roles are **non-tech (Finance & Sales, India)** — `public_work_relevance` ships disabled; the tool interface stays stubbed.
4. **Streamlit confirmed** for the PoC UX. ✔ FastAPI + web front end remains the production path; the review/feedback data model carries over unchanged.
5. **HR + Legal review deferred until after the PoC.** ✔ Accepted — a working demo will make the conversation far easier — with **one safeguard**: until that review happens, PoC outputs are internal evaluation only and are **not used to progress or reject candidates on live requisitions**. Two artifacts make the eventual session a 30-minute show-and-tell rather than an abstract debate: the **Signals UI page** (the registry in plain language) and the **per-candidate audit note** (rubric version + findings + adjustments + critic verdicts + human decision).

**New inputs incorporated:**
- **Key Vault access model = Azure RBAC (confirmed).** Setup simplified: the admin only adds the two secrets and grants **Key Vault Secrets User** — no access-policy fallback needed.
- **Jurisdiction-aware compliance** (your requirement that NYC ≠ CA ≠ India ≠ SG ≠ JP ≠ EU): now a first-class **Jurisdiction Policy Engine** — see §6A. Pilot default pack: `IN`.
- **Pilot market = India, Finance & Sales:** registry seeded with the pilot flavor in §5.1; default jurisdiction `IN`; and one infrastructure observation — your Azure resources are in **eastus** while the first roles are India-based. DPDP currently permits cross-border processing (the US isn't restricted), so eastus is fine for the PoC; for production, plan an Indian region (e.g., Central India) for processing/storage as future-proofing — a redeploy, not a redesign.
