"""The versioned scoring model — every number AND rule that shapes a score.

P51 moved the numeric knobs (bands, caps, credits, confidence) out of code into
``signals/scoring_model.v{N}.yaml``. The design-led rebuild (2026-07-18) merges
the rest of the scoring configuration into the SAME governed artifact:

  * ``signals:`` — the AI-signal definitions (the former signals/registry.yaml;
    the §4 ``Signal`` contract, unchanged — registry.load_registry now reads
    them from here, so Layer 2 / the critic / policy overlays are untouched);
  * ``topics:`` — the never-scored topics (the former ``excluded:`` list), each
    now carrying its plain-language reason, a ``floor`` marker (law-adjacent —
    removal requires an explicit exposure acknowledgment), the enforcement
    ``stages`` (scoring / checklist / preferences / narrative) and the literal
    ``phrases`` the deterministic code screen matches;
  * ``clamp:`` — the final score's floor/ceiling (was hard-coded 0–100).

One immutable file per published version (the rubric ``.vN`` pattern), highest
version = current. Publishing is governance.py's job (change proposal + a
second leader's approval); this module only knows how to validate, load, stamp,
diff and warn about models.

Guardrail note: the schema enforces only what keeps a model mathematically
coherent (Possible < Strong, credit ORDER, positive caps, floor < ceiling).
Everything else is a policy choice — out-of-the-usual values are WARNED about
(``system_warnings``), never blocked. That is deliberate: the gate is the
approval flow, not a hard-coded wall.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from resume_screener import reqcache
from resume_screener.cache import content_hash
from resume_screener.models import STAGES, ExcludedSignal, Signal, Stage  # noqa: F401
from resume_screener.pathkey import canon

# NOTE: Stage/STAGES are the shared enforcement-stage vocabulary; they now live
# in models.py and are re-exported here so `from scoring_model import Stage`
# (prefcheck, critic, tests) keeps working.

SCORING_MODEL_DIR = Path("signals")
_FILE_RE = re.compile(r"^scoring_model\.v(\d+)\.yaml$")


class ScoringModelError(RuntimeError):
    """Raised with a plain, human-readable message (shown to admins verbatim)."""


class CoverageRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_value: dict[str, float]

    @field_validator("match_value")
    @classmethod
    def _match_keys_and_order(cls, v: dict[str, float]) -> dict[str, float]:
        if set(v) != {"full", "partial", "none"}:
            raise ValueError("match credit needs exactly the levels full, partial and none")
        # ORDER is the coherence invariant; the usual 0..1 range is a warning,
        # not a wall (design mandate: everything editable via approval).
        if not v["none"] <= v["partial"] <= v["full"]:
            raise ValueError("match credit must satisfy none ≤ partial ≤ full")
        if not -1 <= v["none"] and v["full"] <= 2:
            raise ValueError("match credit must stay within −1 … 2")
        return v


class BandRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strong_at: float
    possible_at: float
    cap_if_must_have_missing: Literal["Not a fit", "Possible", "Strong"]

    @model_validator(mode="after")
    def _ordered(self) -> "BandRules":
        if not 0 <= self.possible_at < self.strong_at:
            raise ValueError("band cutoffs must satisfy 0 ≤ Possible < Strong")
        return self


class ClampRules(BaseModel):
    """The fence: the final number never leaves [min, max]."""

    model_config = ConfigDict(extra="forbid")
    min: float = 0.0
    max: float = 100.0

    @model_validator(mode="after")
    def _ordered(self) -> "ClampRules":
        if self.min >= self.max:
            raise ValueError("the score floor must sit below the score ceiling")
        return self


class AdjustmentRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_cap_points: float = Field(gt=0)


class PreferenceRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per_preference_cap_points: float = Field(ge=0)
    review_months: int = Field(ge=1)


class BatchQualityRules(BaseModel):
    """The batch-level (per-requisition) quality read — NOT a candidate score.

    Judged on the HEAD of the batch: the average final score of the top
    ``top_n`` gate-passing candidates. A plain average would punish a big
    honest batch for its long tail and reward a tiny mediocre one; what a
    recruiter actually asks is "can I fill this role from what came in?" —
    and that is decided by the best few résumés, not the median."""

    model_config = ConfigDict(extra="forbid")
    top_n: int = Field(5, ge=1)
    strong_at: float = 75.0
    okay_at: float = 55.0

    @model_validator(mode="after")
    def _ordered(self) -> "BatchQualityRules":
        if not 0 <= self.okay_at < self.strong_at:
            raise ValueError("batch bands must satisfy 0 ≤ Okay < Strong")
        return self


class ConfidenceRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unmeasured_extraction_value: float = Field(ge=0, le=1)
    high_requires_at_least: float = Field(ge=0, le=1)
    low_below: float = Field(ge=0, le=1)
    low_when_downgraded_findings_over: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "ConfidenceRules":
        if self.low_below > self.high_requires_at_least:
            raise ValueError('the "low" read-quality bar cannot sit above the "high" bar')
        return self


class TopicRule(BaseModel):
    """One never-scored topic: WHAT is banned (name + reason), WHERE it is
    enforced (stages) and the literal trigger phrases the deterministic code
    screen matches (the reviewing AI model catches paraphrases on top)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    reason: str
    floor: bool = False  # law-adjacent: removal needs an explicit acknowledgment
    added_by: str | None = None
    added_at: str | None = None
    stages: list[Stage] = Field(default_factory=lambda: ["scoring"])
    phrases: list[str] = Field(default_factory=list)

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


class ScoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: int = Field(1, alias="schema")
    version: int = Field(ge=0)
    coverage: CoverageRules
    bands: BandRules
    clamp: ClampRules = Field(default_factory=ClampRules)
    adjustments: AdjustmentRules
    preferences: PreferenceRules
    confidence: ConfidenceRules
    # Batch-level quality bands (2026-07-19). Optional in older version files —
    # a file without the section reads as these seeded defaults, so v1 stamps
    # (and their hashes) stay untouched and nothing drifts stale.
    batch_quality: BatchQualityRules = Field(default_factory=BatchQualityRules)
    signals: list[Signal]
    topics: list[TopicRule]
    display: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_ids(self) -> "ScoringModel":
        seen: set[str] = set()
        for s in self.signals:
            if s.id in seen:
                raise ValueError(f"duplicate signal id '{s.id}'")
            seen.add(s.id)
        for t in self.topics:
            if t.id in seen:
                raise ValueError(
                    f"'{t.id}' appears as both a signal and a never-scored topic — "
                    "it must be one or the other")
            seen.add(t.id)
        return self

    def signal_by_id(self, signal_id: str) -> Signal | None:
        return next((s for s in self.signals if s.id == signal_id), None)

    def topic_by_id(self, topic_id: str) -> TopicRule | None:
        return next((t for t in self.topics if t.id == topic_id), None)

    def floor_topic_ids(self) -> set[str]:
        return {t.id for t in self.topics if t.floor}

    def topics_for_stage(self, stage: str) -> list[TopicRule]:
        return [t for t in self.topics if stage in t.stages]


# --- the built-in fallback for bare working dirs (tests chdir into empty tmp dirs) ----
#
# The default IS the committed seed (parsed once from the repo, version forced to
# 0 so stamps read "v0 (default)") — the anti-rot mechanism that keeps a Python
# default from ever drifting against the YAML the way config.py's 75/50/15 once
# rotted against config.yaml's 88/60/8. The tiny hand-written fallback below only
# exists for exotic installs where the repo seed is unreachable.

_REPO_SEED = Path(__file__).resolve().parents[2] / "signals" / "scoring_model.v1.yaml"
_DEFAULT_CACHE: list[ScoringModel] = []


def _fallback_model() -> ScoringModel:
    return ScoringModel(
        version=0,
        coverage=CoverageRules(match_value={"full": 1.0, "partial": 0.5, "none": 0.0}),
        bands=BandRules(strong_at=88, possible_at=60, cap_if_must_have_missing="Possible"),
        clamp=ClampRules(min=0, max=100),
        adjustments=AdjustmentRules(total_cap_points=8),
        preferences=PreferenceRules(per_preference_cap_points=2, review_months=6),
        confidence=ConfidenceRules(
            unmeasured_extraction_value=0.85,
            high_requires_at_least=0.9,
            low_below=0.75,
            low_when_downgraded_findings_over=2,
        ),
        signals=[],
        topics=[],
    )


