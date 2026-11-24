// TypeScript mirrors of the FastAPI response shapes (src/resume_screener/api/app.py).

export interface Me {
  /** the sign-in address (a payroll UPN when hosted) — the identity the engine stores. */
  user: string;
  /** the person's real name, from the SSO `name` claim; "" when there is no claim. */
  name?: string;
  /** RESERVED — the directory job title, which is what the sidebar would rather show
   *  than an app role. Not a token claim, so it needs a claims-mapping policy or Graph
   *  User.Read.All + admin consent; the server never sets it yet and the shell falls
   *  back to the access scope. Filling it server-side is the whole change. */
  job_title?: string;
  /** 2026-09-06: the identity block's avatar — a directory photo (data URI) when the
   *  people directory holds one, else `initials` on the stable per-person `avatar_hue`. */
  photo?: string | null;
  initials?: string;
  avatar_hue?: string;
  role: string;
  /** every known app role held; capabilities are their union (P36). */
  roles?: string[];
  /** false = signed in but no known app role assigned (SSO) — show the access page. */
  known_role: boolean;
  /** is anyone signed in at all? `known_role: false` covers both a real principal with
   *  no app role (needs an administrator) and an expired session (needs to sign in),
   *  which want different screens. Always true in dev. */
  authenticated?: boolean;
  capabilities: string[];
  has_admin: boolean;
  can_edit_rules: boolean;
  evaluation_mode: boolean;
  review_pending: boolean;
  last_review: { ts: string; reviewers: string; date: string; by: string } | null;
  /** Region access (Phase 7). `ungranted` = no grants exist for this account, which means
   *  UNSCOPED (everything visible), not restricted — see service.region_context. */
  regions?: {
    all: boolean;
    codes: string[];
    ungranted?: boolean;
    /** Display names per code, so a picker never carries its own copy. */
    names?: Record<string, string>;
    /** Where this person's lens should OPEN — their choice, else their country,
     *  else their only granted region, else null ("All regions"). A starting
     *  point, never an access decision. */
    home?: string | null;
    home_source?: "chosen" | "country" | "sole_grant" | null;
  };
  /** P39: count of preferences awaiting this HR Head (0 for non-approvers). */
  pref_pending?: number;
  /** P51: count of configuration changes awaiting a second leader (0 for non-approvers). */
  changes_pending?: number;
  /** dev only (no SSO principal): the user/role switcher is available. */
  dev_switch?: boolean;
  /** Which of the three entry paths this server is serving (APP_MODE) — dev (no
   *  sign-in) | uat (app-driven Entra) | prod (Easy Auth). The shell BADGES it rather
   *  than the client inferring the environment from the shape of this payload:
   *  "am I looking at the real thing?" should never be a guess, least of all in a
   *  screenshot pasted into a bug report. */
  mode?: {
    mode: "dev" | "uat" | "prod";
    label: string;
    summary: string;
    auth_required: boolean;
    dev_switch: boolean;
    sso: "none" | "direct" | "easyauth";
    hosted: boolean;
  };
}

// --- P51 scoring-model governance ---

/** One knob-level change inside a drift item ("Strong match starts at: 88 → 85"). */
export interface DriftChange {
  path?: string;
  label: string;
  from: string;
  to: string;
}

/** One itemized "what changed since this run" entry (preflight, Review banner, dialogs). */
export interface DriftItem {
  area: "checklist" | "rules" | "location" | "scoring_model";
  headline: string;
  changes: DriftChange[];
  publishes: { version: number | null; author: string | null; approved_by?: string | null;
               rationale: string | null; summary: string | null; ts: string | null }[];
}

/** Role-level staleness (null = fresh). locked = decisions recorded (full ceremony);
 *  soft = quiet note, next screen picks it up; auto = the client re-screens once (free). */
export interface Staleness {
  mode: "locked" | "soft" | "auto";
  changed: string[];
  summary: string;
}

/** Review-payload staleness with the full itemization for the banner/dialog. */
export interface ReviewStaleness {
  mode: "locked" | "soft" | "auto";
  summary: string;
  details: DriftItem[];
}

/** GET /api/scoring-config — the published values + resolved display strings every
 *  on-screen scoring fact reads from (never a literal in JSX). */
export interface ScoringConfig {
  version: number;
  published_by: string | null;
  approved_by: string | null;
  published_at: string | null;
  bands: { strong_at: number; possible_at: number; cap_if_must_have_missing: string };
  clamp: { min: number; max: number };
  coverage: { match_value: Record<string, number> };
  adjustments: { total_cap_points: number };
  preferences: { per_preference_cap_points: number; review_months: number };
  confidence: Record<string, number>;
  display: Record<string, string>;
}

/** One AI signal on the governed scoring model (the merged registry). */
export interface ModelSignal {
  id: string;
  description: string;
  display_name: string | null;
  definition: string | null;
  kind: "computed" | "judged";
  direction: "plus" | "minus" | "both";
  cap_points: number;
  enabled_default: boolean;
  retired: boolean;
  job_relatedness: string;
  notes: string | null;
}

export type TopicStage = "scoring" | "checklist" | "preferences" | "narrative";

/** One never-scored topic (reason + floor marker + stages + trigger phrases). */
export interface ModelTopic {
  id: string;
  name: string;
  reason: string;
  floor: boolean;
  added_by: string | null;
  added_at: string | null;
  stages: TopicStage[];
  phrases: string[];
}

/** The full editable scoring-model document (what drafts are staged from). */
export interface ScoringDoc {
  schema?: number;
  version: number;
  bands: { strong_at: number; possible_at: number; cap_if_must_have_missing: string };
  clamp: { min: number; max: number };
  coverage: { match_value: Record<string, number> };
  adjustments: { total_cap_points: number };
  preferences: { per_preference_cap_points: number; review_months: number };
  confidence: Record<string, number>;
  /** Batch-level quality bands (2026-07-19) — governed like everything else. */
  batch_quality: { top_n: number; strong_at: number; okay_at: number };
  signals: ModelSignal[];
  topics: ModelTopic[];
  display: Record<string, string>;
}

/** One editable knob on GET /api/scoring-model (value + plain-language metadata). */
export interface ScoringKnob {
  path: string;
  value: unknown;
  label: string;
  help: string;
  technical_term: string | null;
  usual_bounds: [number, number] | null;
  group: string;
}

