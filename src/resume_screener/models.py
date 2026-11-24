"""All Pydantic data contracts (CLAUDE.md §4) — production contracts, KEEP STABLE.

Read this file as the system's vocabulary. Every stage consumes and produces
these shapes; the UI, reports, artifacts and feedback log all serialize them.
Renaming/removing a field is a breaking change to stored artifacts — prefer
adding optional fields.

Pipeline cheat-sheet (which stage makes which model):
  ATS port (P2)      -> Requisition, CandidateRef, DocumentRef
  Rubric agent (P4)  -> Rubric{Requirement}                     (versioned YAML)
  Parse (P5)         -> ResumeProfile                           (frozen artifact)
  Findings (P6)      -> Finding[]                               (frozen artifact)
  Registry/Policy(P4)-> Signal/SignalRegistry, PolicyPack -> EffectivePolicy/EnabledSignal
  Layer 1 (P8)       -> Layer1Result          (pure code, replayable)
  Layer 2 (P9)       -> Layer2Result{Adjustment, ProposedSignal}
  Critic (P10)       -> CriticReview
  Scoring (P11)      -> ScoreResult
  UI feedback (P12)  -> Decision              (append-only JSONL)
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Shared enums. Literal (not Enum) keeps YAML/JSON round-trips plain strings.
MatchLevel = Literal["full", "partial", "none"]
# hard_gate (P33): an objective pass/fail condition the JD states as an explicit
# minimum. Gates carry NO weight — failing one flags the candidate ineligible
# (they stay listed, never hidden). One ordered requirements list, no second track.
RequirementKind = Literal["nice_to_have", "must_have", "hard_gate"]
RequirementType = Literal[
    "skill", "experience", "education", "certification",
    "domain", "language", "achievement", "other",
]
# The three buckets recruiters actually think in, which the checklist is grouped by
# (2026-08-20). DERIVED from `type`, never stored: `type` stays the contract findings
# and every rubric YAML on disk already speak, so nothing needs migrating and an old
# rubric groups correctly the first time it is opened.
RequirementCategory = Literal["skills", "experience", "qualifications", "other"]
CATEGORY_OF_TYPE: dict[str, RequirementCategory] = {
    "skill": "skills",
    "language": "skills",              # a working language IS a skill to a screener
    "experience": "experience",
    "achievement": "experience",       # "exceeded quota" is evidence OF experience
    "domain": "experience",            # sector exposure is experience, not a credential
    "education": "qualifications",
    "certification": "qualifications",  # the brief puts certifications under qualifications
}
# Display order + recruiter-facing labels. "other" is last and the UI only renders it
# when non-empty: an unclassifiable row must never be silently dropped from the
# checklist (guardrail §6.3 — no hiding), but it should not invent a fourth heading
# on a checklist that has none either.
CATEGORY_ORDER: tuple[RequirementCategory, ...] = ("skills", "experience", "qualifications", "other")
CATEGORY_LABELS: dict[RequirementCategory, str] = {
    "skills": "Skills",
    "experience": "Experience",
    "qualifications": "Qualifications",
    "other": "Other",
}


def category_of(requirement_type: str | None) -> RequirementCategory:
    """Which checklist group a requirement belongs to. Unknown/absent -> "other"."""
    return CATEGORY_OF_TYPE.get((requirement_type or "").strip().lower(), "other")
SignalKind = Literal["computed", "judged"]  # computed = code measures; judged = model assesses
SignalDirection = Literal["plus", "minus", "both"]  # which way a signal may move a score
Verdict = Literal["pass", "flag", "suspend"]  # critic outcome per adjustment
FitBand = Literal["Strong", "Possible", "Not a fit"]
Confidence = Literal["low", "medium", "high"]
HumanAction = Literal["agree", "override", "rerank"]
# Where a never-scored topic or a jurisdiction blocked pattern may be enforced.
# Defined here (the shared vocabulary) and re-exported by scoring_model.
Stage = Literal["scoring", "checklist", "preferences", "narrative"]
STAGES: tuple[str, ...] = ("scoring", "checklist", "preferences", "narrative")


def candidate_id_for(file_bytes: bytes) -> str:
    """Stable candidate id = hash of the resume FILE BYTES.

    Consequences to be aware of: the same file always maps to the same id (cache
    hits across runs); a re-exported/edited file is a NEW candidate id. Mapping
    ids back to people happens only via the private map (redact.record_candidate).
    """
    return hashlib.sha256(file_bytes).hexdigest()[:16]


# --- ATS port shapes (P2) ------------------------------------------------------


class WorkLocation(BaseModel):
    """Where the job actually is, and how far from it the recruiter will look.

    NON-SCORING BY CONSTRUCTION, and that is a guardrail, not a preference.
    `commute_or_address` is a floor never-scored topic (CLAUDE.md §6.6): distance from
    home to office correlates with income, and in India with community, so scoring it
    launders a protected attribute into a "practical" number. Removing that floor takes
    a SuperAdmin structural proposal plus a second leader — it is not something a
    requisition field may quietly route around.

    So this is captured as RECRUITER INTENT and shown as CONTEXT beside a candidate:
    the human reads "within 25 km of Gurugram" next to what the résumé states and makes
    the call. It never reaches layer 1, layer 2 or the score, and the redaction pass
    strips address text before any scoring model sees it, so the scoring path is
    structurally incapable of using it even by accident.

    `job_location` on the requisition is a JURISDICTION code (IN, US-NYC) that resolves
    the policy pack. This is different: a real place name a person would recognise.
    """

    place: str = ""                 # "Gurugram", "Mumbai (Andheri East)" — free text
    radius_km: int | None = None    # None = not set; 0 = at this location only
    max_km: int = 100               # the adjustable ceiling of the range control

    def summary(self) -> str:
        """The recruiter-facing line, e.g. `Within 25 km from "Gurugram"`. Rendered
        from data so the number and the place can never drift apart in copy."""
        if not self.place:
            return ""
        if self.radius_km is None:
            return f'Job location: "{self.place}"'
        if self.radius_km == 0:
            return f'Current location: at "{self.place}"'
        return f'Current location: Within {self.radius_km} km from "{self.place}"'


class Requisition(BaseModel):
    req_id: str
    role_title: str
    jd_ref: str  # local ATS: path to the JD file; real ATS: document id
    job_location: str = "IN"  # the POLICY-PACK code (IN, SG, US-NYC, …) — drives policy resolution
    # The country the job is actually in, and the US state when it is one (the
    # global rollout, 2026-09-12). Distinct from job_location, which names a pack:
    # Hungary and Germany are both "EU" there, and that is all the engine needs —
    # but a recruiter in Budapest should read "Hungary", not "European Union", and
    # the requisition-local clock should be Budapest's, not Brussels'. Optional:
    # a requisition created before this (or by the CLI) simply has neither.
    country: str | None = None   # ISO-3166 alpha-2
    us_state: str | None = None  # USPS code, only meaningful when country == "US"
    # The physical workplace + the recruiter's search radius (2026-08-20). Optional:
    # a requisition without one behaves exactly as before.
    work_location: WorkLocation | None = None
    # The iCIMS Req ID — the mapping anchor back to the real requisition (P29;
    # creation REQUIRES it from P32 on). Lifecycle/status/hm stay in the sidecar,
    # never in this contract: closing a req here is "this workspace only".
    icims_req_id: str | None = None


class CandidateRef(BaseModel):
    candidate_id: str  # file-bytes hash (local) / ATS id (production adapters)
    source: str = "local"
    label: str  # pre-redaction reference (file stem); never shown after redaction


class DocumentRef(BaseModel):
    doc_id: str
    candidate_id: str
    file_name: str
    media_type: str  # "pdf" | "docx"
    uri: str  # local ATS: file path; real ATS: download URL


# --- Rubric (P4) ---------------------------------------------------------------


class Requirement(BaseModel):
    id: str  # stable handle findings refer to (r01, r02, …) — assigned in code
    text: str
    type: RequirementType = "other"
    kind: RequirementKind
    weight: float = Field(ge=0)  # normalized so NON-GATE requirements sum to 1; gates stay 0
    # P43: the deterministic checklist critic's verdict on this row's TEXT. "warn"
    # = references a factor that needs HR-Head sign-off before use; None = clean.
    # Blocked text never reaches a saved row (the save route 400s). Additive with a
    # None default so pre-P43 rubric YAML loads unchanged.
    critic_level: Literal["warn"] | None = None
    # The sentence the critic gave when it flagged the row, stored WITH the flag
    # (2026-10-16): a flag the intent screen raised cannot be recomputed from the
    # phrase lists, so recomputing would show "no conflicts" beside a warning.
    critic_reason: str | None = None


class Rubric(BaseModel):
    """ONE ordered requirements list (P33) — a hard gate is simply
    Requirement(kind="hard_gate", weight=0). extra='forbid' makes the retired
    two-list shape (hard_filters:) fail loudly instead of silently dropping gates."""

    model_config = ConfigDict(extra="forbid")

    role_title: str
    jd_ref: str
    version: int = 1  # bumped on every human edit/save; stamped into results + decisions
    requirements: list[Requirement] = []

    def normalized(self) -> "Rubric":
        """Weights scaled so the NON-GATE requirements sum to 1 (gates carry no
        weight — they are pass/fail, not coverage). Always called on load, so
        humans can edit weights as any relative numbers (3 vs 1 = three times
        as important)."""
        weighted = [r for r in self.requirements if r.kind != "hard_gate"]
        total = sum(r.weight for r in weighted)
        if total <= 0:
            # All-zero weights (or no weighted rows) -> uniform, not divide-by-zero.
            n = len(weighted) or 1
            scale = {r.id: round(1 / n, 4) for r in weighted}
        else:
            scale = {r.id: round(r.weight / total, 4) for r in weighted}
        reqs = [r.model_copy(update={"weight": scale.get(r.id, 0.0)}) for r in self.requirements]
        return self.model_copy(update={"requirements": reqs})


# --- Frozen extraction artifacts (P5/P6) ----------------------------------------


class RoleStint(BaseModel):
    title: str
    summary: str = ""
    start: str | None = None  # "YYYY-MM" (or "YYYY"), best effort from the resume
    end: str | None = None  # None = current role ("Present")
    employment_type: str | None = None  # only when STATED; None is treated as permanent
    # (contract/consulting/internship stints never count against tenure — features.py)


class ResumeProfile(BaseModel):
    """The frozen, redacted representation of one candidate — everything
    downstream stages are allowed to know about them."""

    candidate_id: str
    source_file: str  # kept for the artifact/audit trail; NEVER included in prompts
    years_experience_total: float | None = None  # only when the resume states a total
    skills: list[str] = []
    roles: list[RoleStint] = []
    education: list[str] = []
    certifications: list[str] = []
    languages: list[str] = []  # only stated languages; inference is banned (proxy risk)
    redacted_text: str  # the exact text models see; findings evidence must quote it


class ExperienceSpan(BaseModel):
    """One dated employment span a total length was worked out from.

    `quote` is a VERBATIM role/date line, verified exactly like any other evidence —
    the guardrail is unchanged. What the span adds is the pair of dates the model read
    out of that line, so the arithmetic can be done in CODE rather than asserted in a
    sentence (2026-09-07).

    `title` is the job title held in that position (2026-09-11). It is separate from
    `quote` because a résumé's employment line very often does NOT contain the title:
    it reads "<name>, <employer> 06/2024 - Present", and since the name is redacted
    before the model ever sees the text, the journey rendered a row headed
    "[CANDIDATE], McKinsey…" for someone who was a Finance Process Lead. Verified as a
    substring like every other quoted string, and dropped rather than shown unverified —
    a title is a label, not the evidence a gate rests on, so losing it never voids the
    span.

    `counted` / `why_not` (2026-09-25, prompt p38): a requirement like "7 years managing
    AP regional projects" counts only SOME of a candidate's jobs, and the screen used to
    show only those — a 10-year résumé read "2 yr 1 mo" with nothing saying the other
    eight years had been judged off-topic, or why. Every dated position now comes back;
    the ones the requirement is not about carry `counted=False` and the model's one-line
    reason, so a reviewer can see the split and disagree with it. Defaults keep an
    older artifact (every span it holds was a counted one) reading exactly as before."""

    quote: str
    start: str  # "YYYY-MM" or "YYYY"
    end: str    # "YYYY-MM", "YYYY", or "present"
    title: str = ""  # "" when the résumé did not state one we could verify
    counted: bool = True  # False = listed for context; not about THIS requirement
    why_not: str = ""  # the model's reason when counted is False; "" otherwise


class Finding(BaseModel):
    """One requirement × one candidate. The 'evidence or nothing' unit (§6.5):
    match above 'none' is only valid with a quote that verify_findings() confirmed
    is a substring of redacted_text."""

    requirement_id: str
    requirement_text: str  # denormalized copy so artifacts are readable standalone
    match: MatchLevel
    evidence: str | None = None  # VERBATIM quote; code-verified, else downgraded
    note: str | None = None  # model nuance + any downgrade audit trail
    # A length-of-experience requirement is almost never written in a resume as a
    # sentence, and no SINGLE passage can support it: the answer lives across several
    # dated role lines. These are those lines, each verified; `experience_months` is
    # what OUR code makes of them, never what the model claimed.
    experience_spans: list[ExperienceSpan] = Field(default_factory=list)
    experience_months: int | None = None  # the COUNTED spans only — what the requirement asks about
    # Every verified dated span, counted or not, merged the same way. None on an older
    # artifact, which only ever held counted spans, so readers fall back to the months.
    experience_months_total: int | None = None


# --- Signal registry (P4) --------------------------------------------------------


class Signal(BaseModel):
    """A declared, capped, HR/Legal-reviewable scoring adjustment. If it isn't in
    the registry, Layer 2 cannot apply it — that's the governance model."""

    model_config = ConfigDict(extra="forbid")  # typos in YAML fail loudly, not silently

    id: str
    description: str  # plain recruiter language; shown in the UI as written
    display_name: str | None = None  # friendly UI name (falls back to a mapping/id if unset)
    definition: str | None = None  # agent-facing definition text (falls back to description)
    kind: SignalKind
    direction: SignalDirection
    cap_points: float = Field(gt=0)  # default cap; policy packs may tighten/raise per jurisdiction
    enabled_default: bool = True  # default only — the EFFECTIVE policy decides per run
    retired: bool = False  # soft-retire: kept in the file/history, never applied to any score
    job_relatedness: str  # the written justification (the first thing Legal reads)
    notes: str | None = None