def default_scoring_model() -> ScoringModel:
    """The committed seed with version 0 — what a root with no published model
    file scores under. Deep copy (callers may mutate drafts)."""
    if not _DEFAULT_CACHE:
        model: ScoringModel | None = None
        if _REPO_SEED.exists():
            try:
                model = _parse_file(_REPO_SEED)
                model.version = 0
            except ScoringModelError:
                model = None
        _DEFAULT_CACHE.append(model or _fallback_model())
    return _DEFAULT_CACHE[0].model_copy(deep=True)


# --- per-knob metadata (single source for the UI, diffs and propose-time warnings) --
#
# usual_bounds is [lo, hi] for the WARN band (values outside are allowed but
# flagged on the proposal); None = no numeric band. group anchors the knob to a
# card on the Scoring model page.
KNOB_META: dict[str, dict[str, Any]] = {
    "coverage.match_value.full": {
        "label": "Clear evidence",
        "help": "The résumé states it outright — pays the line's full weight.",
        "technical_term": "Coverage credit — base score = 100 × Σ(importance × credit).",
        "usual_bounds": [0.8, 1.0], "group": "credits",
    },
    "coverage.match_value.partial": {
        "label": "Some evidence",
        "help": "Adjacent or partially stated — a near-miss stays visible instead of vanishing.",
        "technical_term": "Coverage credit for match level “partial”.",
        "usual_bounds": [0.25, 0.75], "group": "credits",
    },
    "coverage.match_value.none": {
        "label": "No evidence",
        "help": "Nothing found. Absence is never punished — it just earns nothing.",
        "technical_term": "Coverage credit for match level “none”.",
        "usual_bounds": [0.0, 0.0], "group": "credits",
    },
    "bands.strong_at": {
        "label": "“Strong match” starts at",
        "help": "Worth a screening call on the evidence alone.",
        "technical_term": "Band threshold on the clamped final score.",
        "usual_bounds": [75, 95], "group": "bands",
    },
    "bands.possible_at": {
        "label": "“Possible match” starts at",
        "help": "Worth a look; below this reads “Weak match”.",
        "technical_term": "Band threshold on the clamped final score.",
        "usual_bounds": [40, 75], "group": "bands",
    },
    "bands.cap_if_must_have_missing": {
        "label": "Missing a must-have caps the label at",
        "help": "A great nice-to-have story can't paper over a missing core requirement.",
        "technical_term": "Band cap applied after scoring; the score itself is unchanged.",
        "usual_bounds": None, "group": "bands",
    },
    "clamp.min": {
        "label": "Score floor",
        "help": "The final number never drops below this.",
        "technical_term": "Lower clamp bound on base + signals + preferences.",
        "usual_bounds": [0, 0], "group": "bands",
    },
    "clamp.max": {
        "label": "Score ceiling",
        "help": "…and never rises above this.",
        "technical_term": "Upper clamp bound on base + signals + preferences.",
        "usual_bounds": [100, 100], "group": "bands",
    },
    "adjustments.total_cap_points": {
        "label": "All AI signals together, at most",
        "help": "Every evidence-backed signal combined can never move a score by more than this many points, up or down.",
        "technical_term": "Total cap — the combined adjustment is scaled back (“clamped”) to this limit.",
        "usual_bounds": [4, 15], "group": "signals",
    },
    "preferences.per_preference_cap_points": {
        "label": "Cap per preference",
        "help": "The most one personal preference can nudge any score.",
        "technical_term": "Per-preference cap; nudges share the final clamp.",
        "usual_bounds": [0, 4], "group": "preferences",
    },
    "preferences.review_months": {
        "label": "Approval re-review window",
        "help": "Flagged preferences expire and return to the HR Head queue. Applies to approvals recorded after the change.",
        "technical_term": "review_due = approval date + this many months.",
        "usual_bounds": [3, 12], "group": "preferences",
    },
    "batch_quality.top_n": {
        "label": "Judged on the top",
        "help": "How many of the batch's best gate-passing candidates the batch read averages. Small on purpose — a role is filled from the head of the batch, not its tail.",
        "technical_term": "Batch score = mean(final score of the top N gate-passing candidates); N shrinks to the batch when fewer pass.",
        "usual_bounds": [3, 10], "group": "batch",
    },
    "batch_quality.strong_at": {
        "label": "“Strong batch” starts at",
        "help": "The top candidates average this or better — you can likely fill the role from this batch.",
        "technical_term": "Threshold on the top-N mean final score. Batch-level only — no candidate's own score or band is affected.",
        "usual_bounds": [65, 90], "group": "batch",
    },
    "batch_quality.okay_at": {
        "label": "“Okay batch” starts at",
        "help": "Workable but thin — below this the batch reads “Weak” and sourcing more résumés is usually faster than re-reading these.",
        "technical_term": "Threshold on the top-N mean final score; below it the batch label is “Weak batch”.",
        "usual_bounds": [45, 70], "group": "batch",
    },
    "confidence.unmeasured_extraction_value": {
        "label": "Assumed read quality for digital résumés",
        "help": "Digital documents have no scan-quality measurement; this stands in for it.",
        "technical_term": "Substitute for the extractor's read-quality measure when None (OCR word confidence for scans, text-layer quality for files read locally).",
        "usual_bounds": [0.7, 0.95], "group": "confidence",
    },
    "confidence.high_requires_at_least": {
        "label": "“High” needs extraction above",
        "help": "Clean digital text reads ≈0.95; photographed pages read lower.",
        "technical_term": "Threshold on extraction confidence (0–1).",
        "usual_bounds": [0.85, 0.98], "group": "confidence",
    },
    "confidence.low_below": {
        "label": "“Low” below",
        "help": "A blurry scan means the text itself is uncertain — double-check the résumé.",
        "technical_term": "Threshold on extraction confidence (0–1).",
        "usual_bounds": [0.6, 0.85], "group": "confidence",
    },
    "confidence.low_when_downgraded_findings_over": {
        "label": "Downgraded claims before “Low”",
        "help": "Claims the evidence-checker walked back. More than this and the whole record earns skepticism.",
        "technical_term": "Count of findings downgraded by the verbatim-evidence check.",
        "usual_bounds": [1, 5], "group": "confidence",
    },
}

