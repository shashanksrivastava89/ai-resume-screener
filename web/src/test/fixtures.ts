// Synthetic role-detail payloads for workspace tests (P31). Mirrors what
// GET /api/roles/{id} returns after P29 — never real screening data.
import type { JdPayload, Quickview, RoleDetail, Trail } from "../types";

export function roleFixture(overrides: Partial<RoleDetail> = {}): RoleDetail {
  return {
    req_id: "financial-analyst",
    title: "Financial Analyst",
    jd_ref: "jds/financial-analyst.md",
    job_location: "IN",
    location_display: "India",
    law: "DPDP Act 2023",
    stage: "screened",
    stage_label: "Screened",
    stage_index: 3,
    steps: [],
    cta: { label: "Start review", goto: "review", magic: false },
    rubric_version: 2,
    requirement_count: 6,
    batch_count: 4,
    screened: true,
    candidate_count: 4,
    reviewed_count: 1,
    bands: { Strong: 1, Possible: 2, "Not a fit": 1 },
    progress: "4 screened · 1 reviewed",
    checklist_ready: true,
    checklist_block_reason: null,
    jd_present: true,
    req_code: "REQ-2041",
    lifecycle: {
      status: "active",
      outcome: null,
      closed_at: null,
      closed_by: null,
      snooze_until: null,
      created_by: "ta@example.com",
      created_at: "2026-07-01T08:00:00+00:00",
    },
    last_activity_utc: "2026-07-10T08:00:00+00:00",
    suggest_close: { suggest: false, decided: 1, quiet_days: 2 },
    jd_language: null,
    hm: null,
    created_by: "ta@example.com",
    region: "IN",
    tz: "Asia/Kolkata",
    ...overrides,
  };
}

export const QUICKVIEW: Quickview = {
  jd_excerpt: "Prepare monthly closes, GST/TDS filings and variance analysis for the India finance team.",
  checklist_version: 2,
  requirement_count: 6,
  screened_count: 4,
  added_by: "ta@example.com",
  icims_req_id: "REQ-2041",
};

export const JD_FIXTURE: JdPayload = {
  title: "Financial Analyst",
  jd_text:
    "Prepare monthly closes, GST/TDS filings and variance analysis for the India finance team.\n\n" +
    "Responsibilities:\n- Own the monthly close and the sub-ledger reconciliations end to end\n" +
    "- GST/TDS filings and reconciliation across all registered entities\n" +
    "- Variance analysis and board decks with commentary for the leadership review\n" +
    "- Partner with the FP&A team on the rolling forecast and the annual operating plan\n" +
    "- Support statutory and internal audits with schedules and evidence",
};

export const TRAIL: Trail = {
  entries: [
    { ts: "2026-07-08T10:00:00+00:00", kind: "run", actor: "", summary: "Screened 4 candidates — IN hiring rules · checklist v2 · run 20260708-1000" },
    { ts: "2026-07-02T09:00:00+00:00", kind: "checklist", actor: "", summary: "Checklist v2 approved" },
    { ts: "2026-07-01T08:00:00+00:00", kind: "created", actor: "ta@example.com", summary: "Requisition created" },
  ],
  decision_summary: {
    forward: 1,
    discuss: 0,
    not: 0,
    quotes: [
      { name: "resume-anita.pdf", action: "Move forward", reason: "Strong GST filing evidence", reviewer: "ta@example.com", ts: "2026-07-09T11:00:00+00:00" },
    ],
  },
  note: "Hire status lives in iCIMS — this trail records the screening and decisions made here.",
};

