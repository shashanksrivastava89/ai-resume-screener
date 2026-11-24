// Client-side helpers for the Scoring model page: draft-change counting, the
// LIVE draft-bar warnings (a faithful port of the server's system_warnings —
// the server recomputes the authoritative set at propose time), the synthetic
// example candidate, and shared formatters. Ported from the v3 prototype's
// diffCfg/computeWarnings/_sample.
import type { ModelSignal, ScoringDoc, ScoringKnob, SystemWarning } from "../../types";

export const fmtN = (v: number) => String(Math.round(v * 10) / 10);
export const sgn = (v: number) => (v >= 0 ? "+" : "−") + fmtN(Math.abs(v));

export function dirLabel(direction: string): string {
  return direction === "plus" ? "+ only" : direction === "minus" ? "− only" : "±";
}

export function capLabel(signal: Pick<ModelSignal, "direction" | "cap_points">): string {
  const sign = signal.direction === "plus" ? "+" : signal.direction === "minus" ? "−" : "±";
  return `${sign}${fmtN(signal.cap_points)}`;
}

export function signalName(signal: ModelSignal): string {
  return signal.display_name || signal.id.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

// --- value access by knob path (mirrors the server's get_path) --------------------------

export function getPath(doc: ScoringDoc, path: string): unknown {
  if (path.startsWith("display.")) return doc.display[path.slice("display.".length)];
  let node: unknown = doc;
  for (const part of path.split(".")) {
    node = (node as Record<string, unknown>)[part];
  }
  return node;
}

export function setPath(doc: ScoringDoc, path: string, value: unknown): void {
  const parts = path.split(".");
  let node = doc as unknown as Record<string, unknown>;
  for (const part of parts.slice(0, -1)) node = node[part] as Record<string, unknown>;
  node[parts[parts.length - 1]] = value;
}

// --- draft-change counting (the "N staged" figure; the server owns the real diff) --------

export function countChanges(published: ScoringDoc, draft: ScoringDoc, knobPaths: string[]): number {
  let n = 0;
  for (const path of knobPaths) {
    if (JSON.stringify(getPath(published, path)) !== JSON.stringify(getPath(draft, path))) n += 1;
  }
  for (const key of new Set([...Object.keys(published.display), ...Object.keys(draft.display)])) {
    if (published.display[key] !== draft.display[key]) n += 1;
  }
  const pubSignals = new Map(published.signals.map((s) => [s.id, s]));
  const draftSignals = new Map(draft.signals.map((s) => [s.id, s]));
  for (const [id, s] of draftSignals) {
    const o = pubSignals.get(id);
    if (!o) { n += 1; continue; }
    for (const field of ["cap_points", "direction", "enabled_default", "retired", "display_name", "description", "definition", "kind", "job_relatedness", "notes"] as const) {
      if (JSON.stringify(o[field]) !== JSON.stringify(s[field])) n += 1;
    }
  }
  for (const id of pubSignals.keys()) if (!draftSignals.has(id)) n += 1;
  const pubTopics = new Map(published.topics.map((t) => [t.id, t]));
  const draftTopics = new Map(draft.topics.map((t) => [t.id, t]));
  for (const [id, t] of draftTopics) {
    const o = pubTopics.get(id);
    if (!o) { n += 1; continue; }
    if (o.stages.join() !== t.stages.join()) n += 1;
    if (o.phrases.join("") !== t.phrases.join("")) n += 1;
    if (o.floor !== t.floor) n += 1;
    if (o.name !== t.name) n += 1;
    if (o.reason !== t.reason) n += 1;
  }
  for (const id of pubTopics.keys()) if (!draftTopics.has(id)) n += 1;
  return n;
}

// --- the LIVE draft-bar warnings (port of scoring_model.system_warnings) ----------------

export function draftWarnings(
  published: ScoringDoc, draft: ScoringDoc, knobs: ScoringKnob[],
): SystemWarning[] {
  const w: SystemWarning[] = [];
  const add = (level: SystemWarning["level"], text: string, path = "") => w.push({ level, text, path });

  for (const knob of knobs) {
    if (!knob.usual_bounds) continue;
    const a = getPath(published, knob.path);
    const b = getPath(draft, knob.path);
    if (a === b || typeof b !== "number") continue;
    const [lo, hi] = knob.usual_bounds;
    if (b < lo || b > hi) {
      add("warn", `${knob.label}: ${fmtN(b)} is outside the usual range (${fmtN(lo)}–${fmtN(hi)}). You can still propose it — the approver will see this note.`, knob.path);
    }
  }
  const c = draft.coverage.match_value;
  if (c.partial >= c.full) {
    add("warn", `Some-evidence credit (${fmtN(c.partial)}) is not below clear-evidence (${fmtN(c.full)}) — reviewers couldn't tell them apart.`, "coverage.match_value.partial");
  }
  if (c.none > 0) add("warn", `No-evidence lines would still earn ${fmtN(c.none)}× — every score inflates for nothing.`, "coverage.match_value.none");
  if (c.none < 0) add("warn", "No-evidence lines would subtract — absence of evidence becomes a penalty.", "coverage.match_value.none");
  const gap = draft.bands.strong_at - draft.bands.possible_at;
  if (gap < 10) add("warn", `Strong (≥${fmtN(draft.bands.strong_at)}) and Possible (≥${fmtN(draft.bands.possible_at)}) sit only ${fmtN(gap)} points apart — labels will flip on tiny differences.`, "bands.strong_at");
  if (draft.bands.possible_at < 40) add("warn", `“Possible match” would include candidates evidencing under ${fmtN(draft.bands.possible_at)}% of the checklist.`, "bands.possible_at");
  if (draft.clamp.min !== 0 || draft.clamp.max !== 100) add("warn", "Scores outside 0–100 break every chart, export and mental model downstream.", "clamp.min");
  for (const s of draft.signals) {
    if (s.enabled_default && !s.retired && s.cap_points > 6) {
      add("warn", `“${signalName(s)}” could move a score by ${capLabel(s)} — more than most checklist lines. Typical caps are 2–5.`, `signals.${s.id}.cap_points`);
    }
  }
  if (draft.preferences.per_preference_cap_points > 4) {
    add("warn", `Personal preferences at ±${fmtN(draft.preferences.per_preference_cap_points)} could outweigh evidenced signals.`, "preferences.per_preference_cap_points");
  }
  if (draft.preferences.review_months > 12) {
    add("warn", `Flagged preferences would go unreviewed for ${draft.preferences.review_months} months.`, "preferences.review_months");
  }
  if (draft.confidence.high_requires_at_least <= draft.confidence.low_below) {
    add("warn", "The “high” confidence bar sits at or below the “low” bar — every record would read as both.", "confidence.high_requires_at_least");
  }
  const draftTopicIds = new Set(draft.topics.map((t) => t.id));
  for (const t of published.topics) {
    if (!draftTopicIds.has(t.id)) {
      if (t.floor) {
        add("red", `“${t.name}” is a policy-floor protection tied to anti-discrimination law (DPDP · GDPR · Title VII reference set). Removal is allowed — and the approver must individually acknowledge the legal exposure.`, `topics.${t.id}`);
      } else {
        add("warn", `“${t.name}” would no longer be checked by the critic at any stage.`, `topics.${t.id}`);
      }
    } else {
      const d = draft.topics.find((x) => x.id === t.id)!;
      if (t.floor && !d.floor) {
        add("red", `“${t.name}” would lose its policy-floor protection — afterwards it is removable like any other topic.`, `topics.${t.id}.floor`);
      }
    }
  }
  const pubSignalIds = new Set(published.signals.map((s) => s.id));
  for (const s of draft.signals) {
    if (!pubSignalIds.has(s.id)) {
      add("info", `“${signalName(s)}” is new — it starts OFF and applies only to runs after it is switched on.`, `signals.${s.id}`);
    }
  }
  return w;
}

/** Which Score-tab card a warning path belongs to (the draft bar's Edit → link). */
export function sectionOfPath(path: string): { tab: string; card: string } {
  if (path.startsWith("topics.")) return { tab: "critic", card: "criticIntro" };
  if (path.startsWith("signals.") || path === "adjustments.total_cap_points") return { tab: "score", card: "signals" };
  if (path.startsWith("coverage.")) return { tab: "score", card: "credits" };
  if (path.startsWith("preferences.")) return { tab: "score", card: "prefs" };
  if (path.startsWith("confidence.")) return { tab: "score", card: "conf" };
  return { tab: "score", card: "bands" };
}

// --- the synthetic example candidate (the prototype's _sample, signal ids real) ---------

export interface SampleRubricLine {
  label: string;
  kind: "must_have" | "nice_to_have";
  weight: number;
  match: "full" | "partial" | "none";
  evidence: string;
}

export const SAMPLE_TITLE = "Sample: Treasury Accountant, 7 yrs";

export const SAMPLE_RUBRIC: SampleRubricLine[] = [
  { label: "GST & TDS filings", kind: "must_have", weight: 25, match: "full", evidence: "Filed GST and TDS monthly through FY25" },
  { label: "Ind AS close & reporting", kind: "must_have", weight: 20, match: "partial", evidence: "Quarterly close support — not owned end-to-end" },
  { label: "Bank & ledger reconciliations", kind: "must_have", weight: 20, match: "full", evidence: "Owns 14 bank recs at month-end" },
  { label: "SAP FICO", kind: "nice_to_have", weight: 20, match: "partial", evidence: "ECC exposure; S/4 unclear" },
  { label: "Statutory audit support", kind: "nice_to_have", weight: 15, match: "none", evidence: "No evidence found" },
];

export interface SampleAdjustment {
  signalId: string;
  points: number;
  verdict: "pass" | "flag";
  evidence: string;
}

export const SAMPLE_ADJUSTMENTS: SampleAdjustment[] = [
  { signalId: "regulated_environment_exposure", points: 3, verdict: "pass", evidence: "Led statutory audit prep in an RBI-regulated process" },
  { signalId: "tenure_pattern_vs_stability", points: 2, verdict: "pass", evidence: "Two completed 3-year stints" },
  { signalId: "skill_recency", points: 1.5, verdict: "flag", evidence: "Dates partially stated — thin evidence" },
];

export const SAMPLE_SUSPENDED = [
  { signalId: "hands_on_currency", points: -2, reason: "The AI’s reasoning said “between jobs” — current unemployment is never scored. Set aside by code." },
];

export const SAMPLE_PREF = { label: "Notice ≤ 30 days (TA preference)", points: 1 };

export const SAMPLE_EXTRACTION = 0.88;
export const SAMPLE_DOWNGRADED = 1;

// The design's canned AI phrase suggestions per topic ("✨ AI suggest").
export const PHRASE_SUGGESTIONS: Record<string, string[]> = {
  employment_gaps: ["résumé gap", "time off work"],
  unemployment_status: ["not currently employed", "job seeker"],
  overqualification_or_age_proxies: ["young team", "digital native"],
  commute_or_address: ["relocation distance"],
  salary_history: ["pay history", "compensation drawn"],
  school_prestige_as_such: ["top-tier college"],
  culture_fit_vibes: ["team chemistry"],
  similarity_to_current_employees: ["mirror of our team"],
  demographic_language: ["family status"],
};

// The design's per-stage hover explainers ("Enforced at · hover each").
export const STAGE_TIPS: Record<string, string> = {
  scoring: "During a screening run — the critic rejects any AI signal adjustment whose reasoning leans on this topic.",
  checklist: "When a recruiter or hiring manager adds or edits a requirement line — flagged before it can publish.",
  preferences: "When anyone adds a personal screening preference — flagged or blocked at entry.",
  narrative: "In the AI-written candidate summary shown in Review — the wording itself is screened.",
};

export const STAGE_ORDER: ("scoring" | "checklist" | "preferences" | "narrative")[] =
  ["scoring", "checklist", "preferences", "narrative"];

/** "2 years, 3 months" between two ISO timestamps (the Versions tab effect line). */
export function durationText(fromIso: string, toIso: string): string {
  const a = new Date(fromIso).getTime();
  const b = new Date(toIso).getTime();
  let days = Math.max(1, Math.round((b - a) / 86400000));
  const years = Math.floor(days / 365);
  days -= years * 365;
  const months = Math.floor(days / 30.44);
  const rest = Math.round(days - months * 30.44);
  const parts: string[] = [];
  if (years) parts.push(`${years} year${years > 1 ? "s" : ""}`);
  if (months) parts.push(`${months} month${months > 1 ? "s" : ""}`);
  if (rest || !parts.length) parts.push(`${rest} day${rest === 1 ? "" : "s"}`);
  return parts.join(", ");
}

export function formatDay(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}