export interface ModelVersionRow {
  version: number;
  ts: string | null;
  author: string | null;
  approved_by: string | null;
  rationale: string | null;
  summary: string | null;
}

export interface ScoringModelRead {
  version: number;
  knobs: ScoringKnob[];
  display: { path: string; value: string }[];
  signals: ModelSignal[];
  topics: ModelTopic[];
  model: ScoringDoc;
  versions: ModelVersionRow[];
  median_recent_move: number | null;
}

/** One leveled propose-time warning ({warn|red|info}) on a change proposal. */
export interface SystemWarning {
  level: "warn" | "red" | "info";
  text: string;
  path: string;
}

export interface DiffRow {
  path: string;
  label: string;
  from: string;
  to: string;
}

/** One change proposal (signals/change_requests.jsonl). */
export interface ChangeRequest {
  change_id: string;
  kind: "scoring_model" | "policy_pack";
  target: string;
  base_version: number;
  summary: string[];
  diff?: DiffRow[];
  system_warnings: SystemWarning[];
  rationale: string;
  proposed_by: string;
  proposed_at: string;
  status: "pending" | "approved" | "declined" | "superseded" | "withdrawn";
  decided_by: string | null;
  decided_at: string | null;
  decline_note: string | null;
  resulting_version: number | null;
}

/** The compact per-card score arithmetic (the score popover). */
export interface ScoreMathBrief {
  base: number;
  adjustments: number;
  suspended_count: number;
  preferences: number;
  before_limits: number;
  final: number;
  clamped: "high" | "low" | null;
  band_label: string;
}

/** The drawer's full "How this score adds up" trace (as-of the run's versions). */
export interface ScoreMath {
  as_of: { scoring_model_version: number; checklist: string | null; approximate: boolean };
  base: { total: number; explainer: string;
          lines: { label: string; kind: string; weight_pct: number; credit: string;
                   credit_value: number; points: number }[] };
  adjustments: { net: number; cap_total: number; cap_note: string;
                 lines: { label: string; points: number; flagged: boolean }[];
                 suspended: { label: string; points: number; note: string }[];
                 enforcement_notes: string[] };
  preferences: { net: number; per_item_cap: number; cap_note: string;
                 lines: { label: string; points: number }[] };
  total: { before_limits: number; final: number; clamped: "high" | "low" | null };
  band: { result: string; label: string; strong_at: number; possible_at: number;
          clamp_min?: number; clamp_max?: number;
          capped_by_must_have: string | null; cap_note: string | null;
          gate_note: string | null };
  confidence: { level: string; explainer: string };
}

/** POST /api/scoring/preview — the example candidate's live result. */
export interface WaterfallStep {
  step: "base" | "adjustment" | "suspended" | "dropped" | "preference" | "clamp" | "band";
  label: string;
  delta: number;
  running: number;
  note: string | null;
}

export interface PreviewResponse {
  result: { final_score: number; band: string; band_label: string; eligible: boolean;
            gate_passer: boolean; confidence: string; summary: string };
  base: { total: number; coverage_pct: number };
  enforcement_notes: string[];
  waterfall: WaterfallStep[];
}

// --- P39 HR preferences ---
export interface TextCheck {
  level: "ok" | "warn" | "blocked";
  basis_ids: string[];
  reason: string;
  /** How the verdict was reached (2026-10-16): what the phrase lists found, what the
   *  AI intent check found, and whether it ran. `level` is already the merged one. */
  deterministic_level?: "ok" | "warn" | "blocked" | null;
  semantic_level?: "ok" | "warn" | "blocked" | null;
  semantic?: "ok" | "flagged" | "unavailable" | "off";
}

export interface Preference {
  id: string;
  text: string;
  owner: string;
  owner_name?: string | null;
  /** "active" is retired (2026-10-16): every preference waits for sign-off. */
  status: "pending" | "approved" | "rereview" | "declined" | "blocked" | "withdrawn";
  status_label: string;
  level?: "ok" | "warn" | "blocked" | null;
  warning?: string | null;
  cap_note: string;
  approved_by?: string | null;
  approved_at?: string | null;
  review_due?: string | null;
  decline_note?: string | null;
  ts?: string | null;
}

// --- P41/P42 hiring-manager portal ---

/** An HM take on a candidate, surfaced on review cards + drawer (P41 writes it). */
export interface HmTake {
  take: "strong_yes" | "yes" | "no";
  hm_name: string;
}

/** A checklist edit the assigned HM proposed (INPUT — the TA accepts/declines). */
export interface Suggestion {
  suggestion_id: string;
  req_id: string;
  requirement_id: string;
  old_text: string;
  new_text: string;
  note: string;
  /** 2026-07-19: a suggestion can change the kind and importance too, and the
   *  HM can withdraw an open one before the recruiter decides. */
  old_kind?: RequirementKind | null;
  new_kind?: RequirementKind | null;
  old_importance?: number | null;
  new_importance?: number | null;
  status: "open" | "accepted" | "declined" | "withdrawn";
  decided_by?: string | null;
  decided_note?: string | null;
  actor: string; // the HM's email
  actor_name?: string;
  ts: string;
}

/** One entry on the Checklist-tab activity rail (GET /activity). */
export interface ActivityEntry {
  ts: string;
  kind: string;
  actor: string;
  summary: string;
  /** Requisition-local IANA zone for the dual-timezone stamp. */
  req_tz: string;
}

// --- P37 feature flags ---
export interface FeatureFlag {
  key: string;
  name: string;
  description: string;
  default: boolean;
  /** resolved global value (env value for env-controlled flags, else default). */
  enabled: boolean;
  regions: Record<string, boolean>;
  /** Whether this flag can differ per region at all. "app" flags are read
   *  globally, so a per-region override would never be consulted. */
  scope?: "requisition" | "app";
  source: "file" | "db";
  /** present only for deployment-controlled flags (value comes from an env var). */
  control?: "env";
  env?: string;
}

export interface ClonePrefill {
  title: string;
  jd_text: string;
  job_location: string;
  country?: string | null;
  us_state?: string | null;
  checklist: Requirement[];
}

export interface Cta {
  label: string;
  goto: string;
  magic: boolean;
}

// --- P29 requisition-workspace payload (additive keys on every role card/detail) ---