// P51: the published scoring values + resolved display strings (GET /api/scoring-config).
export const SCORING_CONFIG = {
  version: 1,
  published_by: null,
  approved_by: null,
  published_at: null,
  bands: { strong_at: 88, possible_at: 60, cap_if_must_have_missing: "Possible" },
  clamp: { min: 0, max: 100 },
  coverage: { match_value: { full: 1.0, partial: 0.5, none: 0.0 } },
  adjustments: { total_cap_points: 8 },
  preferences: { per_preference_cap_points: 2, review_months: 6 },
  confidence: {
    unmeasured_extraction_value: 0.85, high_requires_at_least: 0.9,
    low_below: 0.75, low_when_downgraded_findings_over: 2,
  },
  display: {
    adjustment_cap_note: "Each factor is capped so no single one can swing a decision.",
    total_cap_note: "All context notes together can move a score by at most 8 points.",
    preference_cap_note: "Each preference can move a score by at most 2 points and never hides anyone.",
    review_note: "Approvals expire after 6 months and need a re-review.",
    band_cap_note: 'A candidate missing a must-have can rank no higher than "Possible match", whatever the number says.',
  },
};

// A compact governed scoring-model document (2 signals + 2 topics) for the
// Scoring model page — small enough to reason about in assertions.
export function scoringDocFixture() {
  return {
    version: 1,
    bands: { strong_at: 88, possible_at: 60, cap_if_must_have_missing: "Possible" },
    clamp: { min: 0, max: 100 },
    coverage: { match_value: { full: 1.0, partial: 0.5, none: 0.0 } },
    adjustments: { total_cap_points: 8 },
    preferences: { per_preference_cap_points: 2, review_months: 6 },
    confidence: {
      unmeasured_extraction_value: 0.85, high_requires_at_least: 0.9,
      low_below: 0.75, low_when_downgraded_findings_over: 2,
    },
    batch_quality: { top_n: 5, strong_at: 75, okay_at: 55 },
    signals: [
      { id: "quota_attainment_evidence", display_name: "Quota attainment (sales)",
        description: "Concrete stated attainment.", definition: null, kind: "judged",
        direction: "plus", cap_points: 5, enabled_default: true, retired: false,
        job_relatedness: "Documented attainment is direct evidence.", notes: null },
      { id: "skill_recency", display_name: "Skill recency",
        description: "How recently the JD's core skills were used.", definition: null,
        kind: "computed", direction: "both", cap_points: 3, enabled_default: true,
        retired: false, job_relatedness: "Recent skills transfer immediately.", notes: null },
    ],
    topics: [
      { id: "employment_gaps", name: "Employment gaps", floor: true,
        reason: "Gaps are life — never scored.", added_by: null, added_at: null,
        stages: ["scoring", "checklist", "preferences"],
        phrases: ["employment gap", "career gap"] },
      { id: "demographic_language", name: "Demographic language", floor: true,
        reason: "Identity is removed before scoring.", added_by: null, added_at: null,
        stages: ["scoring", "narrative"], phrases: ["male", "female"] },
    ],
    display: SCORING_CONFIG.display,
  };
}