_DISPLAY_META = {
    "label": "Explainer text",
    "help": "Plain-language template shown in the app; {placeholders} are filled from the values above.",
    "technical_term": None, "usual_bounds": None, "group": "display",
}


def knob_meta(path: str) -> dict[str, Any]:
    """Metadata for a knob path; display.* keys share one template entry."""
    if path.startswith("display."):
        key = path.split(".", 1)[1]
        return {**_DISPLAY_META, "label": f"Explainer text: {key.replace('_', ' ')}"}
    return KNOB_META[path]


# --- value access by dotted path ----------------------------------------------------


def get_path(sm: ScoringModel, path: str) -> Any:
    if path.startswith("display."):
        return sm.display.get(path.split(".", 1)[1])
    node: Any = sm
    for part in path.split("."):
        node = node[part] if isinstance(node, dict) else getattr(node, part)
    return node


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _dir_label(direction: str) -> str:
    return {"plus": "+ only", "minus": "− only", "both": "±"}.get(direction, direction)


def _cap_label(signal: Signal | dict) -> str:
    direction = signal["direction"] if isinstance(signal, dict) else signal.direction
    cap = signal["cap_points"] if isinstance(signal, dict) else signal.cap_points
    sign = {"plus": "+", "minus": "−", "both": "±"}.get(str(direction), "±")
    return f"{sign}{cap:g}"


def _short(text: Any, limit: int = 48) -> str:
    s = str(text or "—").strip() or "—"
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


_SIGNAL_FIELD_LABELS = {
    "display_name": "name", "description": "description", "definition": "definition",
    "kind": "type", "job_relatedness": "job-relatedness rationale", "notes": "notes",
}