class ExcludedSignal(BaseModel):
    """Something this system must NEVER score, with the reason shown to HR.
    The critic checks Layer-2 output against this list."""

    model_config = ConfigDict(extra="forbid")

    id: str
    reason: str


class SignalRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    signals: list[Signal]
    excluded: list[ExcludedSignal]


class EnabledSignal(Signal):
    """A registry signal AFTER the effective policy was applied: final cap +
    jurisdiction params. This is what Layer 2 receives."""

    params: dict[str, Any] = {}


# --- Jurisdiction policy (P4) ----------------------------------------------------


class BlockedPattern(BaseModel):
    """A jurisdiction-layer floor rule: a phrasing that is BLOCKED (or WARN-ed)
    for reqs in this jurisdiction, as GOVERNED DATA rather than code.

    This is the policy floor above the org scoring model: what a govt/legal rule
    (DPDP, California pay-transparency, …) forbids a preference/checklist/score
    from being based on. ``base.yaml`` carries the universal ``floor: true`` set
    (protected attributes — inherited everywhere); overlays ADD region-specific
    ones. Enforcement is deterministic — an LLM may only PROPOSE these; a second
    leader approves them into the pack.

    Matching is by literal ``phrases`` (word-boundary, like a topic) OR raw
    ``regexes`` (for numeric/range rules like an age cutoff or graduation year).
    Both are validated-compilable at load, so a malformed pattern fails loudly.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    reason: str  # plain recruiter language; shown VERBATIM when this fires
    severity: Literal["blocked", "warn"] = "blocked"
    floor: bool = False  # base universal set = floor:true (overlays can never weaken it)
    # Enforcement points. classify_text runs at preferences/checklist; the critic
    # runs at scoring/narrative. Default matches where classify_text runs today.
    stages: list[Stage] = Field(default_factory=lambda: ["preferences", "checklist"])
    phrases: list[str] = Field(default_factory=list)  # literal triggers (case-insensitive)
    regexes: list[str] = Field(default_factory=list)  # raw regex for numeric/range rules
    source: Literal["seed", "derived", "manual"] = "manual"  # provenance (derived = LLM-proposed)
    added_by: str | None = None
    added_at: str | None = None

    @field_validator("id", "name", "reason")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not str(v).strip():
            raise ValueError("must not be empty")
        return str(v).strip()

    @field_validator("stages")
    @classmethod
    def _stages(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate stages")
        return v

    @field_validator("phrases")
    @classmethod
    def _phrases(cls, v: list[str]) -> list[str]:
        cleaned = [str(p).strip() for p in v if str(p).strip()]
        if len({p.lower() for p in cleaned}) != len(cleaned):
            raise ValueError("duplicate trigger phrases")
        return cleaned

    @field_validator("regexes")
    @classmethod
    def _regexes(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in v:
            raw = str(raw)
            if not raw.strip():
                continue
            try:
                re.compile(raw, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"invalid regex {raw!r}: {exc}") from exc
            cleaned.append(raw)
        return cleaned


class SignalOverride(BaseModel):
    """A policy pack's per-signal dial: enable/disable, tighten/raise the cap,
    tune params (e.g. what 'short stint' means in that market)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None  # None = leave the registry default alone
    cap_points: float | None = Field(default=None, ge=0)  # 0 effectively disables
    params: dict[str, Any] | None = None  # deep-merged over base params