export function scoringModelReadFixture() {
  const doc = scoringDocFixture();
  return {
    version: 1,
    knobs: [
      { path: "bands.strong_at", value: 88, label: "“Strong match” starts at",
        help: "Worth a screening call on the evidence alone.",
        technical_term: "Band threshold on the clamped final score.",
        usual_bounds: [75, 95], group: "bands" },
      { path: "bands.possible_at", value: 60, label: "“Possible match” starts at",
        help: "Worth a look; below this reads “Weak match”.", technical_term: null,
        usual_bounds: [40, 75], group: "bands" },
      { path: "clamp.min", value: 0, label: "Score floor",
        help: "The final number never drops below this.", technical_term: null,
        usual_bounds: [0, 0], group: "bands" },
      { path: "clamp.max", value: 100, label: "Score ceiling",
        help: "…and never rises above this.", technical_term: null,
        usual_bounds: [100, 100], group: "bands" },
      { path: "coverage.match_value.full", value: 1.0, label: "Clear evidence",
        help: "Pays the line's full weight.", technical_term: null,
        usual_bounds: [0.8, 1.0], group: "credits" },
      { path: "coverage.match_value.partial", value: 0.5, label: "Some evidence",
        help: "A near-miss stays visible.", technical_term: null,
        usual_bounds: [0.25, 0.75], group: "credits" },
      { path: "coverage.match_value.none", value: 0.0, label: "No evidence",
        help: "Absence is never punished.", technical_term: null,
        usual_bounds: [0, 0], group: "credits" },
      { path: "adjustments.total_cap_points", value: 8, label: "All AI signals together, at most",
        help: "Every signal combined can never move a score more than this.",
        technical_term: "Total cap.", usual_bounds: [4, 15], group: "signals" },
      { path: "preferences.per_preference_cap_points", value: 2, label: "Cap per preference",
        help: "The most one preference can nudge any score.", technical_term: null,
        usual_bounds: [0, 4], group: "preferences" },
      { path: "preferences.review_months", value: 6, label: "Approval re-review window",
        help: "Flagged preferences expire.", technical_term: null,
        usual_bounds: [3, 12], group: "preferences" },
      { path: "confidence.high_requires_at_least", value: 0.9, label: "“High” needs extraction above",
        help: "Clean digital text reads ≈0.95.", technical_term: null,
        usual_bounds: [0.85, 0.98], group: "confidence" },
      { path: "confidence.low_below", value: 0.75, label: "“Low” below",
        help: "A blurry scan means uncertainty.", technical_term: null,
        usual_bounds: [0.6, 0.85], group: "confidence" },
      { path: "confidence.low_when_downgraded_findings_over", value: 2,
        label: "Downgraded claims before “Low”", help: "Corrected claims.",
        technical_term: null, usual_bounds: [1, 5], group: "confidence" },
      { path: "batch_quality.top_n", value: 5, label: "Judged on the top",
        help: "How many of the batch's best gate-passing candidates the batch read averages.",
        technical_term: "Batch score = mean(final score of the top N gate-passing candidates).",
        usual_bounds: [3, 10], group: "batch" },
      { path: "batch_quality.strong_at", value: 75, label: "“Strong batch” starts at",
        help: "The top candidates average this or better.", technical_term: null,
        usual_bounds: [65, 90], group: "batch" },
      { path: "batch_quality.okay_at", value: 55, label: "“Okay batch” starts at",
        help: "Workable but thin — below this the batch reads “Weak”.", technical_term: null,
        usual_bounds: [45, 70], group: "batch" },
    ],
    display: [],
    signals: doc.signals,
    topics: doc.topics,
    model: doc,
    versions: [
      { version: 1, ts: "2026-07-16T09:00:00+00:00", author: "hr@example.com",
        approved_by: "legal@example.com", rationale: "July calibration",
        summary: "“Strong match” starts at: 90 → 88" },
    ],
    median_recent_move: 6,
  };
}

export const PREVIEW_FIXTURE = {
  result: { final_score: 71.5, band: "Possible", band_label: "Possible match",
            eligible: true, gate_passer: true, confidence: "medium", summary: "ok" },
  base: { total: 66, coverage_pct: 66 },
  enforcement_notes: [],
  waterfall: [
    { step: "base", label: "Checklist coverage", delta: 66, running: 66, note: null },
    { step: "adjustment", label: "Quota attainment (sales)", delta: 3, running: 69, note: null },
    { step: "adjustment", label: "Skill recency", delta: 1.5, running: 70.5, note: null },
    { step: "suspended", label: "Hands-on currency", delta: 0, running: 70.5,
      note: "set aside by the fairness check — not counted" },
    { step: "preference", label: "Notice ≤ 30 days (TA preference)", delta: 1, running: 71.5, note: null },
    { step: "band", label: "71.5 ≥ 60 → Possible match", delta: 0, running: 71.5, note: null },
  ],
};

/** GET /api/locations — the real feed shape (the global rollout, 2026-09-12).
 *  ONE fixture, because every page that asks "where is this job?" asks the same
 *  endpoint, and six hand-rolled copies of it is how a shape change turns into
 *  six unrelated-looking failures. */