export interface Lifecycle {
  status: "active" | "closed";
  outcome: { kind: "hired" | "cancelled"; hired_count?: number } | null;
  closed_at: string | null;
  closed_by: string | null;
  snooze_until: string | null;
  created_by: string | null;
  created_at: string | null;
  /** People the owner invited onto this requisition (2026-09-10). Lifts own-req
   *  scoping for them, and nothing else — capabilities still come from their role. */
  collaborators?: Collaborator[];
}

export interface Collaborator {
  email: string;
  name: string;
  added_by: string;
  added_at: string;
}

export interface CloseSuggestion {
  suggest: boolean;
  decided: number;
  quiet_days: number;
}

/** Display-only retention clock (present on closed reqs; P45's keep/purge model).
 *  keep_at_least_days = audit-record floor; purge_by_days = candidate-PII purge
 *  duty; purge_by = the computed date. Nothing ever deletes. */
export interface Retention {
  keep_at_least_days: number | null;
  purge_by_days: number | null;
  purge_by: string | null;
  purged: boolean;
}

/** Hiring-manager assignment. status null = named for reference at creation
 *  (no invite); "invited"/"review_requested" come from the portal flow (P41). */
export interface HmRef {
  email: string;
  name: string;
  status: string | null;
}

/** The batch-level quality read (2026-07-19): mean final score of the top N
 *  gate-passing candidates vs the governed batch_quality thresholds. Derived
 *  per request — refreshes with re-screens AND with threshold changes.
 *  score null = nobody passed the hard gates. */
export interface BatchQuality {
  score: number | null;
  band: "strong" | "okay" | "weak";
  label: string;
  basis: string;
  top_n: number;
  thresholds: { strong_at: number; okay_at: number };
}

export interface ReqV2 {
  /** The run's scoring-model stamp ("v1"); null before the first screen. */
  run_scoring_model?: string | null;
  /** The iCIMS Req ID — the mapping anchor. Always rendered monospace; em-dash when a pre-v2 test role lacks one. */
  req_code: string | null;
  lifecycle: Lifecycle;
  last_activity_utc: string | null;
  suggest_close: CloseSuggestion;
  jd_language: string | null;
  hm: HmRef | null;
  created_by: string | null;
  region: string;
  /** Requisition-local IANA zone (regionmap) for dual-timezone activity stamps. */
  tz: string;
  retention?: Retention;
}

export interface RoleCard extends ReqV2 {
  req_id: string;
  title: string;
  job_location: string;
  location_display: string;
  /** The workplace (city) + search radius. The list's Location column shows this
   *  place rather than the country — the law subline already implies the country. */
  work_location?: WorkLocation | null;
  /** Governing law/pack name — the Location subline on the requisitions list. */
  law: string;
  stage: string;
  stage_label: string;
  progress: string;
  batch_count: number;
  screened: boolean;
  reviewed_count: number;
  candidate_count: number;
  rubric_version: number | null;
  cta: Cta;
  /** True = a published checklist that isn't awaiting HR-Head approval (gate). */
  checklist_ready: boolean;
  /** Plain-language block reason when a checklist exists but can't screen yet. */
  checklist_block_reason: string | null;
  /** A role whose on-disk data couldn't be read — a degraded "Needs attention" card. */
  unavailable?: boolean;
  /** P51: null when fresh — scores produced under older published rules otherwise. */
  staleness?: Staleness | null;
  /** Batch-level quality read; null before the first screen. */
  batch_quality?: BatchQuality | null;
  /** 2026-07-19: the run locking this batch (live or paused); null = unlocked. */
  screening?: ScreenLock | null;
}

export interface RolesResponse {
  metrics: {
    active_roles: number;
    candidates_screened: number;
    /** 2026-09-06: résumés screened on the requisitions the signed-in person created. */
    my_candidates_screened?: number;
    awaiting_review: number;
    total: number;
    need_action: number;
    awaiting_hm: number;
    suggested_close: number;
    history_count: number;
  };
  roles: RoleCard[];
}

export interface Step {
  key: string;
  label: string;
  index: number;
  state: "done" | "current" | "todo";
}

export interface RoleDetail extends ReqV2 {
  req_id: string;
  title: string;
  jd_ref: string;
  job_location: string;
  location_display: string;
  /** The country this requisition was created for (ISO-3166 alpha-2) and, for a
   *  US job, its state. job_location names the PACK; these name the place. */
  country?: string | null;
  us_state?: string | null;
  /** The workplace + search radius (2026-08-20); null when not set. */
  work_location?: WorkLocation | null;
  law: string;
  stage: string;
  stage_label: string;
  stage_index: number;
  steps: Step[];
  cta: Cta;
  rubric_version: number | null;
  requirement_count: number;
  batch_count: number;
  screened: boolean;
  candidate_count: number;
  reviewed_count: number;
  bands: Record<string, number>;
  progress: string;
  /** P43 gate: true = published AND not awaiting HR-Head approval. */
  checklist_ready: boolean;
  checklist_block_reason: string | null;
  /** True when the role has a non-empty job description — screening needs both
   *  a JD and an approved checklist. */
  jd_present: boolean;
  /** P51: null when fresh — scores produced under older published rules otherwise. */
  staleness?: Staleness | null;
  /** Batch-level quality read; null before the first screen. */
  batch_quality?: BatchQuality | null;
  /** 2026-07-19: the run locking this batch (live or paused); null = unlocked. */
  screening?: ScreenLock | null;
  /** 2026-07-20: manual locks on the checklist / candidate list. */
  locks?: RoleLocks;
}

/** One manually-set lock (2026-07-20) — who froze it and when. */
export interface LockInfo {
  locked_by: string;
  locked_by_name: string;
  locked_at: string;
}

export interface RoleLocks {
  checklist: LockInfo | null;
  candidates: LockInfo | null;
}

/** GET /api/roles/{id}/jd — the full job description (Overview panel + checklist reference pane). */
export interface JdPayload {
  title: string;
  jd_text: string;
}

/** Structured 409 detail — duplicate iCIMS id (create + details PUT) or slug clash. */
export interface DuplicateReqDetail {
  message: string;
  field?: string;
  req_id: string;
  title: string;
}