class PolicyPack(BaseModel):
    """One YAML file under policies/. base.yaml defines the floor + defaults;
    overlays (IN, EU, …) dial signals and add obligations. extra='forbid' means
    an overlay inventing new mechanisms (e.g. remove_exclusions:) fails
    validation — that's a floor-protection test, not an accident."""

    model_config = ConfigDict(extra="forbid")

    jurisdiction: str
    version: int = 1  # bumped on every published edit (P25); recorded in the audit log
    floor: dict[str, bool] | None = None  # base defines it; overlays may only affirm (never False)
    signal_overrides: dict[str, SignalOverride] = {}
    additional_exclusions: list[ExcludedSignal] = []  # packs can only ADD exclusions
    # Jurisdiction blocked/warn patterns (governed data; base = universal floor,
    # overlays add region-specific). An overlay can never redefine a base FLOOR
    # pattern (policy._merge_policy rejects it) — the floor can only be affirmed.
    blocked_patterns: list[BlockedPattern] = []
    obligations: list[str] = []  # plain-language compliance checklist (displayed, not enforced)
    # Two explicit retention duties (P45); both are NOTES for reviewers — nothing
    # auto-deletes anywhere in this program. keep_at_least_days = the audit-record
    # retention floor; purge_by_days = when candidate PII should be purged after close.
    keep_at_least_days: int | None = None
    purge_by_days: int | None = None
    monitoring_categories: list[str] | None = None  # None = inherit base; lists REPLACE (not union)
    notes: str | None = None