def _diff_signals(old: list[Signal], new: list[Signal]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    old_by = {s.id: s for s in old}
    new_by = {s.id: s for s in new}
    for s in new:
        o = old_by.get(s.id)
        name = s.display_name or s.id.replace("_", " ").capitalize()
        if o is None:
            rows.append({"path": f"signals.{s.id}", "label": f"New signal “{name}”",
                         "from": "—", "to": f"{_cap_label(s)} · off until switched on"})
            continue
        if o.cap_points != s.cap_points:
            rows.append({"path": f"signals.{s.id}.cap_points", "label": f"“{name}” cap",
                         "from": _cap_label(o), "to": _cap_label(s)})
        if o.direction != s.direction:
            rows.append({"path": f"signals.{s.id}.direction", "label": f"“{name}” direction",
                         "from": _dir_label(o.direction), "to": _dir_label(s.direction)})
        if o.enabled_default != s.enabled_default:
            rows.append({"path": f"signals.{s.id}.enabled_default", "label": f"“{name}”",
                         "from": "on" if o.enabled_default else "off",
                         "to": "on" if s.enabled_default else "off (retired from future runs)"})
        if o.retired != s.retired:
            rows.append({"path": f"signals.{s.id}.retired", "label": f"“{name}”",
                         "from": "retired" if o.retired else "in the registry",
                         "to": "retired" if s.retired else "reinstated"})
        for field, label in _SIGNAL_FIELD_LABELS.items():
            a, b = getattr(o, field), getattr(s, field)
            if a != b:
                rows.append({"path": f"signals.{s.id}.{field}", "label": f"“{name}” {label}",
                             "from": _short(a), "to": _short(b)})
    for s in old:
        if s.id not in new_by:
            name = s.display_name or s.id.replace("_", " ").capitalize()
            rows.append({"path": f"signals.{s.id}", "label": f"Signal “{name}” deleted",
                         "from": "in the registry", "to": "removed"})
    return rows


def _diff_topics(old: list[TopicRule], new: list[TopicRule]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    old_by = {t.id: t for t in old}
    new_by = {t.id: t for t in new}
    for t in new:
        o = old_by.get(t.id)
        if o is None:
            rows.append({"path": f"topics.{t.id}", "label": f"New never-scored topic “{t.name}”",
                         "from": "—",
                         "to": "banned at " + (" · ".join(s.capitalize() for s in t.stages) or "—")})
            continue
        if o.stages != t.stages:
            rows.append({"path": f"topics.{t.id}.stages", "label": f"“{t.name}” enforcement",
                         "from": " · ".join(s.capitalize() for s in o.stages) or "nowhere",
                         "to": " · ".join(s.capitalize() for s in t.stages) or "nowhere"})
        if o.phrases != t.phrases:
            rows.append({"path": f"topics.{t.id}.phrases", "label": f"“{t.name}” trigger phrases",
                         "from": f"{len(o.phrases)} phrases", "to": f"{len(t.phrases)} phrases"})
        if o.floor != t.floor:
            rows.append({"path": f"topics.{t.id}.floor", "label": f"“{t.name}” policy-floor protection",
                         "from": "on" if o.floor else "off", "to": "on" if t.floor else "off"})
        if o.name != t.name or o.reason != t.reason:
            if o.name != t.name:
                rows.append({"path": f"topics.{t.id}.name", "label": f"“{o.name}” renamed",
                             "from": _short(o.name), "to": _short(t.name)})
            if o.reason != t.reason:
                rows.append({"path": f"topics.{t.id}.reason", "label": f"“{t.name}” reason",
                             "from": _short(o.reason), "to": _short(t.reason)})
    for t in old:
        if t.id not in new_by:
            rows.append({"path": f"topics.{t.id}", "label": f"Never-scored topic “{t.name}” removed",
                         "from": "protected (policy floor)" if t.floor else "protected",
                         "to": "no longer checked"})
    return rows


def diff_scoring_models(old: ScoringModel, new: ScoringModel) -> list[dict[str, str]]:
    """Field-level diff in plain language: [{path, label, from, to}]. The single
    wording source for publish summaries, drift itemization and the UI dialogs.
    Covers the knobs, the display templates, the signals and the topics."""
    rows: list[dict[str, str]] = []
    paths = list(KNOB_META) + sorted(
        {f"display.{k}" for k in {**old.display, **new.display}}
    )
    for path in paths:
        a, b = get_path(old, path), get_path(new, path)
        if a != b:
            rows.append({
                "path": path,
                "label": knob_meta(path)["label"],
                "from": _format_value(a),
                "to": _format_value(b),
            })
    rows += _diff_signals(old.signals, new.signals)
    rows += _diff_topics(old.topics, new.topics)
    return rows


# --- propose-time warnings (ported verbatim from the v3 design prototype) -------------
#
# Levels: "warn" (amber), "red" (exposure — the approver must acknowledge),
# "info" (plain note). NEVER blocking: the schema handles incoherence; these
# hand the approver the judgment calls.


def system_warnings(old: ScoringModel, new: ScoringModel) -> list[dict[str, str]]:
    w: list[dict[str, str]] = []

    def add(level: str, text: str, path: str = "") -> None:
        w.append({"level": level, "text": text, "path": path})

    # Edited knobs leaving their usual range — allowed, noted for the approver.
    for path, meta in KNOB_META.items():
        bounds = meta.get("usual_bounds")
        if not bounds:
            continue
        a, b = get_path(old, path), get_path(new, path)
        if a == b or not isinstance(b, (int, float)):
            continue
        lo, hi = bounds
        if not (lo <= float(b) <= hi):
            add("warn",
                f"{meta['label']}: {b:g} is outside the usual range ({lo:g}–{hi:g}). "
                "You can still propose it — the approver will see this note.", path)

    c = new.coverage.match_value
    if c["partial"] >= c["full"]:
        add("warn", f"Some-evidence credit ({c['partial']:g}) is not below clear-evidence "
                    f"({c['full']:g}) — reviewers couldn't tell them apart.",
            "coverage.match_value.partial")
    if c["none"] > 0:
        add("warn", f"No-evidence lines would still earn {c['none']:g}× — every score "
                    "inflates for nothing.", "coverage.match_value.none")
    if c["none"] < 0:
        add("warn", "No-evidence lines would subtract — absence of evidence becomes a "
                    "penalty.", "coverage.match_value.none")
    gap = new.bands.strong_at - new.bands.possible_at
    if gap < 10:
        add("warn", f"Strong (≥{new.bands.strong_at:g}) and Possible (≥{new.bands.possible_at:g}) "
                    f"sit only {gap:g} points apart — labels will flip on tiny differences.",
            "bands.strong_at")
    if new.bands.possible_at < 40:
        add("warn", f"“Possible match” would include candidates evidencing under "
                    f"{new.bands.possible_at:g}% of the checklist.", "bands.possible_at")
    if new.clamp.min != 0 or new.clamp.max != 100:
        add("warn", "Scores outside 0–100 break every chart, export and mental model "
                    "downstream.", "clamp.min")
    for s in new.signals:
        if s.enabled_default and not s.retired and s.cap_points > 6:
            name = s.display_name or s.id.replace("_", " ").capitalize()
            add("warn", f"“{name}” could move a score by {_cap_label(s)} — more than most "
                        "checklist lines. Typical caps are 2–5.", f"signals.{s.id}.cap_points")
    if new.preferences.per_preference_cap_points > 4:
        add("warn", f"Personal preferences at ±{new.preferences.per_preference_cap_points:g} "
                    "could outweigh evidenced signals.", "preferences.per_preference_cap_points")
    if new.preferences.review_months > 12:
        add("warn", f"Flagged preferences would go unreviewed for "
                    f"{new.preferences.review_months} months.", "preferences.review_months")
    if new.confidence.high_requires_at_least <= new.confidence.low_below:
        add("warn", "The “high” confidence bar sits at or below the “low” bar — every "
                    "record would read as both.", "confidence.high_requires_at_least")
    bq_gap = new.batch_quality.strong_at - new.batch_quality.okay_at
    if bq_gap < 8:
        add("warn", f"“Strong batch” (≥{new.batch_quality.strong_at:g}) and “Okay batch” "
                    f"(≥{new.batch_quality.okay_at:g}) sit only {bq_gap:g} points apart — "
                    "batch labels will flip on tiny score moves.", "batch_quality.strong_at")
    if new.batch_quality.top_n > 15:
        add("warn", f"Averaging the top {new.batch_quality.top_n} pulls the batch read toward "
                    "the tail — the label stops answering “can I fill this role?”.",
            "batch_quality.top_n")

    old_topics = {t.id: t for t in old.topics}
    new_topics = {t.id: t for t in new.topics}
    for tid, t in old_topics.items():
        if tid not in new_topics:
            if t.floor:
                add("red", f"“{t.name}” is a policy-floor protection tied to "
                           "anti-discrimination law (DPDP · GDPR · Title VII reference set). "
                           "Removal is allowed — and the approver must individually "
                           "acknowledge the legal exposure.", f"topics.{tid}")
            else:
                add("warn", f"“{t.name}” would no longer be checked by the critic at any "
                            "stage.", f"topics.{tid}")
        elif t.floor and not new_topics[tid].floor:
            add("red", f"“{t.name}” would lose its policy-floor protection — afterwards it "
                       "is removable like any other topic.", f"topics.{tid}.floor")
    old_signals = {s.id for s in old.signals}
    for s in new.signals:
        if s.id not in old_signals:
            name = s.display_name or s.id.replace("_", " ").capitalize()
            add("info", f"“{name}” is new — it starts OFF and applies only to runs after "
                        "it is switched on.", f"signals.{s.id}")
    return w


def resolved_display(sm: ScoringModel) -> dict[str, str]:
    """The display templates with {placeholders} filled from current values."""
    values = {
        "total_cap_points": _format_value(sm.adjustments.total_cap_points),
        "per_preference_cap_points": _format_value(sm.preferences.per_preference_cap_points),
        "review_months": str(sm.preferences.review_months),
        "cap_if_must_have_missing": sm.bands.cap_if_must_have_missing,
        "strong_at": _format_value(sm.bands.strong_at),
        "possible_at": _format_value(sm.bands.possible_at),
        "clamp_min": _format_value(sm.clamp.min),
        "clamp_max": _format_value(sm.clamp.max),
    }

    class _Safe(dict):
        def __missing__(self, key: str) -> str:  # unknown placeholder -> left as-is
            return "{" + key + "}"

    return {k: v.format_map(_Safe(values)) for k, v in sm.display.items()}


# --- trigger-phrase matching (the deterministic code screen's engine) ------------------
#
# Phrases are LITERAL text matches — precision-first, never meaning-based
# ("gap analysis" is normal finance vocabulary and must never trigger). Word
# boundaries wrap a phrase's alphanumeric edges; inner whitespace matches any
# whitespace run. The reviewing AI model catches paraphrases these can't.

_PATTERN_CACHE: dict[tuple[str, ...], re.Pattern[str] | None] = {}


def compile_phrases(phrases: list[str]) -> re.Pattern[str] | None:
    key = tuple(p.lower() for p in phrases)
    if key in _PATTERN_CACHE:
        return _PATTERN_CACHE[key]
    parts: list[str] = []
    for phrase in phrases:
        p = re.escape(phrase.strip())
        p = re.sub(r"(?:\\\s)+", r"\\s+", p)  # escaped whitespace -> any whitespace run
        if phrase and phrase[0].isalnum():
            p = r"\b" + p
        if phrase and phrase[-1].isalnum():
            p = p + r"\b"
        if p:
            parts.append(p)
    pattern = re.compile("|".join(parts), re.IGNORECASE) if parts else None
    _PATTERN_CACHE[key] = pattern
    return pattern


def match_topics(text: str, topics: list[TopicRule], stage: str) -> list[TopicRule]:
    """The topics whose phrases fire on `text` at `stage` (deterministic,
    offline). Phrase-less topics never fire here — they are enforced by the
    reviewing model, which sees every topic's name + reason."""
    hits: list[TopicRule] = []
    for topic in topics:
        if stage not in topic.stages:
            continue
        pattern = compile_phrases(topic.phrases)
        if pattern is not None and pattern.search(text or ""):
            hits.append(topic)
    return hits


def merge_extra_exclusions(topics: list[TopicRule],
                           extras: list[ExcludedSignal]) -> list[TopicRule]:
    """Policy packs may only ADD exclusions ({id, reason}); they arrive as
    phrase-less scoring-stage topics so the critic model enforces them."""
    known = {t.id for t in topics}
    merged = list(topics)
    for e in extras or []:
        if e.id not in known:
            merged.append(TopicRule(id=e.id, name=e.id.replace("_", " ").capitalize(),
                                    reason=e.reason, floor=False,
                                    stages=["scoring"], phrases=[]))
            known.add(e.id)
    return merged


# --- canonical serialization (governance writes new version files from this) -----------

_SECTION_ORDER = ("schema", "version", "bands", "clamp", "coverage", "adjustments",
                  "preferences", "confidence", "batch_quality", "signals", "topics",
                  "display")


def canonical_dump(sm: ScoringModel) -> dict:
    """A stable, section-ordered plain dict for YAML serialization."""
    data = sm.model_dump(by_alias=True)
    ordered: dict = {}
    for key in _SECTION_ORDER:
        if key in data:
            ordered[key] = data[key]
    for key in data:  # anything the order list misses still lands
        if key not in ordered:
            ordered[key] = data[key]
    return ordered


# --- version files -------------------------------------------------------------------


# dir -> (dir mtime_ns, sorted (version, path) pairs). A new version is a new file,
# and only a new file moves the directory mtime — so one stat answers "anything
# published since?" instead of a listing per call (the dashboard asked ~45 times).
_VERSIONS_CACHE: dict[str, tuple[int, list[tuple[int, Path]]]] = {}


def invalidate_versions(dir: str | Path = SCORING_MODEL_DIR) -> None:
    """Forget what we know about which versions exist, and the parsed models.

    WHY THIS EXISTS (2026-09-07). The caches below are validated by the directory's
    mtime, which is the right shape — a new version is a new FILE, and only a new
    file moves a directory — but Windows does not update a directory's timestamp
    reliably or promptly, so a publish followed immediately by a read could still be
    served the previous version. That is a correctness bug in the governance flow
    (publish, second leader approves, everyone must now see the new model), and it
    showed up as a couple of unrelated governance tests failing per suite run, never
    the same two. Every writer of a version file calls this, the way save_rubric
    already clears the request memo.
    """
    _VERSIONS_CACHE.pop(canon(dir), None)
    _MODEL_CACHE.clear()
    reqcache.clear()


def scoring_model_versions(dir: str | Path = SCORING_MODEL_DIR) -> list[tuple[int, Path]]:
    """(version, path) pairs sorted ascending; missing dir -> empty."""
    d = Path(dir)
    try:
        dir_mtime = os.stat(d).st_mtime_ns
    except OSError:
        return []
    # The key is the RESOLVED path (2026-09-07). It used to be str(dir) — the
    # relative "signals" — so every working directory shared one cache entry and
    # the only thing separating them was a directory mtime that two directories
    # created in the same filesystem tick genuinely share. The suite showed it as
    # a couple of unrelated governance tests failing per run, never the same two.
    key = canon(d)
    hit = _VERSIONS_CACHE.get(key)
    if hit is not None and hit[0] == dir_mtime:
        return list(hit[1])
    found: list[tuple[int, Path]] = []
    try:
        with os.scandir(d) as it:
            for e in it:
                m = _FILE_RE.match(e.name)
                if m and e.is_file():
                    found.append((int(m.group(1)), d / e.name))
    except NotADirectoryError:
        return []
    found.sort()
    _VERSIONS_CACHE[key] = (dir_mtime, found)
    return list(found)


def latest_scoring_model_path(dir: str | Path = SCORING_MODEL_DIR) -> Path | None:
    versions = scoring_model_versions(dir)
    return versions[-1][1] if versions else None


def _parse_file(path: Path) -> ScoringModel:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ScoringModel.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — surface a plain message
        raise ScoringModelError(
            f"{path.name} is not a valid scoring model: {exc} "
            "(a pre-merge file? delete signals/scoring_model.v*.yaml to re-seed "
            "from the committed defaults)") from exc


# Parsed-model cache keyed on resolved path + (mtime_ns, size) — the config.py
# pattern; a new version file is a new path so publishes invalidate naturally.
_MODEL_CACHE: dict[str, tuple[int, int, ScoringModel]] = {}


@reqcache.memo(key=lambda dir=SCORING_MODEL_DIR: str(dir))
def load_scoring_model(dir: str | Path = SCORING_MODEL_DIR) -> ScoringModel:
    """The current scoring model: the highest version file, or the built-in
    default when none exists (bare test dirs). Returns a deep copy — ONE per
    request (reqcache): the dashboard asked ~28 times per load and paid a deep
    copy of the whole governed model each time. No caller mutates the returned
    model (checked), so sharing the request's copy is safe; requests never share."""
    path = latest_scoring_model_path(dir)
    if path is None:
        return default_scoring_model()
    key = canon(path)
    try:
        st = path.stat()
        hit = _MODEL_CACHE.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2].model_copy(deep=True)
    except OSError:
        st = None
    sm = _parse_file(path)
    if st is not None:
        _MODEL_CACHE[key] = (st.st_mtime_ns, st.st_size, sm)
    return sm.model_copy(deep=True)


def load_scoring_model_version(n: int, dir: str | Path = SCORING_MODEL_DIR) -> ScoringModel | None:
    """As-of read of one published version (drift itemization, score math)."""
    for version, path in scoring_model_versions(dir):
        if version == n:
            return _parse_file(path)
    return None


def scoring_model_stamp(dir: str | Path = SCORING_MODEL_DIR) -> str:
    """"vN (hash12)" — the run_meta/preflight stamp. "v0 (default)" when no
    file exists (built-in defaults in play)."""
    path = latest_scoring_model_path(dir)
    if path is None:
        return "v0 (default)"
    sm = load_scoring_model(dir)
    return f"v{sm.version} ({content_hash(path.read_bytes())[:12]})"