/** POST /api/roles/{id}/decisions — the enriched single-decision response (F2 reconcile). */
export interface DecisionResponse {
  ok: boolean;
  action: string;
  decision: { candidate_id: string; action: string; reason: string; ts: string };
  decision_counts: { forward: number; discuss: number; not: number; awaiting: number };
}

/** GET /api/locations — everything the form needs to ask "where is this job?"
 *  (the global rollout, 2026-09-12). The form asks for a COUNTRY; which pack of
 *  hiring rules applies is a lookup, and in the United States it is a state
 *  question — for New York, a city one. */
export interface LocationsFeed {
  packs: LocationOption[];
  countries: CountryOption[];
  us_states: UsStateOption[];
  nyc_pack: string;
  nyc_state: string;
  /** Where this deployment stores and processes the data, in the deployment's own
   *  plain words (PROCESSING_LOCATIONS). null = the deployment hasn't said, and
   *  the app shows nothing rather than guessing. */
  processing_locations: string | null;
}

export interface CountryOption {
  code: string;
  name: string;
  /** null = a country we know and have no hiring rules for yet. Listed on purpose:
   *  "no rules for Canada yet" is an answer; leaving it out is not. */
  pack: string | null;
  aliases?: string[];
}

export interface UsStateOption {
  code: string;
  name: string;
  pack: string;
}

/** One set of hiring rules (a policy pack) — P45 keep/purge model. */
export interface LocationOption {
  code: string;
  label: string;
  law: string;
  keep_at_least_days: number | null;
  purge_by_days: number | null;
  /** 2026-09-06: the pack's own plain-language duties, for the hiring-rules popover.
   *  Displayed, never enforced. Optional so older fixtures still type-check. */
  obligations?: string[];
  notes?: string;
  monitoring_categories?: string[];
}

/** One person the app knows (GET /api/people, /api/people/directory) — 2026-09-06.
 *  `photo` is a data URI when the directory holds one (Graph), else null; `initials`
 *  + `hue` render the fallback avatar the same way on every surface. */
export interface Person {
  email: string;
  name: string;
  job_title: string;
  photo: string | null;
  initials: string;
  hue: string;
  source: string;
}

export interface PeopleSearch {
  people: Person[];
  source: "local" | "graph";
  note: string | null;
}

/** GET /api/careers/lookup — TEMPORARY iCIMS bridge; pre-fills the New-req form. */
export interface CareersLookupResult {
  /** The CITY from the posting/file, canonicalised (Gurgaon -> Gurugram). */
  work_place?: string | null;
  status: "ok" | "not_found" | "ambiguous" | "error" | "disabled";
  title?: string;
  jd_text?: string;
  job_location?: string | null;
  location_label?: string | null;
  country?: string | null;
  us_state?: string | null;
  source_url?: string;
  count?: number;
  note?: string;
}

/** POST /api/jd/parse — parsed upload of a JD file (permanent, always-on). */
export interface JdParseResult {
  /** The CITY from the posting/file, canonicalised (Gurgaon -> Gurugram). */
  work_place?: string | null;
  title: string;
  jd_text: string;
  job_location?: string | null;
  location_label?: string | null;
  /** ISO-3166 alpha-2 of the country the document states, and the US state when
   *  it is one — so the form lands on Hungary rather than on a pack code. */
  country?: string | null;
  country_name?: string | null;
  us_state?: string | null;
  /** The document named a country we have no hiring rules for. */
  country_without_pack?: boolean;
  /** ISO 639-1 of the language the document is written in. */
  language?: string | null;
}

/** GET /api/roles/{id}/quickview — the Requisitions-list hover popover (P32 renders it). */
export interface Quickview {
  jd_excerpt: string;
  checklist_version: number | null;
  requirement_count: number;
  screened_count: number;
  added_by: string | null;
  icims_req_id: string | null;
}

// --- GET /api/roles/{id}/trail — the workspace Audit tab ---

export interface TrailEntry {
  ts: string;
  kind: string;
  actor: string;
  summary: string;
}

export interface TrailQuote {
  name: string;
  action: string;
  reason: string;
  reviewer: string;
  ts: string;
}

export interface Trail {
  entries: TrailEntry[];
  decision_summary: { forward: number; discuss: number; not: number; quotes: TrailQuote[] };
  note: string;
}

// P33: one ordered checklist — a hard gate is a requirement with kind "hard_gate"
// (no weight; candidates missing it are tagged "Gate not met", never hidden).
export type RequirementKind = "nice_to_have" | "must_have" | "hard_gate";

/** The requisition's physical workplace + the recruiter's search radius (2026-08-20).
 *  NON-SCORING: reviewer context and a Review filter, never a weight or a gate — see
 *  models.WorkLocation for why (commute/address is a floor never-scored topic). */
export interface WorkLocation {
  place: string;
  /** null = no distance limit chosen. 0 = at this location only. */
  radius_km: number | null;
  /** The adjustable ceiling of the range control. */
  max_km: number;
  /** Server-rendered sentence, e.g. `Current location: Within 25 km from "Gurugram"`.
   *  Present on GET; omitted on PUT. */
  summary?: string;
  /** Is this place in the places table, i.e. can a distance be worked out from it?
   *  false means every candidate's distance reads "unknown" no matter what their
   *  résumé says — the gap is the JOB's city, and the surfaces that offer the filter
   *  say so once instead of letting 40 rows blame the résumés. Present on GET only. */
  place_known?: boolean;
}

export interface Requirement {
  id?: string;
  text: string;
  type?: string;
  kind: RequirementKind;
  importance: number;
  /** P43: the checklist critic's verdict on this row's text. */
  critic_level?: "warn" | null;
  /** Plain-language reason shown under a flagged row (empty unless warn). */
  critic_reason?: string;
}

// P43: a flagged checklist version awaiting HR-Head sign-off.
export interface ChecklistApproval {
  version: number;
  status: "none" | "pending" | "approved";
  flagged_requirement_ids: string[];
  requested_by?: string | null;
  requested_at?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
}

export interface Rubric {
  version: number | null;
  role_title?: string;
  requirements: Requirement[];
  /** Present only while the latest version has warn rows and isn't approved. */
  approval?: ChecklistApproval;
  /** Set by the save response: true when the just-saved version has warn rows. */
  needs_approval?: boolean;
}