class EffectivePolicy(BaseModel):
    """base ⊕ overlay, resolved once per run and stamped into run_meta.
    Merge rules live in policy.resolve_policy — obligations union, params
    deep-merge, floor immutable."""

    jurisdiction: str  # what the run asked for (e.g. "US-TX")
    pack: str  # the overlay actually applied (e.g. "US-default")
    floor: dict[str, bool]
    signal_overrides: dict[str, SignalOverride]
    additional_exclusions: list[ExcludedSignal]
    blocked_patterns: list[BlockedPattern] = []  # resolved base ⊕ overlay floor patterns
    obligations: list[str]
    keep_at_least_days: int | None  # audit-record retention floor (P45)
    purge_by_days: int | None  # candidate-PII purge duty, days after close (P45)
    monitoring_categories: list[str]
    notes: list[str] = []


# --- Layer results (P8–P11) -------------------------------------------------------


class Adjustment(BaseModel):
    """One bounded Layer-2 score change. |points| ≤ the signal's EFFECTIVE cap and
    Σ|adjustments| ≤ layer2_total_cap_points — both clamped IN CODE (layer2.py);
    the model's numbers are proposals, never trusted arithmetic."""

    signal_id: str
    points: float  # signed
    evidence: str  # verbatim resume quote, substring-verified like findings
    rationale: str


