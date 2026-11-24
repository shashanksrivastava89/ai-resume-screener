# ADDENDUM 11 — Resume Screener v2: Requisition Workspace, Gates, HM Collaboration, Audit Depth, Preferences (P29–P49)

> **Status: COMPLETE — P29–P49 done (2026-07-12/13). The v2 requisition-workspace
> program is fully delivered (P49 was the optional DB-backend flip). Details below:
> P29–P36 done (2026-07-12: req sidecar + lifecycle + event
> spine + regionmap; Vitest+RTL harness; requisition workspace at /reqs/:id with the
> 6 tabs; IA CUTOVER — Requisitions table + History + New requisition with required
> iCIMS id + /api/locations, old pages deleted; HARD GATES — single-list rubric
> contract (HardFilter deleted, kind=hard_gate, gate_flags), three-state checklist
> editor, gate-aware Review + "… match" band-label sweep; REVIEW VELOCITY —
> /decisions/batch (all-or-nothing), reason storage cleaned to pure free text
> (action = human_action+final_band), notes on rows/drawer/audit, inline PDF
> preview, hidden j/k/o/1-3/esc shortcuts, real sort menu; HONEST RUNS —
> screen/preflight change detection (never_run/config_changed/files_changed/
> nothing_changed/empty_batch) + the change-aware Candidates tab with per-file
> cached/new/updated tags and magic-only-when-will_run; SIX-ROLE RBAC —
> +HiringManager (hm_take) +SuperAdmin (flags_edit) + caps prefs_own/prefs_approve/
> flags_view/flags_edit/hm_take, capability UNION across held roles
> (get_identity_full, comma DEV_ROLE), require_any(), /api/me roles[], flags_view is
> an admin cap; FEATURE FLAGS — file-first signals/features.yaml +
> feature_flags.py (flag_enabled/catalog/set_flag; file seeds all keys, DB wins
> per key; env-controlled flags e.g. hm_portal read FEATURE_HM_PORTAL only),
> capability suppression (rbac.effective_capabilities strips hm_take when hm_portal
> off), GET /api/features + PUT /api/flags/{key} [flags_edit=SuperAdmin] + flag-gated
> reopen/clone routes, Governance→Feature flags page, History Reopen/Clone/close-sync;
> REGION LENS — service.accessible_regions (None=unrestricted, wraps region_context)
> + region_of_req; _req_authorized(req_id, ident) is the single per-req chokepoint
> (404 then region 403 "…a region you don't have access to."); GET /api/roles?region=
> filters to lens ∩ grant; feature gating standardized on flag_enabled(key,
> region_of_req(req)); RegionPicker on the user chip (localStorage lens → ?region=,
> HIDDEN when me.regions unseeded); regionmap↔db.seed sync test; tests/conftest.py
> pins the ambient DB to sqlite:// so the offline suite degrades like CI;
> HR PREFERENCES — prefcheck.classify_text (deterministic ok/warn/blocked, reuses
> critic._CODE_SCREEN_PATTERNS read-only, severest wins, pack-exclusion union) +
> preferences.py (event-sourced signals/preferences.jsonl: add/approve/decline/
> withdraw, ok→active/warn→pending/blocked→blocked, 6-month re-review expiry →
> display "rereview", +2 cap, pref_* events) + /api/preferences {check,list,add,
> withdraw,pending,approve,decline} [prefs_own / prefs_approve] + /api/me pref_pending
> badge; MyPreferences + PreferenceApprovals pages + nav (badge); NO scoring change
> (that's P40) [412 py + 60 web tests]); PREFERENCE SCORING STAGE — PreferenceAdjustment
> model + ScoreResult.preference_adjustments (additive, [] default → no-prefs run is
> model_dump-identical); scoring.assemble_score folds pref_net (each clamped ±2) into
> the SAME single 0-100 clamp (base + l2_net + pref_net), never touches eligibility/
> gates/must-have band cap; new pref_match.py (one structured call/gate-passer, evidence
> substring-verified, prefcheck re-classify drops since-blocked, JsonCache w/
> PREFS_PROMPT_VERSION); cli run resolves whose prefs (sidecar created_by + hm.email,
> active/approved only) + --no-prefs + run_meta versions.preferences{applied,prompt} +
> counts.preference_adjustments; report pref_total column (adj_total stays L2-only);
> review drawer pref_notes [412→426 py + 60 web tests]); HM PORTAL BACKEND — whole
> surface flag-gated on hm_portal (_hm_portal_or_403/_assigned_hm, defense-in-depth
> over P37 capability suppression); new suggestions.py (jds/<stem>.suggestions.jsonl,
> latest-per-id) + hm_takes.py (output/feedback/hm_takes.jsonl, re-take supersedes per
> run+cand+hm, NEVER writes decisions.jsonl) + notify.py stub (emits `notify` event);
> PUT /roles/{id}/hm assigns [recruiter]; VIEW routes (list/detail/rubric GET/review)
> moved to viewer_identity=require_any(recruiter,hm_take) + _req_view_authorized (HM-only
> sees only assigned reqs); HM mutations POST suggestions [assigned-HM] + accept/decline
> [recruiter] + takes + takes/send [assigned-HM] + GET activity (dual-tz rail) +
> request-hm-review [recruiter]; review cards+detail gain hm_take{take,hm_name}; metrics
> awaiting_hm from assigned hm; FE UNCHANGED (P42 owns it) [426→447 py + 60 web tests]);
> HM PORTAL FRONTEND — dark-launched via capabilities (isHmActive = hm_take & !recruiter;
> web/src/hm.ts). New GET /roles/{id}/suggestions (open only, flag+view-scoped; the sole
> backend add) + request-hm-review now stamps hm.status="review_requested" for the card.
> web: hm.ts, time.ts (dualStamp), useFeatureFlag hook; components ActivityRail /
> SuggestComposer / SuggestionCard / HmTakeButtons; ChecklistTab HM suggest-mode (read-only
> rows + Suggest edit + "waiting for TA" + rail) vs TA editor + inline SuggestionCards
> (accept merges into draft, publish unchanged) + Request-HM-review; ReviewTab/Drawer HM
> take variant (Strong yes/Yes/No, bulk takes, Send-takes footer, résumé file hidden, "TA
> decision" line) + TA "HM · <take>" chip; Requisitions HM list variant (My/All/History,
> no metrics/create, Recruiter column) + flag-off notice; Overview HM assign/edit card;
> hm-variants.test.tsx [447→450 py + 60→65 web tests]);
> CHECKLIST-CRITIC GOVERNANCE — requirement TEXT runs the SAME deterministic critic as
> prefs (P39 prefcheck.classify_text, extra_exclusions=pack union): BLOCKED row → 400
> "Cannot add this requirement — it references a protected attribute ({reason})…" with
> NO version bump/file; WARN row → saved with additive Requirement.critic_level="warn" +
> response needs_approval. New checklist_approval.py (jds/<stem>.approval.json {version,
> status none|pending|approved, flagged_requirement_ids, requested/approved by/at};
> record_publish resets on each save, request_approval [recruiter]→pending, approve
> [rules_edit]→approved; file_lock+atomic). rubric GET carries approval{...} while the
> latest version has warn rows & status≠approved + per-row critic_reason (recomputed);
> save route emits a checklist_publish event every publish (feeds P41 rail + P45 rules
> scope). Routes POST /checklist-approval/request|approve. FE: ChecklistEditor amber
> row + reason + "Needs HR Head approval" chip; ChecklistTab save stays on the tab when
> needs_approval, "Send v{N} for HR Head approval" (recruiter) / "Approve v{N}" (HR Head)
> buttons + banner; checklist-critic.test.tsx [450→456 py + 65→70 web tests]);
> ASK-CLAUDE HANDOFF — server-side redacted bundle. New api/handoff.py build_bundle(req,
> blind) → markdown (title/req_code/place/law, checklist w/ [Must]/[Gate] markers, ranked
> band/gate/score table, per-candidate strengths/watch-outs + evidence quotes, decision
> counts). Redacted BY CONSTRUCTION — built ONLY from review records (evidence already
> from the redacted profile); never touches the private map's paths/ids, so no contact/
> filenames/emails/16-hex ids; blind → "Candidate N" by rank. POST /api/roles/{id}/handoff
> {blind} [viewer_identity + _req_view_authorized: recruiter OR assigned-HM] emits
> handoff_copied event atomically. FE AskClaudePanel opened from the workspace header on
> the Review tab: explainer, 3 copy-append starter chips, Copy context (POST → clipboard),
> Open claude.ai, Phase-2 MCP footer (ask_claude_mcp flag stays off); test_handoff.py +
> ask-claude.test.tsx [456→459 py + 70→71 web tests]);
> **P45 done (2026-07-13):** RETENTION KEEP/PURGE MODEL + AUDIT EXPLORER BACKEND (VISIBILITY
> ONLY — nothing deletes). GREENFIELD REPLACEMENT: PolicyPack/EffectivePolicy DROP
> retention_days → keep_at_least_days (audit floor) + purge_by_days (PII purge), threaded
> through all 8 packs (IN keep 1095/purge 365), policy merge, governance.publish_pack (both
> editable), DB model+repo+migration f6a1c3d9e2b4, run_meta stamp, /api/jurisdictions (+own_*),
> /api/locations, service.retention_for ({keep_at_least_days, purge_by_days, purge_by, purged}
> — History column reads purge_by+purged, unchanged), CLI, FE types + NewRequisition +
> Jurisdictions editor (no dual reads). New retention.py: purge_queue() rows {req_id,title,
> jurisdiction,pack,closed_at,last_run_at,purge_due,keep_audit_until,status
> scheduled|on_hold|awaiting_close,hold?} + signals/legal_holds.json (file_lock+atomic);
> apply_hold/release_hold emit legal_hold_applied/released. New api/explorer.py (APIRouter,
> included in app before SPA catch-all; guards reuse app._runs_audit/_req_authorized lazily):
> GET /api/audit/feed?q&kind (decisions verbatim + governance + events + run starts; chips
> Decision|Preference|Rules|Close|Data|Views; audit_view EXCLUDED unless kind=Views),
> /candidates?q (name search over private maps), /candidates/{req}/{cid}/timeline (Screened→
> HM take→Decision verbatim→Handed-off + targets audit_pack_url/checklist/jd_ref/run_id),
> /requisitions?req (event mini-timelines), /rules (governance + checklist_publish +
> pref_approve/decline), /retention (purge_queue + per-jurisdiction summary strings) +
> POST /retention/{id}/hold|release, /reports (catalog incl. adverse_impact Phase-2 stub) +
> /reports/{key}.csv (throughput_by_region|decision_mix_by_band|time_to_shortlist). Every
> handler emits audit_view + accepts region= (P38 enforce + filter). test_retention.py (6) +
> test_explorer.py (11) + existing policy/us_packs/lifecycle/repo tests migrated to keep/purge
> [459→476 py, 71 web]; green on SQLite AND real Postgres; migration chain applies clean;
> **P46 done (2026-07-13):** AUDIT EXPLORER FRONTEND + "Audit & History" nav swap (FE-only, no
> Python change). New web/src/pages/AuditExplorer.tsx at /audit (runs_audit-gated via
> RequireCap): search-first header + 5 tabs (All/Candidates/Requisitions/Rules & criteria/PII &
> retention) + the region lens (useRegionLens) threaded as region= into every fetch. Six new
> components/audit/*: EventFeed (merged feed, kind chips All+Decision|Preference|Rules|Close|
> Data|Views, server kind+region, client-side q filter), CandidateTimeline (name search →
> Screened→HM take→Decision-verbatim→Handed-off + Export audit pack (md download)/PDF
> (window.print)/View checklist·JD·run links; dual-tz), ReqAuditCards (event mini-timelines,
> ?req= deep link narrows + highlights via data-req), RulesLog (rationale quotes), RetentionPanel
> (purge-queue rows + Apply/Release legal hold POST + jurisdiction summaries; "nothing deleted"
> callout), ReportsRail (3 CSV downloads + print + adverse-impact Phase-2 chip). Nav: AppShell
> "Audit & History" → /audit; App.tsx /audit route + /runs→/audit redirect; RunsAudit.tsx DELETED.
> Requisitions History "View audit" now deep-links to /audit?req={id}. NewRequisition shows the
> TWO retention lines (keep-at-least + purge-by). types.ts gained the explorer shapes. Tests:
> web audit-explorer.test.tsx (4 — tabs+feed chips, timeline verbatim reason, retention hold
> POST, ?req= deep link) + new-requisition.test.tsx updated to the two-line copy [71→75 web];
> pytest unchanged 476; build green;
> **P47 done (2026-07-13):** GOVERNANCE POLISH — the Data page + the rules-publish dialog.
> New `datamgmt.py` (delete_cache; delete_candidate_data — now also clears the per-role batches
> data/roles/*/resumes/ + stray .lock sidecars, keeps output/feedback + dot-files; storage_map =
> §A1 rows with DATA_ROOT-resolved absolute paths + recursive sizes; storage_root). Three
> data_manage-gated routes in app.py: GET /api/data/summary, POST /api/data/delete-cache
> (phrase "delete cache"), POST /api/data/delete-candidates (phrase "delete all candidate data")
> — 400 on phrase mismatch, each doubly recording a data_deleted EVENT + a data_delete GOVERNANCE
> audit entry, one-sentence consequence responses. New web/src/pages/DataPage.tsx at /data (nav
> "Data" under Governance, RequireCap data_manage): §A1 storage table + two typed-confirm danger
> cards + "decision log is kept — it is the audit trail" callout. Rules.tsx paper cut: window.prompt
> publish → a proper <PublishDialog> showing a DIFF SUMMARY (turned on/off + cap moves) + a required
> rationale textarea (same PUT /api/signals underneath). 09-REACT-FASTAPI-UI.md Part F "Data page
> never ported" gap struck. Tests: test_datamgmt.py (5 — engine cache/candidate/storage + API
> summary/deletes/RBAC) [476→481 py]; web datamgmt.test.tsx (2 — Data typed-confirm delete +
> Rules diff-dialog rationale gate) [75→77 web]; green on SQLite AND real Postgres; build green;
> **P48 done (2026-07-13):** DOCS — CLAUDE.md gained the FOURTH architecture banner (requisition-
> centric IA, six-role capability union + dark-launched HM portal, hard-gate kind, HR preferences
> ±2 stage, keep/purge retention, audit explorer, feature flags, region lens) + refreshed §3 tree
> (events/regionmap/prefcheck/preferences/pref_match/retention/datamgmt/suggestions/hm_takes/
> checklist_approval/feature_flags + api/explorer + api/handoff + db/ + web/), §4 contracts
> (single-list Rubric kind incl. hard_gate — HardFilter deleted; PolicyPack keep_at_least_days/
> purge_by_days; Preference/PreferenceAdjustment + ScoreResult.preference_adjustments;
> Requisition.icims_req_id; Decision.reason = pure free text; Layer1Result.gate_flags), §7
> (v2 nav/pages/six roles union), §8 (DEV_ROLE comma list + FEATURE_HM_PORTAL + .req.json + flags/
> region). README rewritten to the requisition workspace + preferences/approvals + audit explorer +
> the six-role RBAC table. Addendum 08 Part B already carries the two new app roles + FEATURE_HM_PORTAL.
> .env.example gained the DEV_ROLE comma-list note + FEATURE_HM_PORTAL=0. Docs-only; suites unchanged
> (481 py + 77 web green).
> **P49 done (2026-07-13):** DB BACKENDS for the new v2 domains (the optional flip; file defaults
> stay so zero-DB behavior is byte-identical). One migration `a1b2c3d4e5f6` (down_rev f6a1c3d9e2b4):
> DROPS the speculative hr_preference_set/_override, CREATES hr_preference (folded latest state —
> pref_id/owner_email/text/status/classification JSON/cap_points/approved_by+at/review_due/…),
> app_event (append-only spine mirror, monotonic id = order), hm_take (FKs requisition+app_user +
> soft req_code/hm_email), checklist_approval + legal_hold (ideal-schema scaffolding — tables only,
> no switch/importer yet), and adds feature.default_enabled (flags' global-default home). ORM:
> models/preference.py reshaped to HrPreference, new event.py + collaboration.py, Feature.default_
> enabled, __init__ updated (28 tables now). Repos db/repositories/{preferences,events,hm_takes,
> flags}.py — storage primitives only; ALL lifecycle logic stays in the file modules. Backend
> switches in config.DbSettings, each default file: PREFS_BACKEND (jsonl|db), EVENTS_BACKEND
> (jsonl|db), HM_TAKES_BACKEND (jsonl|db), FLAGS_BACKEND (yaml|db). File-store dispatch:
> preferences.py `_append`/`_fold`, events.py emit/load, hm_takes.py record/_fold each gain a
> `_use_db()` branch (monkeypatchable, mirrors the feedback/governance Phase-2/3 pattern);
> feature_flags.py gains `_use_flags_db()` — catalog() takes the DB default when db, set_flag upserts
> feature/region_feature (shared `_audit_flag_change`), new file_catalog() import source;
> service.features_catalog() now returns `default`. Importers import_preferences/events/hm_takes/flags
> + import_requisition_contacts (.req.json hm→hiring_manager, created_by→recruiter; new
> requisitions_repo.upsert_contact/get_requisition_row) all in import_all; db import hint +
> `screener check` DB probe now verify the five v2 tables. Tests: test_v2_domains_repo.py (6 — per-
> domain parity file↔db + import idempotence, dbharness SQLite); test_db_schema.py 25→28 tables;
> test_runs_repo import_all set updated [481→487 py, 77 web unchanged]; green on SQLite AND real
> Postgres; migration applies clean up+down+re-up on a fresh PG. Hosted
> P36/P37 rollout blocked on Cloud Admin Part B (the two Entra app roles + FEATURE_HM_PORTAL env).**
> Branch: `feature/v2-requisition-workspace`
> (cut from `feature/hosting-sso-rbac`, 2026-07-12). Spec sources: the **12 Jul 2026 Design
> Review** and the **Resume Screener v2 working prototype** in the Claude Design project
> "Atomic design project improvement" ((internal design artifact, not published)
> — frames 1a–1e, 2a, 2b + the interactive prototype). Where the review doc and the prototype
> disagree, **the prototype wins** (user decision — it is the later iteration; the known case is
> HR preferences: user-scoped, clean-prefs-active-immediately, 6-month re-review).
> Each P-prompt below is sized for ONE focused agent session with only `CLAUDE.md` + this
> addendum as context. Update this banner as prompts land.

---

## Part A — What v2 is (read once, before any prompt)

**Verdict of the design review:** the product interior is right and stays — the evidence-first
candidate drawer, the decision triad with re-click-to-clear and required reasons, blind review,
the ✨ marker for AI actions, the plain-language checklist editor, the guardrail copy
("It suggests — **you** decide"). What changes is the **container** and the concerns that only
appear at production scale.

**A1 — The one structural change: requisition-centric IA.** Screen/Review stop being top-level
pages with role pickers. One **Requisitions** list (dense, sortable, saved views, ~150 active
reqs across regions) → one **workspace per requisition** at a shareable URL with tabs
**Overview · Checklist · Candidates · Review · Shortlist · Audit**. The sidebar shrinks to two
zones: Requisitions + Governance. Page interiors move into tabs mostly as-is.

**A2 — Production concerns designed in:** hard gates first-class and never silent (three-state
requirements; gate-missed candidates stay listed, tagged, overridable with a reason); requisition
lifecycle end (suggested close → human confirms → History, read-only, retention clock);
a real **Hiring Manager** SSO role (suggest-mode checklist edits, "takes" — input, never
decisions); a search-first **Audit & History explorer** (candidate timelines with verbatim
reasons, typed scopes, reports, retention/purge visibility + legal hold); **iCIMS Req ID**
required at creation (the mapping anchor; lifecycle APIs are later, feature-flagged);
**Ask Claude** as a copy-out of a redacted context bundle (audit-logged; MCP connector is
phase 2); review velocity at 500+ résumés (one batch decision call, select-all, inline résumé
preview, hidden keyboard shortcuts); **score de-emphasis** (band + gates lead; the number is
small and neutral); **honest screening runs** ("Proceed to review" when nothing changed; only
new/updated files re-screen); **HR preferences** that nudge ranking within ±2 points under
bias-critic guardrails and HR-Head approval — never a gate, never hiding anyone.

**A3 — Mandates for every executing session:**

1. **Greenfield mandate (user-directed): NO backward-compatibility compromises.** The project
   is greenlit for production and has no users yet; every rubric/decision/output file on disk is
   disposable test data. Build contracts the IDEAL way even when that means refactoring engine
   code, deleting legacy routes outright, or regenerating test data. Do not add compat shims,
   optional-field hedges, dual-format readers, or legacy fallbacks. Backward compatibility
   becomes a constraint when the first real user lands — not now.
2. **Greenfield ≠ sloppy.** Every prompt ends runnable (CLAUDE.md build rule). All §6 guardrails
   keep their tests green: floor protection, evidence-or-nothing, no auto-decisions, everyone
   stays listed, redaction before scoring, no excluded signals.
3. **Deliberate engine-change inventory — everything else in the engine is out of bounds:**
   P33 (requirement-kind unification: models/layer1/findings/rubric — behavior-preserving),
   P40 (the bounded preference stage in scoring), P45 (retention fields on PolicyPack).
   Layer 2, critic, redaction, feedback contracts, policy floor protection: untouched throughout.
4. **Files-first persistence** for every new domain: JSON/JSONL under DATA_ROOT-relative paths,
   `locking.file_lock` on writes, write-temp-then-`os.replace`, corrupt lines skipped on read,
   real SSO email + UTC ISO-8601 timestamps stamped on every record. The DB flip is P49, later.
5. **Every new API route carries a capability dependency** (`require(...)`/`require_any(...)`),
   plain-language 403 messages. **Plain recruiter copy** everywhere (Addendum 04 §B3: no signal
   ids, layer numbers, weight decimals, candidate hashes, YAML/JSON on screen). Reuse
   `web/src/components/ui.tsx` primitives; no new component libraries; green = emphasis only.
6. Timestamps: stored UTC, rendered in the viewer's timezone, requisition-local alongside when
   they differ (IANA zones from `regionmap.py`).

---

## Part B — Who does what (Cloud Admin actions, extends Addendum 08 Part B)

The app-side prompts are self-sufficient locally (`DEV_USER`/`DEV_ROLE`). Before hosted rollout
of P36+ the Cloud Admin must, in Entra ID → App registrations → (the Easy-Auth app) → App roles:

1. Create two additional app roles (value = same string): **`HiringManager`**, **`SuperAdmin`**
   — alongside the existing HRHead / IT / TASpecialist / HRSpecialist. Then Enterprise
   applications → the app → Users and groups → assign people. A user may hold several roles
   (capabilities are the union from P36 on).
2. Container App → Environment variables: add **`FEATURE_HM_PORTAL`** (unset/`0` = HM portal
   dark — the shipped default). Only deployment owners can flip it; it is deliberately NOT
   editable from any in-app page, including the Super Admin's Feature flags page.
3. No other infra changes: same image, same Azure Files mount, same Key Vault path.

---

## Part C — The prompts

Run in order unless the dependency table says otherwise. Standard gate for every prompt:
`uv run pytest` green (254 existing + additions; P40 additionally requires the 254 to pass
UNMODIFIED) · `cd web && npm run build` green · `npm run test` green once P30 lands · then Stop.

| # | Title | Size | Depends on |
|---|---|---|---|
| P29 | Requisition anchor + lifecycle + event spine (backend only) | L | — |
| P30 | Vitest + RTL harness | S/M | — |
| P31 | Requisition workspace `/reqs/:id` + 6 tabs | XL | P29 |
| P32 | Requisitions list + History + New requisition + IA cutover | XL | P31 |
| P33 | Hard gates: unified single-list rubric contract + gate-aware Review | L | P31 |
| P34 | Review velocity: batch decisions, reason cleanup, inline preview, shortcuts | L | P31 |
| P35 | Honest runs: preflight + change-aware Candidates tab | M | P31 |
| P36 | Six-role RBAC + capability union (HiringManager, SuperAdmin) | M | P29 |
| P37 | Feature flags (file-first) + SuperAdmin page + iCIMS Reopen/Clone stubs | M | P36 |
| P38 | Region lens: picker + hard per-region filtering | M | P36 |
| P39 | HR preferences: store + deterministic critic check + approval queue | L | P36 |
| P40 | Preference scoring stage (engine — highest care) | L | P39 |
| P41 | HM portal backend: assignment, suggestions, takes, activity | L | P29, P36, P37 |
| P42 | HM portal frontend: HM variants + TA-side cards (dark-launched) | L | P41, P32 |
| P43 | Checklist-critic governance + approval state | M | P39 |
| P44 | Ask Claude handoff (server-side redacted bundle) | S/M | P31, P36 |
| P45 | Retention model (keep/purge) + Audit Explorer backend | L | P29, P38 |
| P46 | Audit Explorer frontend + "Audit & History" nav swap | M/L | P45 |
| P47 | Governance polish: Data page + rules-publish dialog | M | P36 |
| P48 | Docs: README + CLAUDE.md v2 banner + addendum status | S | all |
| P49 | (Optional, deferrable) DB backends for the new v2 domains | L | P29–P47 |

### P29 — Requisition anchor + lifecycle + event spine (backend only)

```
Backend foundation for the requisition-centric v2, per Addendum 11 Part A/D. NO frontend changes;
the current React app must keep working unchanged this session.
1. ONE consolidated requisition sidecar (src/resume_screener/ats/local_folder.py):
   jds/<stem>.req.json REPLACES jds/<stem>.location.json (greenfield: delete the .location.json
   read/write helpers and the committed test sidecars; job_location now lives here). Helpers:
   req_meta_path(jd_path); load_req_meta(jd_path) -> dict (missing/corrupt -> {});
   save_req_meta(jd_path, **updates) = read-merge-write under locking.file_lock on a sidecar
   .lock, atomic replace, PRESERVING unknown keys (later prompts add fields). Schema v1 (Part D1):
   {schema: 1, icims_req_id, job_location, status: "active"|"closed",
    outcome: {kind: "hired"|"cancelled", hired_count?} | null, closed_at, closed_by,
    snooze_until, created_by, created_at, jd_language, hm: null}.
   models.py: Requisition gains icims_req_id: str | None (adapter fills it; creation REQUIRES it
   from P32 on). Lifecycle/status/hm never enter the ATS Pydantic contract — closing is
   "this workspace only". Existing test roles: recreate them, or add a throwaway hidden
   `screener debug migrate-sidecars` that folds .location.json into .req.json — author's choice,
   the data is disposable.
2. Event spine (new src/resume_screener/events.py, contract in Part D2):
   emit_event(kind, *, actor, actor_role="", req_id=None, candidate_id=None, summary="",
   data=None) appends one JSON line {ts (UTC ISO), kind, actor, actor_role, req_id?,
   candidate_id?, summary, data{}} to signals/events.jsonl under file_lock;
   load_events(req_id=None, kinds=None) skips corrupt lines. The governance audit_log.jsonl
   stays reserved for config publishes — req/app events do NOT enter it (governance.
   review_pending() must be unaffected; add a regression test). service.record_req_event(req_id,
   actor, action, summary, rationale="") delegates to emit_event.
3. Region constants (new src/resume_screener/regionmap.py, Part D4): REGION_OF_JURISDICTION
   (IN->IN, SG->APAC, JP->APAC, EU->EMEA, US-default/US-NYC/US-CA->AMER),
   TZ_OF_JURISDICTION (IN Asia/Kolkata, SG Asia/Singapore, JP Asia/Tokyo, EU Europe/Brussels,
   US-NYC America/New_York, US-CA America/Los_Angeles, US-default America/Chicago),
   region_for_jurisdiction(code) with the same US- fallback logic as policy._resolve_pack_path.
4. service.py helpers: req_lifecycle(req) (status/outcome/closed_at/snooze_until/created_by,
   defaults for sidecar-less test roles); last_activity_utc(req) = max(run_meta started, latest
   decision ts); close_suggestion(req) -> {suggest, decided, quiet_days}: true iff status=active
   AND a run exists AND every scored candidate has a decision AND last activity >= 14 days AND
   (snooze_until absent or past); jd_language heuristic (share of non-Latin letters in the JD
   body > 0.2 -> "non_english" else null; stored at create); retention_for(req) ->
   {retention_days, purge_by, purged} from the effective policy + closed_at (DISPLAY ONLY —
   nothing deletes; P45 replaces the internals). Role payloads gain keys: req_code
   (icims_req_id), lifecycle{...}, last_activity_utc, suggest_close, jd_language, hm (null now),
   created_by, retention (closed rows), region (via regionmap). Metrics gain need_action,
   awaiting_hm (0 until P41), suggested_close, history_count.
5. Routes (api/app.py, guards in brackets):
   GET  /api/roles/{req_id}/quickview [recruiter] -> {jd_excerpt (~300 chars), checklist_version,
        requirement_count, screened_count, added_by, icims_req_id}
   POST /api/roles/{req_id}/close [recruiter] {outcome: "hired"|"cancelled", hired_count?} ->
        400 if already closed; sidecar status/outcome/closed_at/closed_by=SSO email + req_close
        event. POST /api/roles/{req_id}/reopen [recruiter] -> active again + req_reopen event
        (endpoint ships now; UI button stays disabled until P37 flag-gates it).
   POST /api/roles/{req_id}/snooze [recruiter] {days=30} -> snooze_until + req_snooze event.
   POST /api/roles/{req_id}/events [recruiter] — FE-fired events, ALLOWLIST {"req_history_view"}.
   GET  /api/roles/{req_id}/trail [recruiter] -> {entries[{ts, kind, actor, summary}] newest
        first, composed from: sidecar created entry + jds/<stem>.rubric.v*.yaml versions + events
        with this req_id + a run entry from the latest run_meta (run_id, pack, rubric v);
        decision_summary {forward, discuss, not, quotes[{name, action, reason, reviewer, ts}]};
        footer note}. Recruiter language, no hashes. Pre-P29 runs appear as one "latest run" line
        (honesty footnote, don't fabricate history).
   POST /api/roles now also stamps created_by/created_at/job_location/jd_language into the
        sidecar and 409s when another role already claims the same icims_req_id (the REQUIRED
        validation flips in P32 with the new page).
6. api/jobs.py: on a job finishing with code 0, best-effort append a screen_completed event
   (run_id, pack, rubric version from the fresh run_meta) — never fail the job on it.
7. Tests (offline, tmp_path + the TestClient/_as() fixture from tests/test_sso_identity.py):
   tests/test_req_sidecar.py (merge semantics, unknown-key preservation, defaults, icims
   round-trip through fetch_requisitions, location read from .req.json); tests/test_events.py
   (round-trip, req filter, corrupt-line skip, lock sidecar); tests/test_req_lifecycle_api.py
   (close/reopen/snooze/events guards + flows, 409 duplicate icims, close_suggestion truth
   table, retention_for math, req events never flip governance.review_pending());
   tests/test_req_trail.py (composition from synthetic fixtures).
Do NOT touch: web/ (nothing), layer1/layer2/critic/scoring/redact/policy/registry/rubric,
feedback contracts, Dockerfile, rbac.yaml, migrations (files only — no DB tables).
Gate: uv run pytest green; cd web && npm run build green; app boots, old UI identical. Stop.
```

### P30 — Vitest + React Testing Library harness

```
Frontend test harness for web/ (the Addendum 09 Part F gap). No product code changes.
1. Dev deps: vitest, jsdom, @testing-library/react, @testing-library/user-event,
   @testing-library/jest-dom. package.json scripts: "test": "vitest run",
   "test:watch": "vitest". web/vitest.config.ts: environment jsdom, setupFiles
   ./src/test/setup.ts, globals OFF (tests import {describe,it,expect} from "vitest"
   explicitly so tsconfig stays untouched).
2. web/src/test/setup.ts — jest-dom registration + fetch-mock reset. web/src/test/utils.tsx —
   the house pattern every later prompt uses: mockApi(routes: Record<string, unknown |
   ((url, init) => unknown)>) stubbing global.fetch (JSON responses, records calls for
   assertions) and renderPage(ui, {route}) wrapping MemoryRouter + a MeProvider-compatible stub
   (mocked /api/me: user, role, capabilities, has_admin, known_role...).
3. Exemplar tests proving the pattern on STABLE targets: web/src/test/ui.test.tsx (Tabs renders
   counts + fires onSelect; Badge band styles; Switch toggles) and web/src/test/hooks.test.tsx
   (useFetch happy path + error from the api.ts `detail` contract).
Note: test files live under web/src so `npm run build` (tsc) type-checks them — keep them
strict-clean.
Do NOT touch: src/ (python), page components, vite.config.ts server/build sections.
Gate: cd web && npm run test green; npm run build green; uv run pytest untouched. Stop.
```

### P31 — The requisition workspace at /reqs/:id

```
Build the per-requisition WORKSPACE per Addendum 11 Part A1. Old routes/pages stay alive and
unchanged this session (the cutover is P32) — /reqs/:reqId/:tab? is ADDED alongside them.
1. web/src/pages/workspace/Workspace.tsx at /reqs/:reqId/:tab? (default overview). Header:
   back link "All requisitions" (-> /roles now, /reqs after P32), title, stage chip, meta line
   "«req_code mono» · City, CC — pack · checklist vN · N screened · added by X" (req code ALWAYS
   monospace; em-dash if a pre-v2 test role lacks one), Blind review toggle. Tab state via
   Outlet context {role, blind, setBlind, reload}. Tabs declared as ONE exported array
   TABS = [{key, label, count?, element}] (Part D3 — later prompts append). Keys: overview,
   checklist, candidates, review, shortlist, audit. NO Ask Claude button yet (P44 owns it).
2. Extract page interiors into tab components under web/src/pages/workspace/ (MOVE, don't
   rewrite): CandidatesTab <- Screen.tsx interior (batch manager + screen CTA; drop RolePicker/
   Page wrapper; reqId from context). ReviewTab + existing ReviewDrawer <- Review.tsx interior
   (blind from context). ChecklistTab <- Checklist.tsx interior. ShortlistTab <- NextSteps.tsx
   interior retitled "Shortlisted candidates": buckets Move forward / Discuss as note cards,
   the strip "Not a fit — N. No automatic rejections; send a considerate update when you're
   ready, or keep them on file.", Export CSV, and the line "Nothing is sent automatically — you
   confirm each step. Push to iCIMS arrives with the adapter."
   OverviewTab (new): stage headline card + the ONE primary CTA (✨ magic style only where AI
   runs); JD panel (excerpt + "Maintained in iCIMS · auto-fetched via Req ID when the lifecycle
   APIs land"); "Job location & hiring rules" line (place — law); danger zone "Close
   requisition": outcome picker (Hired + count / No hire — cancelled) -> POST close, copy
   EXACTLY "Closing moves this req to History in this workspace only — the iCIMS requisition is
   unchanged."
   AuditTab (new): renders GET /api/roles/{id}/trail — checklist versions, runs with pack
   version, decision counts with verbatim reason quotes, close/reopen events; footer "Hire
   status lives in iCIMS — this trail records screening and decisions made here. Full explorer:
   Audit & History (HR Head / IT)."
   Opening a CLOSED req: read-only banner + fire POST events {action:"req_history_view"}.
   The old page files become thin wrappers rendering the same components — a runnable-at-each-
   step measure for this one intermediate prompt (deleted in P32), not a compat layer.
3. types.ts: mirror the P29 payload additions (lifecycle, suggest_close, trail, quickview...).
4. Tests (P30 pattern): web/src/test/workspace.test.tsx (tabs render + switch, header meta,
   mono req code, closed-req banner fires the event); web/src/test/overview.test.tsx (CTA per
   stage, close flow posts outcome + navigates).
Do NOT touch: python files, API routes, App.tsx old routes (only ADD the new one), AppShell nav,
stages.ts.
Gate: npm run test + npm run build green; uv run pytest green; walk one role end-to-end entirely
inside /reqs/:id while the old pages still work. Stop.
```

### P32 — Requisitions list + History + New requisition + IA cutover

```
Replace the roles-cards IA with the requisition-centric one, per Addendum 11 Part A1. This
session ends with the old top-level pages DELETED (greenfield: no redirect layer — the existing
`*` catch-all already lands on the home route).
1. web/src/pages/Requisitions.tsx at /reqs — a DENSE sortable table, not cards. Columns:
   Requisition (title + monospace req-code subline) / Location (place + pack-name subline +
   "non-English JD" badge when jd_language set) / Stage chip / Candidates (count links to
   /reqs/:id/candidates or review) / Hiring manager (status chip; em-dash until P42) / Last
   activity (viewer TZ via Intl; requisition-local subline via regionmap TZ when zones differ) /
   Action: ONE green primary button per row = the single next step from cta (magic style only
   when cta.magic) + a white secondary "Open". Sortable headers with ↓↑ (client-side). Search
   over title/req code/location. Metric stat cards on top (Active · Need your action · Awaiting
   HM input · Suggested to close) — each a CLICKABLE filter. Saved-view pills: "My reqs · N"
   (created_by == me, the default) / Needs action / APAC / EMEA / Americas / "All · N" /
   divider / "History · N" — region pills use the `region` field on each card. Title hover/click
   -> Quick-view popover (GET quickview: JD excerpt, checklist vN, requirement count, screened,
   added-by; buttons Open workspace / View JD). Every count is a link.
   SUGGESTED-CLOSE rows render as a green-tinted banner row: "All N decisions recorded, no
   activity for 14 days · closing here does not change the iCIMS req" + [Confirm close] (inline
   outcome picker Hired·N / No hire) + [Keep open] (POST snooze 30d; the row rejoins the list).
   HISTORY view (the pill): same table grammar — Requisition / Location / Closed date / Outcome
   chip ("Hired · N" -> /reqs/:id/audit; "No hire — cancelled") / Data retention ("PII purges in
   {N}d · audit kept 3y" from retention, or "PII purged {date} · audit retained") / Action:
   [View audit] green + [Reopen] [Clone] as DISABLED white buttons (tooltip "Coming with feature
   flags" — P37 wires them).
2. web/src/pages/NewRequisition.tsx at /reqs/new (replaces NewRole): iCIMS Req ID REQUIRED —
   inline error EXACTLY "iCIMS Req ID is required — no mapping to the requisition is possible
   without it."; helper "Copied from iCIMS — maps this screening to the requisition for audit
   and reports. Later, fetches the job details automatically."; Job title; Job location picker
   that declares consequences up front (e.g. "DPDP Act 2023 pack applies · candidate PII purged
   365 days after close") fed by a new light GET /api/locations [recruiter] returning
   [{code, label, law, retention_days}] (P45 extends this payload); JD paste; ✨ "Create draft
   checklist" -> existing create + draft flow. Server: POST /api/roles now REQUIRES
   icims_req_id (400 with the exact message; writes it + job_location into the .req.json).
3. Cutover: AppShell nav = "Requisitions" (single item) + Governance group (Rules & Signals,
   Jurisdictions, Access & Roles, "Audit & History" — label rename of Runs & Audit only) + user
   chip. App.tsx: routes /reqs, /reqs/new, /reqs/:reqId/:tab?; `*` catch-all -> /reqs. DELETE
   pages/Roles.tsx, RoleDetail.tsx, NewRole.tsx, Checklist.tsx, Screen.tsx, Review.tsx,
   NextSteps.tsx shells and components/RolePicker.tsx (interiors already live under workspace/).
   stages.ts: in_review chip -> BLUE (accent-blue-tint / #16729E; amber is reserved for
   needs-a-look); labels: Draft checklist (neutral), Candidates added (blue), Screened (green),
   In review (blue), Complete (green).
4. Tests: web/src/test/requisitions.test.tsx (rows with mono code + ONE green action, metric
   card click filters, History shows outcome/retention, suggestion banner fires close/snooze);
   web/src/test/new-requisition.test.tsx (required-icims error, consequence line changes with
   location). Backend: extend tests/test_req_lifecycle_api.py for the required-icims 400 +
   /api/locations.
Do NOT touch: engine files, review.py/jobs.py, decisions contract, admin pages beyond the nav
label.
Gate: npm run test + npm run build green; uv run pytest green; create a requisition with an
iCIMS id end-to-end, close one, find it under History. Stop.
```

### P33 — Hard gates: the ideal single-list rubric contract + gate-aware Review

```
Make hard gates first-class, per Addendum 11 Part A2 — as a GREENFIELD contract unification.
The engine already implements gate SEMANTICS end-to-end (findings covers hard filters in the one
structured call; layer1 passes only on match=="full", sets eligible=False, keeps the candidate
listed; scoring bands "Not a fit (gate)"; Layer 2 skips gate-failers). What changes is the SHAPE:
the two-list rubric (requirements + hard_filters) becomes ONE ordered requirements list.
1. models.py: RequirementKind = Literal["nice_to_have", "must_have", "hard_gate"]. DELETE the
   HardFilter class and Rubric.hard_filters — a gate is Requirement(kind="hard_gate", weight=0).
   Rubric.normalized() normalizes weights over non-gate rows only (gates carry no weight).
2. layer1.py: gates derive from kind=="hard_gate" — pass only on match=="full"; fail -> rename
   hard_filter_flags to gate_flags + eligible=False + reason "Hard requirement not met: <text>";
   gates contribute 0 to coverage; passes_gate() semantics unchanged. Update scoring.py/report.py
   references to the renamed field only (no behavior change).
3. findings.py: _requirement_items passes kind through; the prompt instruction for
   "hard_filter" becomes "hard_gate" (same objective pass/fail wording: full only when the
   resume explicitly satisfies it). rubric.py: single-list YAML serialization; the draft agent
   may propose kind: hard_gate for conditions the JD states as explicit minimums; version bump
   on save untouched. REGENERATE the committed test rubrics to the new shape — NO loader shim
   for the old two-list YAML (greenfield; test data is disposable).
4. Checklist editor (web/src/pages/workspace/ + components/ChecklistEditor.tsx): the Must-have
   switch becomes a three-state segmented control Nice-to-have / Must-have / Hard gate. Gate
   rows show "gate — no weight" where the importance dots were, plus "Hard gate: candidates
   without this are tagged 'Gate not met' in Review — still visible, never auto-rejected."
   Toggling preserves the row's typed importance locally within the session. Row order is
   naturally preserved (single list — the old hard_filters:[] wipe bug ceases to exist).
5. api/review.py: per-card gates {total, failed: [gate texts]} from hard_gate findings + layer1
   reasons; band_label -> "Gate not met" when ineligible (internal band unchanged); detail gains
   gate_warn for the drawer banner; counts gain gates_passed / gate_not_met. Sweep band labels
   everywhere to "Strong match / Possible match / Weak match" (band vs decision-label collision
   fix; the CSV export uses the same labels).
6. Review tab: gate filter pills (Gates · all / Passed · N / Gate not met · N) shown only when
   the run's rubric has gates; per-row chip "Gates 4/4" (green) vs amber "Gate not met · <which
   gate>"; gate-missed rows STAY listed with "…under the hard minimum. Shown for transparency —
   you can still open the evidence and override with a reason." (override = a normal decision
   with a reason); amber gate banner in the drawer. Copy must say the gate needs to be
   "explicitly shown in the résumé" (partial evidence fails a gate — the layer1 full-only rule).
7. Tests: REWRITE tests/test_layer1.py gate cases + the replay test to the new contract (the
   §11 Layer-1 replayability gate must be green on the NEW shape); new tests/
   test_gates_contract.py (normalization ignores gates; gate-fail semantics; single-list YAML
   round-trip; editor PUT round-trip preserves order + kinds); tests/test_review_gates.py
   (gate-failed card keeps rank/listing, "Gate not met" label, counts, drawer gate_warn).
   FE: web/src/test/checklist-gates.test.tsx, web/src/test/review-gates.test.tsx.
Do NOT touch: layer2.py, critic.py, redact.py, policy floor protection, cli run pipeline
structure, signals/, policies/.
Gate: uv run pytest green; npm run test + npm run build green; add a gate in the editor,
version bumps, Review shows the gate chips. Stop.
```

### P34 — Review velocity: batch decisions, reason cleanup, inline preview, hidden shortcuts

```
Make reviewing fast at 500+ résumés and make decision notes visible, per Addendum 11 Part A2.
1. Batch endpoint: POST /api/roles/{req_id}/decisions/batch [recruiter]
   {candidate_ids: string[], action: "forward"|"discuss"|"not"|"clear", reason?} — validate the
   action and ALL ids against the run records FIRST (400/404 listing unknown ids;
   all-or-nothing), then append one Decision per candidate via feedback.record_decision (same
   suggestion/versions stamping as the single route; reviewer = SSO email). Response
   {ok, applied, decisions: {cid: {action, reason}}}. The Review tab bulk bar ("{N} selected —
   apply to all: Move forward / Discuss / Not a fit + Clear") makes ONE call with optimistic row
   updates (reconcile from the response, roll back on error). Select-all checkbox over the
   FILTERED list.
2. Reason storage CLEANED (greenfield): kill the "Move forward — <text>" prefix-encoding —
   Decision.reason becomes the pure free text (the action already lives in human_action).
   Update the API composer and service.role_decisions; NO dual-format reader (old decisions.jsonl
   lines are disposable test data). Reasons must now DISPLAY everywhere: cards gain
   decided_reason + decided_ts; rows show the note (single line, ellipsized); the drawer seeds
   its note input from detail.decision, renders 'Noted: "<text>"' under the decision bar, and
   typing after deciding shows "Update note" which re-posts the same action with the new reason
   (append-only correction, consistent with the log). The Audit tab trail already quotes reasons.
3. Inline résumé preview in the drawer: build_detail gains resume_media_type (from the private-
   map path suffix; null when missing); the resume route accepts ?inline=1 (content-disposition
   inline); the drawer embeds PDF via <object type="application/pdf"> with a download fallback,
   DOCX = download link only; the whole preview is hidden in blind mode (current behavior kept).
4. Keyboard shortcuts with NO visible chrome (a code comment documents them — an accessibility
   affordance, not UI): j/k move the active row (visible focus ring), enter/o open drawer,
   1/2/3 = Move forward / Discuss / Not a fit on the active (or drawer) candidate, esc = close
   drawer else clear selection. Inert while focus is in an input/textarea.
5. Paper cuts: footer CTA "Shortlist →" disabled until >= 1 decision; the decorative sort
   button becomes a real menu (Match rank default / Name A–Z / Recently decided via decided_ts,
   undecided last); the "＋" text glyph on Add résumés becomes the icon component.
6. Tests: tests/test_decisions_batch.py (N lines appended with the SSO reviewer, clear action,
   all-or-nothing on an unknown id, empty selection 400); tests/test_review_reasons.py (clean
   reason round-trip, decided_reason/decided_ts surfaced, resume_media_type + inline
   disposition). FE: web/src/test/review-bulk.test.tsx (select-all -> ONE POST -> optimistic
   chips), web/src/test/review-shortcuts.test.tsx (j/k/1/esc; typing in the note input does NOT
   trigger shortcuts).
Do NOT touch: models.Decision fields, feedback.py validation, engine files, the single-decision
route's contract.
Gate: uv run pytest green; npm run test + npm run build green; drive it: select 3, bulk
"Discuss", type a reason, see it on the row, in the drawer and in the Audit tab. Stop.
```

### P35 — Honest screening runs: preflight + change-aware Candidates tab

```
Give the Candidates tab change detection, per Addendum 11 Part A2. Read-only server work — the
engine's content-hash cache already makes re-runs incremental; this makes the UI tell the truth.
1. service.screen_preflight(cfg, req) + GET /api/roles/{req_id}/screen/preflight [recruiter] ->
   {will_run, reason: "never_run"|"config_changed"|"files_changed"|"nothing_changed"|
    "empty_batch", changed: {checklist, rules, location}, files: [{name, status:
    "cached"|"new"|"updated"}], counts: {total, new, updated, cached, removed}}.
   Detection (no Azure, no engine calls): current rubric version vs the "(vN)" inside run_meta
   versions.rubric; registry version + content_hash(cfg.signals_file)[:12] vs versions.registry;
   recomputed content_hash of policies/base.yaml + the role's pack vs versions.policy_files;
   req.job_location vs meta jurisdiction => config_changed (all N re-screen). File drift:
   current batch content-hash ids vs scored record ids; an unscored file whose BASENAME appears
   in the private map under a different id = "updated", else "new"; scored ids missing from the
   batch = removed (informational). No run_meta => never_run.
2. Candidates tab: fetch preflight on load and after every batch mutation. Three states above
   the screen CTA — nothing_changed: headline "Nothing changed since the last run", note "Same
   checklist, rules and batch — screening again would be a no-op, so the shortlist is already
   current.", CTA [Proceed to review] (plain green Button -> Review tab, NO POST);
   files_changed: "K file(s) new or updated will screen — M cached results are reused free."
   + per-file tags in the batch list (cached / new — will screen / updated résumé — will
   re-screen); config_changed: "Checklist or rules changed — all N re-screen against the new
   version." The CTA uses the ✨ MagicButton style ONLY when will_run. During a run add the
   note "Only new or changed items call the model — everything else is served from cache."
3. Tests: tests/test_preflight.py over synthetic output/<role>/ fixtures in tmp_path: never_run;
   nothing_changed; one new file; same-basename-different-bytes => updated; rubric bump =>
   config_changed(checklist); registry hash change => rules; pack edit / location change;
   removed counted. FE: web/src/test/candidates-preflight.test.tsx (the three exact headlines;
   no-op CTA navigates without POSTing /screen; magic style only when will_run).
Do NOT touch: cli.py run pipeline, cache.py, jobs.py start/progress logic, engine modules.
Gate: uv run pytest green; npm run test + npm run build green; demo the three states by adding
a file and bumping the checklist. Stop.
```

### P36 — Six-role RBAC + capability union (HiringManager, SuperAdmin)

```
Extend RBAC to six roles with new capabilities, per Addendum 11 Parts A/B. rbac.yaml becomes:
  HRHead:        [recruiter, rules_view, rules_edit, runs_audit, data_manage, prefs_own,
                  prefs_approve, flags_view]
  IT:            [recruiter, rules_view, runs_audit, data_manage, prefs_own, flags_view]
  TASpecialist:  [recruiter, prefs_own]
  HRSpecialist:  [recruiter, prefs_own]
  HiringManager: [hm_take, prefs_own]        # NO recruiter: no create/screen/decide
  SuperAdmin:    [flags_view, flags_edit]    # feature flags only; composes via union
1. rbac.py: ROLES ordered by display precedence (HRHead, IT, TASpecialist, HRSpecialist,
   HiringManager, SuperAdmin). Capabilities become the UNION across ALL known roles the
   principal holds (today role_from_app_roles picks one; keep it for the display role).
   get_identity_full(headers) -> (user, display_role, roles: list[str]). DEV_ROLE accepts a
   comma list ("TASpecialist,SuperAdmin"). Unknown/no role still -> zero capabilities -> the
   access-not-configured page. ADMIN_CAPS gains flags_view (a pure SuperAdmin sees Governance).
2. api/app.py: require() reads the union; add require_any(*caps) dependency factory (P41 uses
   recruiter-OR-hm_take on shared view routes). /api/me gains roles: string[].
3. web: AccessNotConfigured lists the six roles; ROLE_LABEL entries for the two new roles;
   Access & Roles matrix renders 6 rows (+ the new capability columns with plain descriptions);
   types.ts Me.roles?: string[].
4. Addendum 11 Part B records the Entra step (create the two app roles) — no code for it.
5. Tests: extend tests/test_rbac.py — six-role matrix pins; union(TASpecialist+SuperAdmin) has
   recruiter AND flags_edit; HiringManager lacks recruiter; comma DEV_ROLE; unknown -> empty;
   TestClient checks: SuperAdmin 403s on /api/roles create, HiringManager 403s on decisions.
Do NOT touch: existing four roles' current capabilities (only additive prefs_own/flags_view),
scoring/critic/feedback, Decision contract.
Gate: uv run pytest green; npm run test + npm run build green. Stop.
```

### P37 — Feature flags (file-first) + SuperAdmin surface + iCIMS lifecycle stubs

```
A file-first feature-flag store with the /api/features read shape (so the P49 DB flip is
invisible), a Governance -> Feature flags page, and flag-gated Reopen/Clone stubs. Per Addendum
11: NO real iCIMS API calls anywhere (§12).
1. signals/features.yaml (committed seed; add to dataroot's seed list):
   version: 1
   features:
     icims_reopen:     {name: "Reopen requisition", description: ..., default: false, regions: {}}
     icims_clone:      {name: "Clone requisition", description: ..., default: false, regions: {}}
     icims_close_sync: {name: "iCIMS close-status sync", description: ..., default: false, regions: {}}
     hm_portal:        {name: "Hiring-manager portal", description: ..., default: false,
                        control: env, env: FEATURE_HM_PORTAL}
     ask_claude_mcp:   {name: "Ask Claude — live MCP", description: "Phase 2", default: false, regions: {}}
   regions: {IN: true} style overrides; absent = default.
2. src/resume_screener/features.py: flag_enabled(key, region=None) -> bool; catalog() — merged
   view (file seeds all keys; once the DB region framework is seeded, DB rows win per key they
   know; file supplies the rest); set_flag(key, region|None, enabled, author) = ruamel
   round-trip + file_lock + atomic replace + governance.append_audit(action="publish_flags") +
   events flag_changed. TWO CONTROL CLASSES: normal flags (UI-editable) and control:env flags —
   the value comes ONLY from the named env var; set_flag/PUT reject them (400 "controlled by
   deployment"); Flags.tsx renders them read-only with "Controlled by deployment (env var)".
   hm_portal is control:env (FEATURE_HM_PORTAL, default OFF) — only the dev/deployment owner
   can enable the HM portal.
3. CAPABILITY SUPPRESSION (one enforcement point): when hm_portal is off, the rbac-computed
   capability set STRIPS hm_take — every API guard and the FE (which derives all HM UI from
   /api/me capabilities) follow automatically.
4. Routes: GET /api/features -> require_any("recruiter","hm_take") returning the merged catalog
   (same shape as today); PUT /api/flags/{key} [flags_edit] {enabled, region?} (rejects
   control:env keys). P29's POST /roles/{id}/reopen gains the icims_reopen flag check for the
   req's region (403 plain message when off). New POST /api/roles/{req_id}/clone [recruiter,
   icims_clone-gated] -> {title, jd_text, job_location, checklist} prefill; the client lands on
   /reqs/new prefilled with iCIMS Req ID deliberately blank (required). History Reopen/Clone
   buttons + a close-sync chip enable from the flags; tooltip when off: "Turned off — ask IT
   (feature flag)".
5. web/src/pages/Flags.tsx at /flags (nav "Feature flags" under Governance, visible with
   flags_view; switches disabled without flags_edit): flag rows, per-region toggles, plain
   descriptions, caption "Changes are logged."
6. .env.example documents FEATURE_HM_PORTAL=0.
7. Tests: tests/test_features.py — default/override precedence, DB-wins-per-key merge (SQLite
   harness), file write bumps version + audit entry + event, PUT rejected for env-controlled
   key, hm_portal tracks the env var, capabilities drop hm_take when off, reopen/clone 403 when
   flag off, clone prefill shape.
Do NOT touch: db/seed.py feature rows, service.features_catalog() internals (compose around it),
no Alembic migrations.
Gate: uv run pytest green; npm run test + npm run build green. Stop.
```

### P38 — Region lens: picker + hard per-region filtering

```
The deferred 10-DATA-MODEL §6 enforcement, per Addendum 11 Part A2 — with zero-DB local dev
byte-identical (unseeded region framework = unrestricted, picker hidden).
1. service.accessible_regions(user) -> set[str] | None (None = unrestricted; wraps the existing
   region_context helpers, which degrade when the DB is unseeded). service.region_of_req(req) =
   regionmap.region_for_jurisdiction(req.job_location).
2. Enforcement: GET /api/roles?region=CODE filters cards to that lens AND to the caller's
   accessible set; the per-req lookup helper grows into _req_authorized(req_id, ident) -> 403
   "This requisition belongs to a region you don't have access to." when outside the grant
   (workspace, review, batch, decisions — everything per-req flows through it). The audit
   explorer (P45) accepts the same region= param. Feature gating standardizes on
   features.flag_enabled(key, region_of_req(req)).
3. regionmap.py gains the sync test hook: a test imports db.seed's region/jurisdiction seed data
   and asserts regionmap's constants match (they must never drift).
4. web/src/components/RegionPicker.tsx on the user-chip area: menu "All regions" + the codes
   from me.regions; HIDDEN entirely in unseeded local dev (exact current behavior); chosen lens
   persisted in localStorage and sent as ?region= on the requisitions fetch. The APAC/EMEA/
   Americas saved-view pills keep working client-side off the `region` card field.
5. Tests: tests/test_regionmap.py (mapping, US- fallback, seed sync); tests/test_region_scope.py
   (SQLite harness: EMEA-only user sees only EU reqs and 403s on an IN req; all-regions grant
   unrestricted; UNSEEDED DB -> everything visible, no 403s — the critical degradation pin).
Do NOT touch: db/seed.py, identity.roles_in_region (consume only), jurisdiction->policy
resolution (region is an ACCESS lens, never a law lens).
Gate: uv run pytest green; npm run test + npm run build green. Stop.
```

### P39 — HR preferences: store + deterministic critic check + approval queue

> **Superseded in part on 2026-10-16 (pen-test RS-01).** The spec below has clean
> preferences going live at once; the pen test got ten of fourteen disguised
> protected-attribute preferences live that way. Now: **every preference lands
> "pending" and applies only once an HR Head approves it**, never its own author
> (`preferences.applies`; a stored "active" reads as pending). The classifier folds
> spelling before matching (zero-width characters, look-alike letters, accents, leet,
> spaced letters — escalate-only), and the "optional LLM escalation" below now exists as
> `prefcheck_semantic.py`: the model judges intent against the governed never-use list,
> can only escalate and only as far as warn (a hard stop stays the governed lists'), and
> when it can't run a clean verdict becomes warn. The vocabulary
> grew as governed data (`scoring_model.v5`: `family_and_caregiving`, career breaks,
> `demographic_language` staged for preferences and checklists; a wider universal floor
> in `base.yaml`). Every route passes its stage explicitly; every attempt's verdict rides
> on the `pref_add` event and the Audit Explorer shows its text. The report's own probe
> is a regression test (`tests/test_pref_bypass_probe.py`).

```
"My preferences" for every role and the HR Head approval queue, per Addendum 11 Part A2 and the
v2 prototype (which WINS over the review doc here): user-scoped, clean prefs active immediately,
critic-flagged need HR Head approval, protected attributes blocked outright, approvals expire
(6-month re-review). NO scoring change in this phase (that is P40).
1. src/resume_screener/prefcheck.py — the shared deterministic classifier (also used by P43):
   classify_text(text, *, extra_exclusions=[]) -> TextCheck{level: "ok"|"warn"|"blocked",
   basis_ids: [..], reason: str (plain language, shown verbatim)}. Offline, precision-first
   regex lexicons in the style of critic._CODE_SCREEN_PATTERNS (import/reuse its patterns
   read-only): BLOCKED = protected attributes (age/DOB/graduation-year phrasing like "under 35",
   gender, religion, caste/community, marital status, pregnancy, nationality, disability, sexual
   orientation). WARN = proxies (tier-1/metro/city/location preference, employment gaps, salary
   history, language preference, school prestige, startup-age/employer-age, culture-fit vibes,
   similarity-to-employees) + any pack additional_exclusions keywords passed in. Severest wins.
   User-scoped prefs are checked against the UNION of all shipped packs' additional_exclusions
   (strictest anywhere). Include finance-vocabulary negative fixtures ("gap analysis", "salary
   processing" must stay ok). An optional LLM escalation may run online only (never in tests)
   and can only escalate, never downgrade.
2. src/resume_screener/preferences.py — event-sourced signals/preferences.jsonl, one line per
   transition, latest-line-per-pref_id wins: {pref_id, owner, owner_name?, text, action:
   add|approve|decline|withdraw, status: active|pending|approved|declined|blocked|withdrawn,
   classification: {level, basis_ids, reason}, cap_points: 2.0, approved_by?, approved_at?,
   review_due? (= approved_at + 6 months), actor, actor_role, ts}. Reader list_prefs(owner=None)
   folds latest lines and derives display status "rereview" when review_due < now (suspended —
   contributes 0 in P40; copy "Re-review needed — suspended until re-approved"). add_preference:
   blocked -> stored blocked (struck-through, black chip, NO approval path); warn -> pending
   (+notify event to HR Head); ok -> active immediately. Every transition emits a pref_* event.
3. Routes: GET /api/preferences [prefs_own] (own prefs, recruiter-plain rows incl. status_label,
   warning, cap note "Capped at +2 if approved.", approved_by/review_due); POST
   /api/preferences/check {text} -> TextCheck (dry-run for the composer); POST /api/preferences
   {text} (commits; the client shows the warn confirm dialog between check and commit); DELETE
   /api/preferences/{id} (withdraw own); GET /api/preferences/pending [prefs_approve] (pending +
   rereview across ALL users, owner shown, badge count); POST /api/preferences/approve
   {pref_ids[]} bulk + POST /api/preferences/{id}/decline {note} [prefs_approve] — approve
   stamps approved_by/at + review_due (+6 months), audit text "approved with the warning on
   record".
4. FE: web/src/pages/MyPreferences.tsx at /preferences, nav item "My preferences" for EVERY
   role. Header copy per the prototype: "Yours — applied to every requisition you manage,
   whatever your role. No conflicts → active immediately. Flagged by the bias critic → HR Head
   approval with the warning on record. Protected attributes → blocked. Approvals expire and
   need periodic re-review." Composer "Add a preference in plain language…" + "Check & add";
   warn confirm dialog "This needs HR Head approval … An approval notification will be sent.";
   status cards: green "Active — no conflicts" / amber "Pending HR Head approval" (withdraw) /
   "Approved · {name}" with "Re-review due {date}" / amber "Re-review needed" / black "Blocked"
   (struck-through text + reason). web/src/pages/PreferenceApprovals.tsx at /preference-
   approvals [prefs_approve] + nav item with pending-count badge: queue across users, select-
   all, "Approve selected (N)", decline-with-note.
5. Tests: tests/test_prefcheck.py (blocked fixtures incl. caste + "under 30" + pregnancy; warn
   fixtures incl. tier-1 college + "prefers candidates in Mumbai" + gap-free; ok fixtures;
   finance negatives; extra_exclusions escalate; severest wins). tests/test_preferences.py
   (lifecycle add->pending->approve->rereview after frozen-time expiry; withdraw; blocked has no
   approval path; bulk approve; events emitted; lock/atomicity smoke; guards per capability).
Do NOT touch: scoring.py, critic.py behavior (patterns imported read-only), signals/
registry.yaml, decisions log.
Gate: uv run pytest green; npm run test + npm run build green. Stop.
```

### P40 — Preference scoring stage (engine — highest care)

```
The ±2-per-preference bounded stage applied AFTER checklist scoring, per Addendum 11 Part A2.
READ FIRST: src/resume_screener/scoring.py (all of it) and the assemble_score call site +
run_meta writer in cli.py. The bar: a run with NO applicable preferences is BYTE-IDENTICAL to
today, and all 254 pre-existing tests pass UNMODIFIED.
1. models.py (additive): PreferenceAdjustment{pref_id, owner, text, points, evidence: str|None,
   rationale}; ScoreResult.preference_adjustments: list[PreferenceAdjustment] = [].
2. scoring.py: assemble_score(..., preference_adjustments: list[PreferenceAdjustment] | None =
   None). pref_net = round(sum(clamp(p.points, -2.0, +2.0)), 2), folded into the SAME single
   clamp: final = clamp(base + l2_net + pref_net, 0, 100). Preferences NEVER touch eligibility,
   gates, or the must-have band cap; the band is recomputed from final exactly as today.
   Rationale gains "N hiring-preference note(s), net {+X}." only when non-empty.
3. New src/resume_screener/pref_match.py: ONE structured call per gate-passing candidate ONLY
   when the applicable pref list is non-empty — input redacted profile + findings + the
   active/approved pref texts; output [{pref_id, matched, points, evidence, rationale}]. Code
   enforces: unknown pref_id dropped; evidence must be a verbatim substring of redacted_text
   (whitespace-tolerant, same check as findings) else the adjustment is DROPPED; per-pref clamp
   ±2; pending/blocked/declined/expired("rereview") prefs never reach the matcher; prefcheck
   re-classifies each pref text at run time (a since-blocked phrase contributes 0). Cached via
   JsonCache with PREFS_PROMPT_VERSION in the key. temperature=0.
4. WHOSE prefs apply (replayable by construction): the requisition's sidecar contacts — the
   union of active+approved prefs of created_by and hm.email. Resolved in cli.py run via
   preferences.list_prefs. New --no-prefs flag forces the stage off.
5. run_meta["versions"]["preferences"] = {applied: [{pref_id, owner, text_hash}], prompt:
   PREFS_PROMPT_VERSION} (empty when off); counts gain preference_adjustments. report.py
   per-candidate JSON carries preference_adjustments; the xlsx/csv rows gain pref_total (keep
   adj_total = Layer-2 only). api/review.py build_detail -> drawer pref_notes [{text,
   owner_label ("Your preference" / "{name}'s preference"), points, evidence}] rendered under
   the existing Context notes with +N/−N chips.
6. Tests: extend tests/test_scoring.py — per-pref clamp at ±2; multiple prefs sum then ONE
   final clamp; None/empty => model_dump()-identical ScoreResult vs a golden (the
   scoring-behavior-unchanged pin); band moves only via final score; ineligible candidates get
   pref_net in the score but keep the "Not a fit (gate)" band. New tests/test_pref_match.py
   (offline fake client: evidence-verification drop, unknown-id drop, expired contributes 0,
   cache key includes the prompt version). tests/test_report.py round-trips
   preference_adjustments.
Do NOT touch: layer2.py, critic.py, band thresholds, layer2_total_cap_points semantics,
Decision contract.
Gate: uv run pytest green with the 254 pre-existing tests UNMODIFIED; npm run test + npm run
build green; `screener run` on a test role with no prefs produces byte-identical scores. Stop.
```

### P41 — HM portal backend: assignment, suggestions, takes, activity

```
The hiring-manager collaboration backend, per Addendum 11 Part A2. ENTIRE surface flag-gated:
every route below ALSO requires features.flag_enabled("hm_portal") -> 403 "The hiring-manager
portal isn't enabled yet." when off (defense in depth on top of P37's capability suppression).
Takes are INPUT, never decisions.
1. Assignment: PUT /api/roles/{req_id}/hm [recruiter] {email, name} -> sidecar hm
   {email, name, status: "invited"} via save_req_meta + event + notify stub to the HM. The
   list's HM chip and metrics.awaiting_hm light up from this field.
2. Stores (Part D schemas): jds/<stem>.suggestions.jsonl — {suggestion_id, req_id,
   requirement_id, old_text, new_text, note?, status: open|accepted|declined, decided_by?,
   decided_note?, actor (hm email), actor_name?, ts}; status transitions are new lines, latest
   wins (checklist collaboration lives beside the rubric it amends and survives candidate-data
   deletion). output/feedback/hm_takes.jsonl — {take_id, req_id, run_id, candidate_id, take:
   strong_yes|yes|no, note?, hm_email, hm_name?, ts}; a re-take supersedes per (run_id,
   candidate_id, hm_email). New modules src/resume_screener/suggestions.py + hm_takes.py with
   file_lock + corrupt-line skip.
3. Access model: the VIEW routes (roles list/detail, rubric GET, review list/detail) move to
   require_any("recruiter", "hm_take"); a scoping helper filters for callers whose capabilities
   include hm_take but NOT recruiter: they see ONLY reqs where sidecar hm.email == their email
   (list AND per-req 403 otherwise). Blind mode works unchanged. The résumé file route stays
   recruiter-only (HM sees evidence, not files). ALL mutations stay recruiter-guarded EXCEPT:
   POST /api/roles/{req_id}/suggestions [hm_take + assigned-HM check] -> suggestion + event +
        notify(recruiter);
   POST /api/suggestions/{suggestion_id}/accept|decline [recruiter] {note?, edited_text?} —
        accept records status + returns {merged_text} for the checklist editor draft (the
        editor merges + publishes as usual; toast "Suggestion merged — publish to make it
        count"); decline notifies the HM with the note;
   POST /api/roles/{req_id}/takes [hm_take + assigned] {takes: [{candidate_id, take, note?}]}
        (single or bulk) — stamps the SSO email; NEVER writes decisions.jsonl;
   POST /api/roles/{req_id}/takes/send [hm_take + assigned] -> hm_takes_sent event + notify;
   GET  /api/roles/{req_id}/activity [require_any] — merged rail: suggestion events, accepts/
        declines, checklist publishes (P43's checklist_publish events + derived entries from the
        rubric version files), gate changes, hm_review_requested; each entry carries the UTC ts
        + req_tz (regionmap.TZ_OF_JURISDICTION) so the client renders "11:42 SGT · 09:12 your
        time";
   POST /api/roles/{req_id}/request-hm-review [recruiter] -> event + notify(hm.email).
4. api/review.py: cards + detail gain hm_take {take, hm_name} | null; detail keeps decision
   (the "TA decision: …" context line for the HM drawer).
5. Tests: tests/test_suggestions.py (thread lifecycle, latest-wins, HM-only create, recruiter-
   only decide, 403 for a non-assigned HM). tests/test_hm_takes.py (takes recorded with email;
   re-take supersedes; bulk; send event; decisions.jsonl PROVABLY untouched by any take; HM
   403 on /decisions, screen, batch routes and on reqs where they are not the sidecar HM;
   HM GET /api/roles returns only assigned reqs; everything 403 when the hm_portal flag is off).
Do NOT touch: feedback.py / Decision contract, rubric YAML writing (the editor owns merges),
resume file guard.
Gate: uv run pytest green; npm run test + npm run build green (FE unchanged). Stop.
```

### P42 — HM portal frontend: HM variants + TA-side cards (dark-launched)

```
Everything visual for the HM collaboration, per Addendum 11 Part A2 — DARK-LAUNCHED: the HM
experience is driven by me.capabilities containing hm_take, which the server already suppresses
when FEATURE_HM_PORTAL is off, so shipping this changes nothing user-visible until the dev flips
the env var. TA-side affordances that INVITE HM input hide behind the hm_portal flag from
/api/features.
1. HM signing in with the flag off (role known, hm_take suppressed): "My preferences" nav +
   a plain notice "The hiring-manager portal isn't enabled yet — ask IT." — never a broken shell.
2. HM list variant (capability-driven): saved-view pills reduce to My reqs / All / History; no
   metrics row; no "New requisition" button; the "Hiring manager" column becomes "Recruiter"
   (TA name from created_by + note "requested your review" while an open hm_review_requested
   event exists).
3. Checklist tab in SUGGEST MODE (HM): banner "You're in suggest mode. Propose edits on any
   line — {TA} reviews and publishes. Nothing you send changes the checklist directly."; rows
   read-only + per-row "Suggest edit" -> composer "Suggest an edit — goes to the TA for review"
   (Send suggestion / Cancel); own suggestions show "Waiting for {TA} to accept or decline."
   TA side: inline SuggestionCard (avatar initials, "Rahul (HM) suggests an edit", dual-timezone
   stamp, old text struck / new text, quoted HM note, Accept / Edit / Decline with note; accept
   merges into the editor draft + toast "Suggestion merged — publish to make it count").
   ActivityRail component on the Checklist tab (both roles) rendering GET /activity with
   dual-tz stamps; footer "Only TA / HR publish. HM suggestions merge when you accept — every
   accept is versioned and logged." + "Request HM review" button (TA; disabled with tooltip
   when no HM assigned).
4. Review tab (HM): ranked list + evidence drawers, blind-capable; NO decision triad; HmTake
   buttons Strong yes / Yes / No (+ optional note in the drawer); multi-select bulk takes;
   footer CTA "Send takes to {TA}"; drawer shows "TA decision: …" when one exists and the
   caption "Your take goes to the TA — decisions stay with TA/HR." TA view: chip
   "HM · Strong yes" beside the band on cards + drawer.
5. Overview tab: "Hiring manager" card (assign/edit email + name -> PUT hm; shows status).
6. New components: SuggestComposer, SuggestionCard, ActivityRail, HmTakeButtons under
   web/src/components/. types.ts mirrors.
7. FE tests (P30 harness): hm-variants.test.tsx — with hm_take-only capabilities the list hides
   create/metrics and Review shows take buttons not the triad; with the flag off the notice
   renders; TA sees the HM chip when review payload carries hm_take.
Do NOT touch: the recruiter decision bar, blind-mode résumé hiding, Part D refinements
(stale-data reconciliation, decision toggle/clear).
Gate: npm run test + npm run build green; uv run pytest green; demo with DEV_ROLE=HiringManager
+ FEATURE_HM_PORTAL=1 (and confirm the dark state with it unset). Stop.
```

### P43 — Checklist-critic governance + approval state

```
Requirement text runs the same deterministic critic as preferences; blocked text cannot be
saved; warned text routes publish through HR Head approval. Per Addendum 11 Part A2. The
checklist editor UI itself already exists — this is the seam + semantics.
1. In the rubric save route: classify EVERY requirement row with prefcheck.classify_text(text,
   extra_exclusions=<union of all packs>). Any BLOCKED row -> HTTP 400 "Cannot add this
   requirement — it references a protected attribute ({reason}). Reword it without the
   protected attribute." and NOTHING saves (no version bump, no file). WARN rows -> saved with
   an additive Requirement.critic_level: "warn" | None field + response needs_approval: true.
2. src/resume_screener/checklist_approval.py manages jds/<stem>.approval.json {version, status:
   none|pending|approved, flagged_requirement_ids[], requested_by?, requested_at?, approved_by?,
   approved_at?} (file_lock + atomic). While the LATEST rubric version has warn rows and status
   != approved, rubric GET carries approval {...} and the editor's publish button renders
   "Send v{N} for HR Head approval" instead of "Approve & publish v{N}".
3. Routes: POST /api/roles/{req_id}/checklist-approval/request [recruiter] -> pending +
   checklist_approval_requested event + notify(HR Head). POST /api/roles/{req_id}/checklist-
   approval/approve [rules_edit, message "Approving flagged checklists is limited to the HR
   Head."] -> approved + checklist_approved event (approval unblocks; publishing stays the
   normal rubric save flow). EVERY successful rubric save also emits a checklist_publish event
   (feeds the P41 activity rail and the P45 explorer rules scope).
4. Editor: amber row border + the critic's plain reason under flagged rows; chip "Needs HR Head
   approval at publish." (blocked text simply cannot be added — surface the 400 inline).
5. Tests: tests/test_checklist_critic.py — blocked text 400s and leaves NO new rubric version
   on disk; warn saves flagged + pending flow + publish-button state; clean text publishes
   exactly as before (regression); approve path gated to rules_edit; events emitted.
Do NOT touch: rubric version-bump mechanics, governance.publish_registry/publish_pack, weights.
Gate: uv run pytest green; npm run test + npm run build green. Stop.
```

### P44 — Ask Claude handoff (server-side redacted bundle)

```
The "Ask Claude" affordance, per Addendum 11 Part A2: copy a redacted context bundle now, MCP
connector later (the UI contract must not change then). Assembly is SERVER-SIDE (redaction/blind
rules live there; the bundle needs all candidates; the audit event must be atomic with the copy).
1. src/resume_screener/api/handoff.py: POST /api/roles/{req_id}/handoff {blind: bool}
   [require_any("recruiter","hm_take") + assigned-HM check] -> {text}. Markdown bundle built
   from the same records review.build_review/build_detail read: req title + req_code + location
   + pack name; checklist version + requirement texts (+must-have/gate markers); the band/gate/
   score table (display names, or "Candidate NN" by rank when blind); per-candidate evidence
   quotes + strengths/watch-outs; decision counts. EXCLUDED BY CONSTRUCTION: contact details
   (only already-redacted text is quoted), file paths/source filenames, candidate ids, emails.
   Emits handoff_copied event: "Ask Claude context copied · redacted bundle · {user} · logged
   automatically."
2. FE: "Ask Claude" button in the workspace header opening a panel on the Review tab:
   explainer "Copies a redacted context bundle — checklist v{N}, bands, gate results, evidence
   quotes. No contact details; names withheld in blind mode. Copies are recorded in the audit
   log."; three starter questions as copy-append chips ("Why did #2 outrank #5?", "Summarize
   the gate misses — any borderline?", "Draft an HM update on the top 5"); [Copy context]
   (calls the route, navigator.clipboard.writeText, toast); [Open claude.ai] link; footer
   "Phase 2: MCP connector — ask live with your own tokens." (chip visibility off the
   ask_claude_mcp flag, which stays off).
3. Tests: tests/test_handoff.py — the bundle contains no "@", no output/ or data/ path
   fragments, no 16-hex candidate ids, no source filenames; blind mode names are "Candidate N";
   the event line is appended; 403 for a non-assigned HM. FE test: panel copy + POST fired.
Do NOT touch: review payload shapes, the private map helpers (read-only reuse).
Gate: uv run pytest green; npm run test + npm run build green. Stop.
```

### P45 — Retention model (keep/purge) + Audit Explorer backend

```
Retention becomes two explicit duties per pack, and audit becomes queryable. Per Addendum 11
Part A2. VISIBILITY ONLY: nothing deletes anything in this program.
1. Retention fields (GREENFIELD REPLACEMENT): PolicyPack/EffectivePolicy DROP retention_days,
   REPLACED by keep_at_least_days (audit duty) + purge_by_days (candidate-PII privacy duty).
   Update ALL consumers in this prompt: the 8 pack YAMLs (commented values; IN: keep 1095 /
   purge 365), policy merge, governance.publish_pack (both fields editable), jurisdictions API
   (+own_* fields), run_meta stamp, service.retention_for internals (History column contract
   unchanged), /api/locations payload, any copy. No fallback derivation, no dual reads.
2. src/resume_screener/retention.py: purge_queue() -> [{req_id, title, jurisdiction, pack,
   closed_at|last_run_at, purge_due (closed_at + purge_by_days), keep_audit_until (closed_at +
   keep_at_least_days), status: scheduled|on_hold|awaiting_close, hold?}];
   signals/legal_holds.json {req_id: {by, ts, reason}} with file_lock + atomic;
   apply_hold/release_hold emit legal_hold_applied/released events.
3. src/resume_screener/api/explorer.py — all routes guarded _runs_audit (HRHead+IT), all accept
   region= (enforced via P38), and EVERY handler emits an audit_view event (viewer, scope,
   query) — "viewing here is itself logged":
   GET /api/audit/feed?q=&kind=          merged newest-first: decisions (verbatim reasons) +
                                          governance audit + events + run starts; kind chips
                                          Decision|Preference|Rules|Close|Data; audit_view
                                          events EXCLUDED by default (a "Views" chip opts in).
   GET /api/audit/candidates?q=          resolves names over the private maps -> matches.
   GET /api/audit/candidates/{req_id}/{candidate_id}/timeline
                                          the template: Screened (score/band/gates, run_id,
                                          checklist vN, policy pack id, ts) -> HM take (who,
                                          dual-tz) -> Decision (who, ts, VERBATIM reason) ->
                                          "Handed off — hire status lives in iCIMS" (derived
                                          from a forward decision); plus action targets
                                          {audit_pack_url (existing markdown route), checklist
                                          version, jd_ref, run_id}.
   GET /api/audit/requisitions?req=      req cards with event mini-timelines (deep-link target
                                          for History "View audit").
   GET /api/audit/rules                  publish_registry/publish_pack/review_signoff entries
                                          with rationale quotes + checklist_publish +
                                          pref_approved/declined events.
   GET /api/audit/retention              the purge queue + per-jurisdiction summary strings
                                          ("IN — DPDP pack: audit records kept ≥ 3 yrs ·
                                          candidate PII purged 365 days after close").
   POST /api/audit/retention/{req_id}/hold|release {reason}.
   GET /api/audit/reports                catalog incl. {key: "adverse_impact", status:
                                          "Phase 2"} stub; GET /api/audit/reports/{key}.csv for
                                          throughput_by_region (runs x regionmap),
                                          decision_mix_by_band, time_to_shortlist (run start ->
                                          last decision per req).
4. Tests: tests/test_retention.py (pack fields load/merge, floor protection untouched, purge
   date math, hold pauses status, publish_pack edits + audit entry; existing policy tests
   updated to the new fields in the same prompt). tests/test_explorer.py (feed composition +
   kind filter + views-excluded default; timeline from synthetic tmp artifacts — NEVER real
   output/; every endpoint appends audit_view; region filter; CSV shapes).
Do NOT touch: the audit-pack markdown route, run_meta existing keys, anything that deletes.
Gate: uv run pytest green; npm run test + npm run build green. Stop.
```

### P46 — Audit Explorer frontend + "Audit & History" nav swap

```
The explorer UI, per Addendum 11 Part A2 and the v2 mockups (frame 1e).
1. web/src/pages/AuditExplorer.tsx at /audit (+ components EventFeed, CandidateTimeline,
   ReqAuditCards, RulesLog, RetentionPanel, ReportsRail). Search-first header: "Every run,
   decision and rule change — searchable. Viewing here is itself logged." Five tabs: All /
   Candidates / Requisitions / Rules & criteria / PII & retention. All = the merged feed with
   kind chips. Candidates = search -> the timeline template with actions Export audit pack
   (existing markdown download; "PDF" = a print-friendly view via window.print), View checklist
   vN, View JD, View run. Requisitions honors the ?req= deep link from History "View audit".
   PII & retention = purge-queue rows + [Apply legal hold] (typed reason) + the jurisdiction
   summary. Reports rail: three CSV downloads + print views + the "Adverse-impact monitor —
   Phase 2" chip. Dual-tz stamps reuse the shared formatter.
2. Nav: "Runs & Audit" becomes "Audit & History" -> /audit; /runs redirects there; delete
   RunsAudit.tsx (its runs list + export live on in the explorer's Requisitions scope).
   Guard stays runs_audit (HR Head + IT) — recruiters/HMs never see the nav item.
3. NewRequisition location picker now shows the TWO retention lines from /api/locations
   (keep-at-least + purge-by, plain language).
4. FE tests: audit-explorer.test.tsx (tabs render, feed rows with kind chips, timeline renders
   verbatim reason, retention rows + hold button POSTs, deep link selects the req card).
Do NOT touch: explorer API shapes (P45 owns), guards.
Gate: npm run test + npm run build green; uv run pytest green. Stop.
```

### P47 — Governance polish: Data page + rules-publish dialog

```
Two governance gaps closed, per Addendum 11 and Addendum 09 Part G.
1. src/resume_screener/datamgmt.py — resurrect the Streamlit-era deletion logic from git
   history (`git show ea91a10:src/resume_screener/ui_app.py`, lines ~1818-1840) as engine
   functions: delete_cache() (rmtree + recreate .cache), delete_candidate_data() (clear the
   legacy resumes dir except dot-files; clear output/ except output/feedback/; EXTEND to also
   clear the per-role batches data/roles/*/resumes/ and stray .lock sidecars);
   storage_map() -> the Addendum 04 §A1 table rows with DATA_ROOT-resolved absolute paths +
   recursive sizes.
2. Routes [data_manage, message "Data management is limited to the HR Head and IT."]:
   GET /api/data/summary -> {rows: [{kind, path, format, note, size}], data_root};
   POST /api/data/delete-cache and POST /api/data/delete-candidates with body {confirm:
   "<typed phrase>"} — phrases "delete cache" / "delete all candidate data", 400 on mismatch;
   both emit data_deleted events AND governance.append_audit(action="data_delete"); responses
   are one-sentence consequences ("Cache deleted. The next screening rebuilds it — nothing else
   changed.").
3. web/src/pages/DataPage.tsx at /data, nav "Data" under Governance [data_manage]: the storage
   table + two danger cards with typed confirms + the note "The anonymous decision log
   (output/feedback) is kept — it is the audit trail."
4. Rules & Signals paper cut: replace the window.prompt() publish-rationale with a proper
   dialog showing a DIFF SUMMARY of the pending changes (enabled/disabled signals, cap changes,
   exclusion additions) + a rationale textarea (required). Same publish flow underneath.
5. Update 09-REACT-FASTAPI-UI.md Part F: strike the "Data page never ported" gap.
6. Tests: tests/test_datamgmt.py on tmp_path trees ONLY (guardrail §6.12 — never real data/ or
   output/): cache emptied + recreated, feedback/ survives, role batches cleared, dot-files
   kept, typed-confirm 400, audit entry + event written, RBAC guard. FE test: dialog requires
   rationale + shows the diff rows.
Do NOT touch: dataroot.bootstrap seeding, real data/output/.cache during development.
Gate: uv run pytest green; npm run test + npm run build green. Stop.
```

### P48 — Docs: README + CLAUDE.md v2 banner + addendum status

```
Bring the docs current with v2, in the P28 voice.
1. CLAUDE.md: add the FOURTH architecture banner (dated) — requisition-centric IA (Requisitions
   list + per-req workspace tabs), six SSO roles with capability union (+ dark-launched HM
   portal via FEATURE_HM_PORTAL), hard gates as first-class requirement kind, HR preferences
   (user-scoped, critic-checked, HR-Head-approved, ±2 bounded scoring stage), retention
   keep/purge fields, the audit explorer, feature flags, region lens. Update §3 (tree: events.py,
   regionmap.py, features.py, prefcheck.py, preferences.py, pref_match.py, suggestions.py,
   hm_takes.py, checklist_approval.py, retention.py, datamgmt.py, api/explorer.py,
   api/handoff.py, signals/features.yaml, signals/events.jsonl…), §4 CONTRACTS (single-list
   Rubric with kind incl. hard_gate — HardFilter deleted; PolicyPack keep_at_least_days/
   purge_by_days replacing retention_days; PreferenceAdjustment + ScoreResult.preference_
   adjustments; Requisition.icims_req_id; Decision.reason = pure free text), §7 (new
   nav/pages/roles), §8 (FEATURE_HM_PORTAL, DEV_ROLE comma list, new seeds).
2. README.md: nav/flows/roles table current (six roles), the requisition workspace quickstart,
   preferences + approvals blurb, audit explorer line. Keep ~120 lines.
3. Addendum 11: update the status banner (which prompts landed); Addendum 08 Part B: append the
   two new app roles + FEATURE_HM_PORTAL env var (already drafted in Part B here — make 08
   point at it).
4. .env.example: DEV_ROLE comma-list note + FEATURE_HM_PORTAL=0.
Stop.
```

### P49 — (Optional, deferrable) DB backends for the new v2 domains

```
Flip the new v2 file stores to Postgres behind the usual env switches, with an IDEAL schema
(greenfield — do NOT contort the new domains into the speculative hr_preference_set/_override
shape; reshape or drop those unused tables in the same migration). File defaults stay.
1. One Alembic migration: new tables hr_preference (first-class columns: pref_id, owner_email,
   text, status, classification JSON, cap_points, approved_by/at, review_due, ts — replaces
   hr_preference_set/hr_preference_override, which are dropped), app_event (append-only mirror
   of signals/events.jsonl with row-id ordering), hm_take (FKs requisition + app_user),
   checklist_approval, legal_hold; feature/region_feature reused for flags via upserts.
2. Repos + backend switches, each defaulting to the file store: PREFS_BACKEND, EVENTS_BACKEND,
   HM_TAKES_BACKEND, FLAGS_BACKEND (jsonl|yaml|db per domain). db import gains importers for
   the four domains + sidecar hm/created_by -> requisition_contact rows (contact_role values
   already fit). screener check's DB probe covers the new tables.
3. Tests: per-domain contract parity (same reads through either backend — the dbharness SQLite
   pattern), import idempotence.
Do NOT touch: file-store code paths (they remain the default), engine modules.
Gate: uv run pytest green; npm run test + npm run build green. Stop.
```

---

## Part D — Cross-phase contracts (the interfaces prompts share)

1. **`jds/<stem>.req.json` (P29)** — THE requisition metadata source (absorbs `.location.json`):
   `{schema: 1, icims_req_id, job_location, status, outcome, closed_at, closed_by, snooze_until,
   created_by, created_at, jd_language, hm: {email, name, status} | null}`. Merge-writes preserve
   unknown keys; `hm` is written by P41, read by P29's payloads (em-dash/0 until then).
2. **`events.py` (P29)** — the ONE app-event spine: `emit_event(kind, *, actor, actor_role,
   req_id, candidate_id, summary, data)` / `load_events(req_id, kinds)` on
   `signals/events.jsonl`. Governance `audit_log.jsonl` stays config-publish-only. The explorer
   (P45) merges decisions + governance audit + events + runs. `notify` events are the
   notification stub (payload `{to, subject}`) — rendered in activity rails, never emailed.
3. **Workspace `TABS` array + Outlet context `{role, blind, setBlind, reload}` (P31)** — later
   prompts append tabs/panels; never fork a second tab mechanism.
4. **`regionmap.py` (P29)** — REGION_OF_JURISDICTION + TZ_OF_JURISDICTION + `region_for_
   jurisdiction()`; consumed by list sublines (P32), dual-tz stamps (P41/P42/P46), enforcement
   (P38), reports (P45). P38 pins it to `db/seed.py` with a sync test.
5. **`prefcheck.classify_text` (P39)** — the deterministic ok/warn/blocked classifier, shared by
   preferences (P39/P40) and checklist governance (P43). LLM escalation online-only, never in
   tests, never downgrades.
6. **`service.retention_for(req)` (P29)** — display contract `{retention_days→(keep/purge from
   P45), purge_by, purged}`; P45 replaces the internals without touching the History column.
7. **`GET /api/locations`** — introduced P32 `{code, label, law, retention_days}`, payload
   extended P45 (`keep_at_least_days`, `purge_by_days`). Recruiter-guarded (the jurisdictions
   admin route stays rules_view).
8. **Role-card payload keys are additive-only** within the series; `web/src/types.ts` mirrors
   every addition in the same prompt that adds it.

## Part E — DB-flip mapping (P49 — DONE 2026-07-13, migration `a1b2c3d4e5f6`)

| File store (default) | DB home (switch) | Status |
|---|---|---|
| `signals/preferences.jsonl` | `hr_preference` — NEW ideal table; `hr_preference_set`/`_override` dropped (`PREFS_BACKEND`) | ✅ repo + switch + importer |
| `signals/events.jsonl` | `app_event` append-only (`EVENTS_BACKEND`) | ✅ repo + switch + importer |
| `output/feedback/hm_takes.jsonl` | `hm_take` FK requisition + app_user (`HM_TAKES_BACKEND`) | ✅ repo + switch + importer |
| `signals/features.yaml` | `feature` (+`default_enabled`) + `region_feature` upserts (`FLAGS_BACKEND`); `control: env` keys stay env-resolved | ✅ repo + switch + importer |
| `jds/<stem>.approval.json` | `checklist_approval` | ⏳ table created (schema-ready); switch/importer deferred |
| `signals/legal_holds.json` | `legal_hold` | ⏳ table created (schema-ready); switch/importer deferred |
| `.req.json` `hm`/`created_by` | `requisition_contact` rows (contact_role fits) | ✅ importer (import_requisition_contacts) |

---

**Sequence & gates:** P29 → P30 → P31 → P32 (IA cutover — the app is v2-shaped) → P33 → P34 →
P35 (the recruiter experience is complete) → P36 → P37 → P38 (platform: roles, flags, regions) →
P39 → P40 (preferences live, scoring guarded by the golden test) → P41 → P42 (HM portal, dark
until `FEATURE_HM_PORTAL=1`) → P43 → P44 → P45 → P46 → P47 (governance depth) → P48 (docs).
P49 is optional and can run any time after P47. Every prompt: `uv run pytest` green ·
`cd web && npm run build` green · `npm run test` green (post-P30) · Stop.

**Program definition of done** (mirrors the design review): one shareable URL per requisition;
gates visible and never silent (both groups always listed); closed reqs answerable in History
with a retention clock; an HM can suggest and take but can never decide; the audit explorer
answers "who hired this candidate, what was the score, which JD?" in one search with verbatim
reasons; preferences nudge within ±2 under approval + critic guardrails and never hide anyone;
a no-change screening run costs nothing and says so.

---

## Addendum — UX refinements (post-P49, from pilot feedback)

Ten recruiter-facing rough edges surfaced in real use. The engine, scoring and all
guardrails are **unchanged**; these are React + FastAPI-layer refinements. Full plan +
rationale in the working notes; the contract-affecting parts are summarised here so the
§4/§7 vocabulary stays the single source of truth.

### Backend contract changes (React app consumes these)

- **`role_stage` payload gains `checklist_ready: bool` + `checklist_block_reason: string|null`.**
  `checklist_ready` = a rubric exists **and** (no critic-`warn` rows **or** that version is
  HR-Head approved). Factored into `service.checklist_gate(req, rubric)` and reused by
  `_rubric_ui` **and** `start_screen` — so `POST /screen` now **enforces** the P43 approval
  gate server-side (previously UI-only), returning `400 "This checklist needs HR Head approval
  before screening."`. Résumés can still be added anytime; only screening is gated.
- **`GET /api/roles/{id}/jd` (new)** → `{title, jd_text}` — the full JD, decomposed from
  `jds/<stem>.md` via `service.split_jd`. Recruiter-gated + region lens.
- **`PUT /api/roles/{id}/details` (new)** → partial `{title?, jd_text?, job_location?,
  icims_req_id?}`; rewrites the JD heading/body in place (slug / `req_id` / filename **never**
  change), merges location/icims into the sidecar, iCIMS clash **excluding self** → structured
  409, emits `req_details_updated`. Under `REQUISITIONS_BACKEND=db` a narrow `sync_role_details`
  write-through keeps the identity fields (title/location + JD snapshot) coherent.
- **Structured 409 detail.** The duplicate-iCIMS clash (create + details PUT) and the slug clash
  now return `detail = {message, field, req_id, title}` (was a bare string) so the client can
  deep-link to the existing requisition. `api.ts` gained an `ApiError {status, detail}`.
- **`POST /api/roles/{id}/decisions` response enriched** → `{ok, action, decision:{candidate_id,
  action, reason, ts}, decision_counts:{forward,discuss,not,awaiting}}` (was `{ok, action}`), and
  the batch route adds `decision_counts` — the client reconciles optimistically with no full
  re-fetch (`review.decision_counts_for`).
- **`build_review` cards gain `must_have_gaps: string[]`** (missing must-have requirement *texts*)
  and the root gains `req_code` — for the richer CSV.
- **`POST /api/roles` accepts optional `hm_name` / `hm_email`** → sidecar `hm = {name, email?,
  status: null}`. `hm.status` is now **`string | null`**: `null` = "on record" (named at
  creation, no portal invite); `"invited"` / `"review_requested"` stay portal-flow values set by
  `assign_hm` / `request_hm_review`. The `awaiting_hm` metric counts only `status`-truthy HMs.
- **Batch folders re-keyed to the immutable `req.req_id`** (was `role_title`) so a title edit can
  never orphan a role's résumés; `display_title` now prefers the JD heading so renames show.
- **Resilience:** `role_stage` no longer dereferences a `None` rubric (the upload-before-checklist
  500); `list_roles` degrades one unreadable req to a visible "Needs attention" card instead of
  500-ing the whole dashboard; a **global exception handler** returns parseable JSON `{"detail":…}`
  on any unhandled error.
- **Perf:** file-state-keyed caches for the decisions log, résumé content hashes and parsed
  `config.yaml` (collapse the dashboard's O(reqs × log + bytes) re-reads to one read per file
  version); `/assets/*` served `Cache-Control: immutable`, `index.html` `no-cache`.

### Frontend refinements

1. **Decisions are optimistic** — the row chip flips instantly, the server echo reconciles the
   note + counts, failures roll back inline. The workspace is now stale-while-revalidate
   (`Workspace.tsx`), so a background reload never unmounts the page (the "postback" flash).
2. **Resizable Review drawer** — left-edge drag (or Arrow keys); min = the old 560px, max 88vw;
   width lives on the workspace context so it persists across tabs and resets on req change.
3. **Checklist editor** — auto-growing full-width textareas, S.No column + live "N requirements"
   count, **undo** for deleted rows (session-only stack), and a collapsible **JD reference pane**
   beside the editor (`components/JdPane.tsx`).
4. **Duplicate iCIMS** shows the inline "Open {title} →" deep link; the empty Checklist tab gets a
   **"Draft checklist from the job description"** button (`POST /rubric/draft`, fills the editor,
   never auto-publishes).
5. **JD on Overview** (full, collapsible) replaces the 300-char excerpt; the requisitions row title
   is a direct link to the workspace (the QuickView popover + "Open" button are gone).
6. **Screening gated** on `checklist_ready` with a plain-language tooltip; **Review + Shortlist
   tabs disabled** (with a tooltip) until the role is screened.
7. **Draft persistence** (localStorage) — the checklist editor and the New-requisition wizard
   autosave unsaved edits and offer to restore them on reopen; cleared on publish. The wizard's
   Back→forward no longer re-POSTs (`PUT /details` on edit, re-draft offer when the JD changed).
8. **Perf** — `useFetch` gained a stale-while-revalidate module cache + request dedupe
   (`invalidate(prefix)` after mutations); the heavy admin pages are `React.lazy`-split.
9. **Rich CSV export** — iCIMS ID, rank, candidate, band, score, confidence, decision + note +
   date, gates-not-met, must-haves-missing, strengths, watch-outs, HM take; filename uses the
   iCIMS ID.
10. **Hiring-manager name** captured at requisition creation (Graph-API lookup left as a seam).

**Tests:** 7 new backend suites (`test_roles_resilience`, `test_screen_gate`,
`test_role_details_api`, `test_hm_at_creation`, `test_decisions_single`,
`test_review_export_fields`, `test_view_caches`) and 6 new web suites (`review-decide`,
`checklist-editor`, `checklist-draft`, `checklist-draft-button`, `drawer-resize`,
`shortlist-export`), plus updates to the existing ones. `uv run pytest` + `cd web && npm test`
green; `npm run build` green.

### Follow-ups — preference-aware preflight + Candidates downloads

**(1) A preference change is now a config change (P40 gap closed).** `screen_preflight`
tracked checklist (rubric version), rules (registry + policy-pack hashes) and location, but
**not preferences** — so after a preference was approved/edited the CTA said "nothing changed"
and wouldn't let the recruiter re-screen to apply the new ±2 nudge. Added
`service.current_pref_signature(req)` — the `{pref_id, text_hash}` set the run itself computes
(the sidecar contacts' active/approved preferences) and already stamps into
`run_meta.versions.preferences.applied` — and a **`changed.preferences`** flag: when the
applicable set (or a preference's text) differs from the run's stamp, preflight returns
`config_changed`, re-enabling the ✨ CTA. The engine already re-scores correctly on that run
(`run_pref_match` cache-misses on the new set). `Preflight.changed` gains `preferences: boolean`;
the Candidates headline names what moved (e.g. "Checklist, rules and hiring preferences changed —
all N re-screen").

**(2) Résumé downloads on the Candidates tab (pre-screening).** A per-row download and a
whole-batch ZIP, served straight from the batch folder (before the candidate-id/private-map
bridge exists): **`GET /api/roles/{id}/batch/file/{filename}`** (basename-only, parent-verified —
no traversal) and **`GET /api/roles/{id}/batch/download`** (zipped to a temp file so a
500-résumé batch streams to disk, cleaned up after send via `BackgroundTask`). The Candidates
`FileRow` gains a download link; the batch header gains "Download all (ZIP)".

**Tests:** `test_batch_download.py` (5, incl. traversal refusal), preference cases in
`test_preflight.py`, `candidates-download.test.tsx`.

### Pilot asks — the candidate drawer in plain language (2026-09-11)

Five items from the pilot, all in the React layer except one prompt change. Engine,
scoring and every guardrail unchanged; §6.5 is untouched.

**(1) The résumé tab scrolls; the way out never scrolls away.** The résumé section set
the pane to `overflow: hidden` with the viewer at `flex: 1 1 auto; min-height: 420px` —
so on a short window the bottom of the viewer was CLIPPED with nothing to scroll, and
the bar carrying "Open in a new tab" could leave the screen. The pane is `overflowY:
auto` now and `.resume-bar` is `position: sticky; top: 0`. Measured at 1280x620: pane
scrollHeight 455 vs clientHeight 172, and after scrolling to the bottom the bar's top
is still exactly the pane's top, with the link inside the visible box.

**(2) A tick explains itself; "~" does not.** Every checklist glyph carries its meaning
as a hover tip AND as its accessible name (`MATCH_MEANING`): "Met", "Partly met — the
résumé shows some of it, not all. Your call", "Not found". A reviewer reading "~" as
"no" turns a part-way match into a rejection, which is the one misreading that costs a
candidate.

**(3) The dated positions are a journey, and they always come.** Prompt `p34` -> **`p35`**
(cached findings invalidate; applies from the next screen): a length-of-experience
requirement now returns `experience_spans` **even when the résumé states the total in
words**. The evidence precedence is UNCHANGED — the résumé's own sentence is still the
first choice — but "6+ years of experience" is the easiest line on a résumé to write
without it being so, and the reviewer was being shown the answer with no way to check
it. The drawer draws the spans as a timeline (`ExperienceJourney`): one step per
position, its period, a bar sized against the longest span, and its own length, over the
verified line it was read off, under a total that says when concurrent roles were counted
once. **The dates are read in exactly one place**: `findings.span_bounds` / `span_months`
/ `span_ongoing` measure a single position, `review._span_view` orders the rows oldest
first and attaches each length, and the client only formats — so the picture and the
gate-bearing total can never drift apart. The journey's outer columns are FIXED (120px /
68px, from measured text: the longest real period reads 113px, the longest length 64px)
because two per-row grids side by side do not align — measured bar boxes of 547 and 555
in adjacent rows before the fix.

**(4) An action surface is not a reading surface.** The distance dialog lost its
explanatory paragraph, the checkbox's trailing clause and `LocationRadius`'s duplicated
sentence + caveat in `mode="radius"` (the wizard and Overview keep both — there the
sentence IS the requisition's stored line). The hero reads "2 of 3 — 1 within 25 km of
Gurugram, 1 unknown"; what the lens is, and what it deliberately never does (score,
reject, or hide anyone whose distance is unknown), moved to an info dot one hover away.
Dialog height 263px, and its number still equals its button's. The drawer's score-
adjustments explainer stopped being printed above the list it explains AND on the info
dot beside it. Deliberately kept: empty states (guidance where there is nothing else on
screen) and the closer-look explainers in candidate detail, which an earlier pass asked
for.

**(5) The score reads as its parts.** "Covers 96% of weighted requirements (base 96). 2
evidenced signal adjustment(s), net +6.0." became `96% of the checklist` + `+6 by the AI`
= `100/100` `capped at 100` — the first opens the checklist, the second opens the
adjustments that produced it. Built from the score TRACE (`score_math`), never by parsing
the stored sentence: the record keeps `summary_rationale` exactly as the engine wrote it,
and a run too old to carry a trace still shows that sentence. A gate note or a must-have
cap follows on its own line; a set-aside adjustment stays visible as its own fact.

**Tests:** `tests/test_experience_journey.py` (4) + new cases in `test_experience_math.py`
(one position vs a merged working life, an undatable span, a stated length that now comes
with its working); `web/src/test/drawer-plain-language.test.tsx` (12). jsdom computes no
sticky, so the bar's `position: sticky; top: 0` moved from the stylesheet ONTO the element,
beside the pane that scrolls — the two are one guarantee, and there a test can hold us to
both (a `?raw` CSS import is not the answer: vitest runs with CSS disabled and hands back
`undefined`; `?raw` on a `.tsx` works and still pins the "explained once" rule, see
`web/src/raw-modules.d.ts`, added because this package has no `@types/node` and `node:fs`
fails the BUILD's tsc even when `tsc --noEmit` passed).

### Pilot asks — a requisition anywhere, and a run the upload asked for (2026-09-11)

Two reports from the same afternoon. Engine, scoring and guardrails unchanged;
`commute_or_address` is still a floor never-scored topic and nothing below reaches a
score.

#### 1. "The location filter doesn't work when battle tested"

A requisition for **Budapest**, résumés from **Gurugram** that state it plainly, and
every candidate reading *"distance is unknown"*. The reported question was "filter issue
or haversine issue?" — neither. `signals/places.yaml` held 58 places, 50 of them Indian,
and Budapest was not one of them, so `resolve_place` returned `None` for the JOB's side
of every pair and no candidate could ever produce a number. The file's own comment had
called it: the non-India list existed *"so a non-India requisition is not silently 100%
unknown, not as real coverage"*.

Three separate faults, all of which had to be fixed for the lens to be trustworthy:

**Coverage.** ~250 places now: every European capital and major metro, APAC, the
Americas, MEA, the existing Indian depth untouched. `tests/test_location_coverage.py`
checks the coordinates against **published great-circle distances** (London–Paris 344 km,
Budapest–Vienna 214, New York–San Francisco 4,129, Sydney–Melbourne 714) within 2%,
because a mistyped coordinate is the one failure mode of a hand-kept table that looks
perfectly right on screen. Two rules ship with it, both enforced by tests:

* a name that is also an ordinary word or a common personal name — "Austin", "Phoenix",
  "Nice", "Cork" — carries **`sweep: false`**. It stays fully usable as a job location
  and on an explicit `Location: Austin` line, and is kept out of the LAST-RESORT scan of
  the résumé body, where a bare word in prose ("rose like a phoenix", "reported to Austin
  Fernandes") would invent a home town. A confidently wrong city is worse than an honest
  unknown — the module's whole docstring rests on that.
* two cities never share a name. Cambridge and Birmingham are deliberately absent: the
  first entry wins, so a duplicate would report the loser's residents thousands of km
  from where they live.

**Reach.** `signals/` is seeded into DATA_ROOT on first run and **never overwritten**, so
a release that adds cities would reach a fresh deployment and no other — the same trap
`features.yaml` hit in pass 6, and worse here, because the symptom is a whole
requisition's worth of "unknown" rather than a stale label. The shipped table is now the
**floor** and the share's entries are laid over it **field by field**: a hosted
correction (a wrong coordinate, a local spelling) survives every release, and a city
added in a release arrives without anyone touching the share. Field-by-field matters —
someone fixing a coordinate writes `{name, lat, lon}` and would otherwise silently delete
that city's aliases, dropping "Gurgaon", the single likeliest cause of a false "unknown"
in this pilot.

**Honesty.** When the unresolvable side is the job's own city, the gap is OURS and it is
identical for everyone in the list. Saying "distance unknown" on forty rows sends the
reader to the résumés to look for what is missing. `work_location.place_known` (new,
additive, on the review and role payloads via `service.work_location_view`) makes the
pill read **"No distances for Budapest"**, the dialog say it once, and the controls that
could only ever hide people for a missing row of ours — the radius, the unknown-distance
box, the apply button — disappear. Each row states `States Gurugram` and stops.
`LocationRadius` says the same thing on Overview, where the place is set.

**And the distance is now DERIVED at read time.** The expensive, evidence-bearing half —
what the résumé STATES — stays frozen in the run artifact; that was the half worth
freezing (26 seconds for 42 candidates when Review re-parsed résumés off the share). The
arithmetic half is two table lookups and a haversine, so `review._attach_location_context`
re-derives it from the current table against the current job location. Two consequences,
both of which the pilot needed: **a city added to the table reaches the candidates
already screened against it** (no re-run to collect a one-line data fix), and **editing a
requisition's location re-measures** instead of leaving a distance to the old city under
a note naming the new one — which is worse than unknown, because it looks right. The
table is loaded ONCE per Review (`location_context.Places`, with a 15 s re-stat net);
`resolve_place` used to `stat()` the file twice per lookup, which is 666 share round trips
for a 333-candidate list.

#### 2. "Upload and screen still leaves the user in confusion"

The 2026-09-10 pass put a hold on the Screen call-to-action that hangs off **preparation**
— and the case that bit the pilot never has any. A handful of résumés can be prepared
before the upload's own response comes back, so the tab sees a finished batch, a run that
has not started yet, and (the mount probe having already answered "nothing running") a
live Screen button for work that is already on its way. Nothing arrives to correct it,
because the batch poll only runs while preparation does.

So the intent is held **client-side from the click** (`autoScreenAsked`), joining
`preparing` and `awaitingAutoScreen` into one uninterrupted stretch, and it is released
only by an ANSWER: a run in hand, or three `screen/active` probes 1.5 s apart reporting
there is none. Capped deliberately — if the run really is not coming (the checklist gate
closed behind the upload, the worker failed), a recruiter who cannot press Screen is
stuck, and a hold nothing releases is worse than the flash it prevents.

Every résumé in such a batch wears **"preparing to screen"** (orange — activity, one step
before the blue of a live run) instead of "not screened", which is true and reads as a
dead end. It shows on the rows the preflight has **not** caught up with — a file that
landed seconds ago, a slice just cut out of a bulk PDF — because that is exactly the row
that used to say nothing at all while the recruiter stood watching for a sign that the
next step was happening. The CTA block says it too: *"Screening starts on its own — you
can leave this page."*

### Pilot asks — the journey reads like a résumé, and the app stops asking people to zoom out (2026-09-11)

#### The experience journey

Three things, from one pilot reading of a real candidate's drawer.

**Reverse chronology.** The journey ran oldest-first, on the theory that a journey reads
forwards. A résumé does not: it is written newest-first, and the job being hired against
is the current one — which was at the *bottom*. `review._span_view` now orders newest
first (ties and undatable rows keep the model's own order, which follows the résumé).
The dates are still read in exactly one place; only the sort key changed.

**A role is named by its title, not by whatever the résumé printed on that line.** The
reported row read `[CANDIDATE], Mckinsey Global Capabilities & Services Private Limited
06/2024 - Present` — for someone who was a **Finance Process Lead**. That is not a
rendering bug: the résumé's employment line carries the person's NAME and the employer,
the title sits on its own line, and the name is redacted before the model sees the text.
So `ExperienceSpan` gains **`title`** and prompt **`p35` → `p36`** asks for it — with the
trap spelled out, because the model had no way to know: *"a line starting with
[CANDIDATE] is a NAME line: never treat it as a title"*, and never substitute the
employer. The title is verified as a substring exactly like every other quoted string,
and a title the résumé does not contain is **dropped without voiding the span** — the
dates are what a gate rests on, the title is the label that says which job you are
looking at, and losing a label must not cost someone the evidence of their own
experience. `title` is additive: a run screened under p35 has none, and the row draws
without it. **Titles appear from the next screen** (a prompt bump invalidates cached
findings); the reverse ordering and the fold apply immediately, to existing runs.

**The headline folds it away.** "Counted from the résumé" is now a button: the working is
open by default (showing it for everyone was the ask), and once a reviewer has checked
the addition, a click leaves the headline and the total. The row layout changed with it —
the title is the row's label, the bar sits under it in the same column, and the verified
line it was all read off stays beneath as the quote.

#### Working space, and the zoom nobody should have to ask for

The pilot's report: *"many times I had to request a test user to zoom out on their
browser to find working space"*. Checked first, because the fix depends on the cause —
the owner's own machine is 1920×1080 at **100%** Windows scaling with **no** zoom set in
Edge and no zoom policy. So nothing is misconfigured: a laptop at 125% or 150% scaling
hands the page a 1280- or 1080-CSS-pixel viewport, and a 240px nav plus an 880px
candidate drawer does not leave a readable list inside that.

**`web/src/uiScale.ts`** decides, at load, how big the app draws itself: `zoom` on the
root element (so overlays that portal to `<body>` scale with everything else), 1.0 at
≥1500px, 0.95 at ≥1320, 0.9 below — never lower, because smaller than that is not "more
room", it is unreadable. Measured at 1280×720: the app lays out in **1422px** instead of
1280, and the ranked list beside an open drawer goes from a sliver to something worth
reading. At 1920×1080 nothing changes, by design — there is no problem to fix there.

**It never fights the person.** Browser zoom is a statement about what someone wants to
look at, and an app that quietly claws it back is worse than one that never helped.
Browser zoom moves `devicePixelRatio`; a window resize does not — so if that ratio ever
differs from the one we planned against, the person has taken control and the app drops
to 1.0 **permanently** on that browser. One-way on purpose: being slightly too big is a
preference, being unexpectedly small is the complaint this exists to fix.

**Tried and dropped:** collapsing the 240px nav while the drawer is open (the drawer is
modal, so the nav is unusable room). The rule matched, the selector matched, and the
computed `flex-basis` never moved off 240px — and shipping a layout hack I could not
explain was the wrong trade when the scale change already delivers the space. The pilot's
own instruction covered it: *"if it doesn't look good, skip any changes."*