export interface BatchFile {
  name: string;
}
/** A bulk file (e.g. iCIMS Bulk Print) split into individual résumés (temporary bridge). */
export interface BatchGroup {
  source: string;
  derived: string[];
  count: number;
  /** How many résumés the Bulk Print's table of contents listed. */
  expected?: number;
  /** expected − count: names we couldn't separate onto their own page. */
  shortfall?: number;
  /** Derived files still holding more than one résumé (refused at screening). */
  oversized?: string[];
  /** Identical slices dropped so the batch count can't over-report candidates. */
  duplicates_dropped?: number;
  /** How many of this packet's people are IN the batch — new files plus the ones
   *  already here from another export. The number a recruiter reaches by counting,
   *  so it is the number the header states. */
  present?: number;
}
/** Live state of the background résumé preparation (bulk-file splitting +
 *  duplicate checks). Uploads return instantly; the Candidates tab polls this
 *  while the server works through the staged files — the work is server-side,
 *  so leaving the page never interrupts it. */
export interface BatchPreparing {
  /** Work outstanding (staged files or a session in flight) — screening waits on this. */
  active: boolean;
  /** A worker process is running right now — removing/clearing waits on this. */
  live: boolean;
  status: "idle" | "queued" | "working" | "done" | "error";
  processed: number;
  pending: number;
  total: number;
  added: number;
  duplicates: number;
  unsupported: number;
  split_files: number;
  split_resumes: number;
  failed: number;
  /** The file being worked on right now. */
  current?: string | null;
  /** The finished session's plain summary ("40 added, 2 duplicates skipped · …"). */
  sentence?: string | null;
  /** Plain-language error when status is "error". */
  message?: string | null;
  /** True when a screening run is owed once preparation finishes ("upload and screen"). */
  auto_screen: boolean;
  /** True once a stop has been asked for and the worker hasn't wound up yet. */
  stopping: boolean;
}

export interface Batch {
  count: number;
  files: BatchFile[];
  folder: string;
  sentence?: string;
  removed?: number;
  /** Present when one or more uploaded files were split into multiple résumés. */
  groups?: BatchGroup[];
  preparing?: BatchPreparing;
}

// P35: read-only screening preflight — what a run would actually do right now.
export type PreflightFileStatus = "cached" | "new" | "updated";

export interface PreflightFile {
  name: string;
  status: PreflightFileStatus;
}

export interface Preflight {
  will_run: boolean;
  reason: "never_run" | "config_changed" | "files_changed" | "nothing_changed" | "empty_batch";
  changed: { checklist: boolean; rules: boolean; location: boolean;
             scoring_model: boolean; preferences: boolean };
  /** P51: the itemized "what changed" behind a config_changed reason. */
  details: DriftItem[];
  /** P51: null when nothing drifted; mode drives the Candidates-tab behavior. */
  stale: { mode: "locked" | "soft" | "auto"; has_decisions: boolean } | null;
  files: PreflightFile[];
  counts: { total: number; new: number; updated: number; cached: number; removed: number };
}

export interface ScreenJob {
  job_id: string;
  req_id: string;
  status: "running" | "paused" | "done" | "error" | "stopped";
  done: number;
  total: number;
  message: string;
  error: string;
  /** remaining time from the run's own progress estimate; null while unknown. */
  eta_seconds?: number | null;
  /** who started the run + when (in-process extras; empty from another instance). */
  actor?: string;
  started_utc?: string;
  /** the batch filenames this run set out to screen (its candidate set)… */
  planned?: string[];
  /** …and the ones already finished — rows flip "screening…" → "screened". */
  completed?: string[];
}

// The persistent screening-run history (who ran it, when, how it ended).
export interface ScreenRunEntry {
  ts: string;
  kind: "screen_completed" | "screen_stopped" | "screen_paused" | "screen_failed" | "screen_interrupted";
  actor: string;
  summary: string;
  run_id?: string;
  /** gate_passers is a tally of the AI's gate results — absent without verdict_view. */
  counts?: { scored: number; resumes_in: number; gate_passers?: number; errors: number };
  duration_seconds?: number | null;
}

/** 2026-07-19: the run locking a req's batch (live or paused) — nobody else may
 *  screen or change the batch until it finishes or is cancelled. */
export interface ScreenLock {
  job_id: string;
  status: "running" | "paused";
  started_by: string;
  done: number;
  total: number;
}

export interface ScreenRunsResponse {
  runs: ScreenRunEntry[];
  note: string | null;
}

export interface Chip {
  text: string;
  kind: "strength" | "watchout";
}

export interface CardGates {
  total: number;
  failed: string[]; // the failed gate texts (plain language)
  /** 2026-09-07: the subset of `failed` the résumé gave PARTIAL evidence for. A
   *  machine needs "full" to pass a gate; "close and not counted" is a human's
   *  call, "nothing there" is settled — the card distinguishes them. */
  partial?: string[];
}

/** One published checklist version (GET …/rubric/versions). `screening_runs` is
 *  what makes a version safe or unsafe to leave behind: candidates were judged
 *  against it. */
export interface RubricVersion {
  version: number;
  published_ts: string;
  published_by: string;
  requirement_count: number;
  gate_count: number;
  must_count: number;
  is_current: boolean;
  screening_runs: number;
  last_screened_ts: string | null;
}

export interface ReviewCard {
  rank: number;
  candidate_id: string;
  /** Location lens (2026-08-26), NON-SCORING throughout — see models.WorkLocation.
   *  Computed at SCREENING time and frozen into the run artifact, so Review reads a
   *  number rather than re-parsing résumés. */
  /** The requisition's place, echoed onto every card for labelling. */
  location_place?: string;
  /** The place the résumé states (canonicalised), or null = states none. */
  location_stated?: string | null;
  /** Approximate great-circle km. null = unknown, which is NEVER "far away". */
  location_km?: number | null;
  /** True only when both places resolved, i.e. location_km is a real number. */
  location_resolved?: boolean;
  /** Inside the requisition's radius? null = unknown distance, and a null must never
   *  be filtered out — an omission is not evidence of distance. */
  location_within?: boolean | null;
  /** The plain sentence, rendered server-side so the wording lives in one place. */
  location_note?: string;
  name: string;
  /* The AI's verdict (band … math) reaches only a viewer holding verdict_view — HR
   * roles (InfoSec, 2026-10-14). For everyone else the server leaves these out and
   * orders the list by decision then name; render by presence, never an empty slot
   * (verdict.ts). */
  band?: string;
  band_label?: string;
  score?: number;
  confidence?: string;
  flag?: boolean;
  chips?: Chip[];
  gates?: CardGates;
  must_have_gaps?: string[]; // missing must-have requirement TEXTS (CSV export)
  // The team's decision — absent outside the requisition's team (2026-10-21; the
  // payload says decisions_visible: false), so read every one as "maybe missing".
  decided?: string | null;
  decided_reason?: string | null; // the recruiter's note ("" when none was typed)
  decided_ts?: string | null;
  decided_by?: string | null; // who recorded the decision/note (attribution for other TAs)
  /** 2026-07-20: the decision was carried forward from scores that have since
   *  moved — the card shows a "reconfirm" badge. */
  decided_reconfirm?: boolean;
  hm_take?: HmTake | null; // P41/P42: the assigned HM's take, when one exists
  /** 2026-07-19: comment-thread size (viewer-filtered for the HM). */
  comments?: number;
  /** P51: the score popover's compact arithmetic. */
  math?: ScoreMathBrief;
}