class ProposedSignal(BaseModel):
    """Layer 2 may PROPOSE a new signal it wishes existed. Proposals are logged to
    signals/proposals.jsonl for humans to consider — never applied to any score."""

    name: str
    why: str


class Layer1Result(BaseModel):
    """The deterministic, replayable half of the score (pure code over frozen
    findings). If auditors ask 'why this number', this object IS the answer."""

    candidate_id: str
    rubric_version: int
    gate_flags: list[str] = []  # failed hard-gate requirement ids (candidate stays listed)
    must_haves_missing: list[str] = []  # must-have requirement ids matched 'none'
    coverage_score: float  # 0..1 weighted coverage over the non-gate requirements
    base_score: float  # 100 * coverage
    eligible: bool  # False = failed a hard gate; never means hidden
    reasons: list[str] = []  # human-readable explanations for flags/gate outcomes


class Layer2Result(BaseModel):
    candidate_id: str
    adjustments: list[Adjustment] = []
    proposed_signals: list[ProposedSignal] = []  # logged, never applied
    narrative: str = ""  # 2-3 recruiter-readable sentences of context


class CriticReview(BaseModel):
    """Bias critic's verdicts. 'suspend' removes an adjustment from the score but
    keeps it visible in the record + UI queue — reviewable, never hidden."""

    verdict_per_adjustment: dict[str, Verdict] = {}  # signal_id -> pass|flag|suspend
    notes: list[str] = []
    overall_flags: list[str] = []