export const LOCATIONS_FEED = {
  packs: [
    { code: "US-CA", label: "California", law: "FEHA / CCPA / CPRA",
      keep_at_least_days: 1460, purge_by_days: 1460, obligations: [], notes: "",
      monitoring_categories: [] },
    { code: "EU", label: "European Union", law: "AI Act + GDPR",
      keep_at_least_days: 365, purge_by_days: 180, obligations: [], notes: "",
      monitoring_categories: [] },
    { code: "IN", label: "India", law: "DPDP Act 2023",
      keep_at_least_days: 1095, purge_by_days: 180, obligations: [], notes: "",
      monitoring_categories: [] },
    { code: "US-NYC", label: "New York City", law: "Local Law 144",
      keep_at_least_days: 1095, purge_by_days: 365, obligations: [], notes: "",
      monitoring_categories: [] },
    { code: "US-default", label: "United States", law: "EEOC / Title VII / ADA / ADEA",
      keep_at_least_days: 730, purge_by_days: 365, obligations: [], notes: "",
      monitoring_categories: [] },
  ],
  countries: [
    { code: "CA", name: "Canada", pack: null },
    { code: "HU", name: "Hungary", pack: "EU" },
    { code: "IN", name: "India", pack: "IN" },
    { code: "US", name: "United States", pack: "US-default" },
  ],
  us_states: [
    { code: "CA", name: "California", pack: "US-CA" },
    { code: "NY", name: "New York", pack: "US-default" },
    { code: "TX", name: "Texas", pack: "US-default" },
  ],
  nyc_pack: "US-NYC",
  nyc_state: "NY",
  processing_locations: null,
};

/** GET /api/jurisdictions — enough of it for the Scoring page's location lens. */
export const JURISDICTIONS_FIXTURE = {
  default: "IN",
  jurisdictions: [
    { code: "EU", name: "European Union", law: "AI Act + GDPR", active: true, badge: null,
      obligations: [], keep_at_least_days: 365, purge_by_days: 180,
      monitoring_categories: [], blocked_patterns: [], own_blocked_patterns: [],
      own_obligations: [], own_keep_at_least_days: null, own_purge_by_days: null,
      own_monitoring_categories: [], version: 1 },
    { code: "IN", name: "India", law: "DPDP Act 2023", active: true, badge: null,
      obligations: [], keep_at_least_days: 1095, purge_by_days: 180,
      monitoring_categories: [], blocked_patterns: [], own_blocked_patterns: [],
      own_obligations: [], own_keep_at_least_days: null, own_purge_by_days: null,
      own_monitoring_categories: [], version: 2 },
  ],
};

/** GET /api/scoring-model/effective?jurisdiction=EU — the one global model as the
 *  EU pack applies it: `language_evidence` off, everything structural identical. */
export const EFFECTIVE_EU = {
  scope: "jurisdiction",
  jurisdiction: "EU",
  pack: "EU",
  notes: [],
  model: { version: 1, stamp: "v1 (abc123def456)" },
  pack_version: 1,
  structural: {
    scope: "global",
    bands: { strong_at: 85, possible_at: 60, cap_if_must_have_missing: "Possible" },
    clamp: { min: 0, max: 100 },
    coverage: { match_value: { full: 1, partial: 0.5, none: 0 } },
    adjustments: { total_cap_points: 8 },
    preferences: { per_preference_cap_points: 2, review_months: 6 },
    confidence: {},
    batch_quality: { top_n: 7, strong_at: 85, okay_at: 55 },
  },
  signals: [
    { id: "language_evidence", display_name: "Language evidence", description: "",
      kind: "judged", direction: "plus", retired: false, model_cap: 2,
      effective_cap: null, enabled_in_model: true, enabled_here: false,
      origin: "pack", enabled_origin: "pack", cap_origin: "model", cap_raised: false,
      disabled_reason: "turned off by the EU pack", params: {}, param_origins: {} },
  ],
  critic: {
    universal_floor: [
      { id: "protected_demographic", name: "Protected attributes",
        reason: "Never a screening factor.", severity: "blocked",
        stages: ["preferences", "checklist"] },
    ],
    model_topics: [],
    pack_exclusions: [],
    pack_blocked_patterns: [],
    critic_added_topics: [],
    total_never_scored: 9,
  },
};