export interface BatchDecisionResponse {
  ok: boolean;
  applied: number;
  decisions: Record<string, { action: string; reason: string }>;
  decision_counts: { forward: number; discuss: number; not: number; awaiting: number };
}

export interface ReviewResponse {
  ready: boolean;
  req_id: string;
  title: string;
  req_code?: string | null; // the iCIMS id, for the CSV export + filename
  run_id?: string;
  location_display: string;
  /** The workplace + search radius (2026-08-20); null when not set. */
  work_location?: WorkLocation | null;
  jurisdiction?: string;
  meta?: { screened: number; rubric?: string; when?: string };
  /** Only `total` reaches a viewer without verdict_view — the rest are band/gate
   *  tallies, i.e. the AI's verdict on the pool. */
  counts?: {
    strong?: number;
    possible?: number;
    not_a_fit?: number;
    needs_look?: number;
    gates_passed?: number;
    gate_not_met?: number;
    total: number;
  };
  decision_counts?: { forward: number; discuss: number; not: number; awaiting: number };
  /** Whether this viewer sees the team's decisions (2026-10-21). False outside the
   *  requisition's team: the cards carry no decision, and `decisions_note` is the
   *  sentence to show in place of the decision controls. Absent = true. */
  decisions_visible?: boolean;
  decisions_note?: string;
  cards?: ReviewCard[];
  /** Files the run refused to score (un-split Bulk Print, merged packet, unreadable). */
  skipped?: { file: string; reason: string }[];
  /** P51: null when fresh; every card shares the run, so this drives all row icons. */
  staleness?: ReviewStaleness | null;
  /** 2026-07-19: the share-with-HM state for the CURRENT run. hm_shared false +
   *  an HM viewer = the empty "nothing shared yet" state. The HM sees the
   *  share's SNAPSHOT, never a live sync. */
  hm_shared?: boolean;
  hm_share?: { shared_by: string | null; ts: string | null; run_id: string } | null;
  /** What a re-share would push — drives "Send updates to hiring manager"
   *  (null on the HM's own view / before any share). Decisions are a snapshot;
   *  a changed decision or a newly-added candidate makes it pending. Comments
   *  reach the HM live and are not counted here. */
  hm_updates?: { pending: boolean; decisions: string[]; new_candidates: string[] } | null;
  /** The batch-level quality read (recruiter view only; null for the HM). */
  batch_quality?: BatchQuality | null;
}

/** One emoji reaction group on a comment (2026-07-20). */
export interface ReactionView {
  emoji: string;
  count: number;
  mine: boolean; // the viewer reacted with this emoji
  who: string[]; // display names, for the hover ("Bhavika, Rahul reacted")
}

/** One comment on a candidate's collaboration thread (2026-07-19). */
export interface CandidateComment {
  comment_id: string;
  author: string;
  author_name: string;
  author_role: string; // "Hiring manager" | "Recruiting team"
  text: string;
  ts: string;
  /** 2026-07-20: emails @-mentioned in the body + emoji reactions. */
  mentions?: string[];
  reactions?: ReactionView[];
}

/** A person you can @-mention on a requisition (2026-07-20). */
export interface Participant {
  email: string;
  name: string;
  role: string; // "Recruiting team" | "Hiring manager"
}

/** GET /api/notifications — one bell entry (a `notify` event addressed to me). */
export interface AppNotification {
  ts: string;
  summary: string;
  from: string;
  /** 2026-07-20: the actor's display NAME + the requisition TITLE, so the bell
   *  shows names (email on hover) and can group by requisition. */
  from_name?: string;
  req_title?: string | null;
  req_id: string | null;
  kind: string;
  /** Deep-link ids (2026-07-19): candidate_ids, candidate_id, comment_id,
   *  suggestion_id, change_id… whatever the notifying flow attached. */
  data?: Record<string, unknown>;
}

/** One dated position behind a length-of-experience requirement. The dates were read
 *  off the résumé by the model and the quote verified against it; `months` and the
 *  ordering are the SERVER's arithmetic (review._span_view) — nothing here parses a
 *  date, so the journey the drawer draws and the total can never disagree. */
export interface ReqSpan {
  quote: string;
  /** The job title held in this position, verified against the résumé like any other
   *  quoted string. "" when the résumé stated none we could verify — never the
   *  employer and never the person's name, which the quoted line often carries
   *  instead (redacted to "[CANDIDATE]"). Present from the p36 screen onward. */
  title?: string;
  start: string;
  end: string;
  /** This position's own length, both ends counted. null = unreadable dates. */
  months?: number | null;
  /** Runs up to today ("present", "till date"…). */
  ongoing?: boolean;
  /** p38 (2026-09-25): every dated job comes back. false = listed for context — the
   *  requirement is not about this job — with the AI's reason in `why_not`. Absent on
   *  an older run, whose spans were all counted ones. */
  counted?: boolean;
  why_not?: string;
}