class PreferenceAdjustment(BaseModel):
    """One applied HR-preference nudge (P40). Bounded to ±2 IN CODE; evidence is a
    verbatim resume quote, substring-verified like Layer-2 adjustments (dropped if
    unverifiable). A preference NEVER touches eligibility, gates or the must-have
    band cap — it only nudges the final score, then the band recomputes from it."""

    pref_id: str
    owner: str  # the preference owner's email (a requisition contact)
    text: str  # the plain-language preference, shown to reviewers verbatim
    points: float  # signed, |points| ≤ 2.0
    evidence: str | None  # verbatim resume quote, or None
    rationale: str


class ScoreResult(BaseModel):
    """Everything the UI shows for one candidate on one run."""

    candidate_id: str
    role_title: str
    jurisdiction: str
    layer1: Layer1Result
    layer2: Layer2Result | None = None  # None = gate-failed (Layer 2 never ran) or L2 disabled
    critic: CriticReview | None = None
    final_score: float  # clamp(base ± surviving adjustments ± preference nudges, 0, 100)
    fit_band: FitBand
    confidence: Confidence  # from extraction quality + evidence completeness
    summary_rationale: str
    # P40 (additive): the applied HR-preference nudges. Empty for every run with no
    # applicable preferences → a no-prefs ScoreResult is model_dump-identical to pre-P40.
    preference_adjustments: list[PreferenceAdjustment] = []


# --- Human decisions (P12) ---------------------------------------------------------


class Decision(BaseModel):
    """One human decision, appended to output/feedback/decisions.jsonl. The
    versions dict makes every decision reconstructible: which model, prompts,
    rubric, registry and policy produced the suggestion the human saw. This log
    is the raw material for the (offline, human-gated) learning loop — §12."""

    candidate_id: str
    run_id: str
    # The requisition this decision belongs to (2026-07-21). Decisions are global
    # in the log but a decision is MADE on one req; carry-forward across a
    # re-screen must stay within that req (a shared résumé — same candidate_id —
    # in another req must NOT inherit this decision). Optional/"" for pre-2026-07-21
    # rows, which simply never carry forward.
    req_id: str = ""
    suggestion: str  # what the tool suggested (band + score)
    human_action: HumanAction
    final_band: FitBand | None = None
    reason: str  # required in the UI — "agree" without a reason is not captured
    reviewer: str | None = None
    versions: dict[str, str] = {}  # model, prompts, rubric, registry, policy
    ts: str  # ISO-8601