export interface DetailReq {
  label: string;
  match: string;
  glyph: string;
  must: boolean;
  gate: boolean;
  evidence: string;
  note: string;
  /** 2026-09-07: the AI judged this met but its quote didn't check out against the
   *  résumé, so the score leaves it out. Our check being careful — not the candidate
   *  falling short — so the row says so and the reviewer settles it. */
  unverified?: boolean;
  /** A LENGTH-of-experience requirement is settled by arithmetic (2026-09-07): these
   *  are the dated role lines it was worked out from, each verified against the
   *  résumé, and `months` is the total OUR code made of them — never the model's
   *  claim. Shown so a reviewer can check the addition rather than trust it. */
  spans?: ReqSpan[];
  months?: number | null;
  /** Every dated job on the résumé, counted or not, merged the same way (p38). null on
   *  an older run. `months` is only what THIS requirement counted — a "7 years managing
   *  AP regional projects" line can count two of five jobs, and without this number the
   *  scoped total read as the candidate's whole career. */
  months_total?: number | null;
}

export interface DetailNote {
  signal: string;
  points: number;
  evidence: string;
  rationale: string;
  suspended: boolean;
  /** Critic flagged this note "worth a look" — stays in the score (older
   *  fixtures may omit it). Independent of the points' sign. */
  flagged?: boolean;
}

// P40: an applied HR-preference nudge, shown under Context notes with a +N/−N chip.
export interface PrefNote {
  text: string;
  owner_label: string; // "Your preference" / "{owner}'s preference"
  points: number;
  evidence: string;
}

export interface CandidateDetail {
  candidate_id: string;
  name: string;
  /* The AI's verdict — absent for a viewer without verdict_view (see ReviewCard). */
  band?: string;
  band_label?: string;
  score?: number;
  confidence?: string;
  sub?: string;
  verdict?: string;
  flag?: boolean;
  flag_note?: string;
  gate_warn?: string; // non-empty = the drawer's gate headline ("2 hard requirements not met")
  /** 2026-09-07: one entry per unmet hard gate, so the drawer lists them instead of
   *  running them into a paragraph. `state: "partial"` = the résumé showed something
   *  short of the bar — the case that needs a human, not a machine. */
  gate_items?: { text: string; state: "none" | "partial" | "unverified"; evidence: string; note: string }[];
  /** The pass/fail rule + the "nobody is rejected" caveat, said once. */
  gate_rule?: string;
  /** 2026-09-07: each "closer look" reason with somewhere to GO — `where` names a
   *  drawer section (verdict | evidence | score | resume), `action` its link text. */
  flag_items?: { text: string; where: string; action: string }[];
  /** The requisition's place + what this résumé states (NON-SCORING; see ReviewCard). */
  location_place?: string;
  location_stated?: string | null;
  location_km?: number | null;
  location_note?: string;
  strengths?: string[];
  watchouts?: string[];
  req_summary?: string;
  reqs?: DetailReq[];
  notes?: DetailNote[];
  pref_notes?: PrefNote[]; // P40; the drawer guards with ?? [] for older fixtures
  hm_take?: HmTake | null; // P41/P42: the assigned HM's take on this candidate
  /** needs_reconfirm (2026-07-20): the decision was carried forward from a run
   *  whose scores have since moved — show a "reconfirm" prompt. */
  decision?: { action: string; reason: string; ts?: string; reviewer?: string | null;
               needs_reconfirm?: boolean } | null;
  /** See ReviewResponse.decisions_visible — absent outside the requisition's team. */
  decisions_visible?: boolean;
  decisions_note?: string;
  /** 2026-07-19: the collaboration thread (LIVE for the HM since 2026-07-20). */
  comments?: CandidateComment[];
  /** 2026-07-20: the viewer's own PRIVATE note ({text, ts}) — recruiter only. */
  self_note?: { text: string; ts: string } | null;
  resume_media_type: string | null; // "pdf" embeds inline; "docx" = download only
  /** P51: the full "How this score adds up" trace (null when it couldn't be built). */
  score_math?: ScoreMath | null;
}

export interface Signal {
  id: string;
  display_name: string;
  description: string;
  definition: string;
  kind: string;
  direction: string;
  cap_points: number;
  enabled_default: boolean;
  retired: boolean;
  job_relatedness: string;
}

export interface Excluded {
  id: string;
  reason: string;
  locked: boolean;
}

export interface SignalsResponse {
  version: number;
  signals: Signal[];
  excluded: Excluded[];
}

export interface BlockedPattern {
  id: string;
  name: string;
  reason: string;
  severity: "blocked" | "warn";
  floor: boolean; // universal base floor (inherited everywhere) vs region-specific
  stages: string[];
  phrases: string[];
  regexes: string[];
  source: "seed" | "derived" | "manual";
}

export interface Jurisdiction {
  code: string;
  name: string;
  law: string;
  active: boolean;
  badge: string | null;
  obligations: string[]; // effective (base ⊕ overlay) — for display
  keep_at_least_days: number | null;
  purge_by_days: number | null;
  monitoring_categories: string[] | null;
  blocked_patterns: BlockedPattern[]; // effective (universal floor ⊕ region) — display
  own_blocked_patterns: BlockedPattern[]; // this overlay's own (region-specific) — editable
  own_obligations: string[]; // overlay's own additions — for editing
  own_keep_at_least_days: number | null;
  own_purge_by_days: number | null;
  own_monitoring_categories: string[] | null;
  /** This pack's own dial over the model's signals — which fire here, their caps,
   *  their market settings. Absent keys take the model's own values. */
  own_signal_overrides?: Record<string, {
    enabled?: boolean; cap_points?: number; params?: Record<string, unknown>;
  }>;
  version: number | null;
}

export interface BlockedPatternDerivation {
  patterns: BlockedPattern[];
  conflicts: { signal_id: string; note: string }[];
  source: "model" | "heuristic";
  note: string;
}

export interface JurisdictionsResponse {
  default: string;
  jurisdictions: Jurisdiction[];
}

export interface RbacCap {
  key: string;
  label: string;
}
export interface RbacRow {
  name: string;
  entra: string;
  cells: Record<string, boolean>;
}
export interface RbacResponse {
  caps: RbacCap[];
  rows: RbacRow[];
}

export interface Run {
  req_id: string;
  role: string;
  run_id: string;
  jurisdiction: string;
  when: string;
  meta: string;
}
export interface RunsResponse {
  runs: Run[];
}

export interface AuditEntry {
  ts: string;
  action: string;
  target?: string;
  author?: string;
  rationale?: string;
  summary?: string;
  version?: number;
}
export interface AuditLogResponse {
  entries: AuditEntry[];
}

// --- Audit Explorer (P46; shapes owned by api/explorer.py) ------------------------

export interface FeedEntry {
  ts: string | null;
  kind: string; // Decision | Preference | Rules | Close | Data | Views
  actor: string;
  summary: string;
  req_id: string | null;
  candidate: string | null;
  region: string | null;
  source: string;
}
export interface FeedResponse {
  entries: FeedEntry[];
  kinds: string[];
}

export interface CandidateMatch {
  req_id: string;
  req_title: string;
  candidate_id: string;
  name: string;
  region: string | null;
}

export interface TimelineStep {
  step: string; // screened | hm_take | decision | handed_off
  ts?: string | null;
  score?: number;
  band?: string;
  gates?: { total: number; failed: string[] };
  run_id?: string;
  checklist_version?: string | null;
  pack?: string | null;
  /** P51: the scoring-model stamp this run scored under ("v3 (hash)"). */
  scoring_model?: string | null;
  who?: string;
  take?: string;
  action?: string;
  reason?: string;
  note?: string;
}
export interface CandidateTimelineData {
  req_id: string;
  candidate_id: string;
  name: string;
  tz: string;
  steps: TimelineStep[];
  targets: {
    audit_pack_url: string;
    checklist_version: string | null;
    jd_ref: string;
    run_id: string;
  };
}

export interface ReqAuditEvent {
  ts: string | null;
  kind: string;
  actor: string;
  summary: string;
}
export interface ReqAuditCard {
  req_id: string;
  title: string;
  req_code: string | null;
  jurisdiction: string;
  region: string;
  status: string;
  created_by: string | null;
  events: ReqAuditEvent[];
}
export interface ReqAuditResponse {
  requisitions: ReqAuditCard[];
}

export interface RuleEntry {
  ts: string | null;
  kind: string;
  action: string;
  actor: string;
  summary: string | null;
  rationale: string | null;
  version: number | null;
  /** P51: the second leader who approved the change proposal (null = direct publish). */
  approved_by?: string | null;
  region: string | null;
}
export interface RulesResponse {
  entries: RuleEntry[];
}

export interface LegalHold {
  by: string;
  ts: string;
  reason: string;
}
export interface RetentionRow {
  req_id: string;
  title: string;
  jurisdiction: string;
  pack: string | null;
  closed_at: string | null;
  last_run_at: string | null;
  purge_due: string | null;
  keep_audit_until: string | null;
  status: string; // scheduled | on_hold | awaiting_close
  hold: LegalHold | null;
}
export interface RetentionResponse {
  queue: RetentionRow[];
  summaries: string[];
}

export interface AuditReport {
  key: string;
  label: string;
  status: string; // "ready" | "Phase 2"
}
export interface ReportsResponse {
  reports: AuditReport[];
}

// Data page (P47): the storage map + delete responses.
export interface StorageRow {
  kind: string;
  path: string;
  format: string;
  note: string;
  size: string;
}
export interface DataSummary {
  rows: StorageRow[];
  data_root: string;
}

/** GET /api/scoring-model/effective — the one global model as it applies in one
 *  jurisdiction (the global rollout, 2026-09-12).
 *
 *  Every row is derived from the SAME call a screening run makes, so this view
 *  cannot drift from what a run would do. `scope: "global"` on `structural` is
 *  the server saying which fields no policy pack can touch. */
export interface EffectiveModelView {
  scope: "global" | "jurisdiction";
  jurisdiction: string | null;
  pack: string | null;
  /** e.g. the note a US state without its own pack carries about US-default. */
  notes: string[];
  model: { version: number; stamp: string };
  pack_version: number | null;
  structural: {
    scope: "global";
    bands: { strong_at: number; possible_at: number; cap_if_must_have_missing: string };
    clamp: { min: number; max: number };
    coverage: { match_value: Record<string, number> };
    adjustments: { total_cap_points: number };
    preferences: { per_preference_cap_points: number; review_months: number };
    confidence: Record<string, number>;
    batch_quality: { top_n: number; strong_at: number; okay_at: number };
  };
  signals: EffectiveSignalRow[];
  critic: {
    universal_floor: EffectivePattern[];
    model_topics: { id: string; name: string; reason: string; floor: boolean; stages: string[] }[];
    pack_exclusions: { id: string; reason: string }[];
    pack_blocked_patterns: EffectivePattern[];
    critic_added_topics: { id: string; name: string; stages: string[] }[];
    total_never_scored: number;
  };
}

export interface EffectiveSignalRow {
  id: string;
  display_name: string;
  description: string;
  kind: string;
  direction: string;
  retired: boolean;
  model_cap: number;
  /** null when the signal does not fire in this jurisdiction. */
  effective_cap: number | null;
  enabled_in_model: boolean;
  enabled_here: boolean;
  origin: "model" | "pack";
  enabled_origin: "model" | "pack" | "exclusion" | "retired" | "cap_zero";
  cap_origin: "model" | "pack";
  /** A pack may RAISE a cap above the model's — allowed, and marked because it
   *  is the one direction a reader would not assume. */
  cap_raised: boolean;
  disabled_reason: string | null;
  params: Record<string, unknown>;
  param_origins: Record<string, string>;
}

export interface EffectivePattern {
  id: string;
  name: string;
  reason: string;
  severity: string;
  stages: string[];
  phrase_count?: number;
  /** Whether this reaches the critic's deterministic screen (scoring/narrative
   *  staged AND carrying literal phrases), or is checked when someone writes a
   *  preference or a checklist row — a different moment. */
  critic_screened?: boolean;
}


/** GET /api/access/grants — who is scoped to which region (2026-09-12).
 *  `status` is honest about the three states: ready, not switched on for this
 *  deployment, or database unreachable. Each carries its own `message`. */
export interface AccessGrants {
  status: "ok" | "unseeded" | "unavailable";
  message: string;
  regions: { code: string; name: string }[];
  people: AccessPerson[];
}

export interface AccessPerson extends Person {
  regions: string[];
  region_names: Record<string, string>;
  all_regions: boolean;
  /** Nobody has scoped them — which this app treats as UNRESTRICTED, on purpose. */
  unscoped: boolean;
  home: string | null;
  home_source: "chosen" | "country" | "sole_grant" | null;
}
