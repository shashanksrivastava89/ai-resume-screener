"""Computed features (P7, §5.3) — pure, deterministic code over frozen artifacts.

What this module is
-------------------
The "calculator" half of Layer 2's inputs. Where `findings.py` asks a model to
match requirements, this module computes numbers with plain code: tenure stats,
must-have skill recency, scope trajectory, certification currency. Layer 2's
model receives these numbers (with the text they came from) and turns them into
capped, evidenced adjustments — the model interprets, the code measures.

Design rules (read before editing)
----------------------------------
1. PURE: no model calls, no randomness, no wall-clock. Every function takes an
   explicit `as_of` date, so replaying a run months later reproduces identical
   numbers. Breaking this breaks Layer-1/2 replayability and the audit story.
2. POLICY-PARAMETERIZED: thresholds (what's a "short stint", how recent is
   "recent") come from EffectivePolicy params — jurisdictions tune norms
   (IN: 9 months, JP: 18), code never hard-codes them.
3. FLOOR GUARDRAIL: employment gaps are computed NOWHERE here — not as a field,
   not as an intermediate. A test asserts no gap-shaped key ever appears in the
   FeatureSet schema. Do not add one.
4. CRUDE BEATS CLEVER: the text-matching here is deliberately simple token
   overlap. It is auditable and reproducible; the nuance lives in Layer 2 where
   every claim needs quoted evidence anyway.
"""

from __future__ import annotations

import re
import statistics
from datetime import date
from typing import Any

from pydantic import BaseModel

from resume_screener.models import EffectivePolicy, ResumeProfile, Rubric

# Employment types that never count against tenure math (registry description:
# "Contract, consulting and internship roles never count against anyone").
# Unlabeled roles are treated as permanent because most resumes don't label
# permanent jobs — only deviations get labeled.
NON_PERMANENT_TYPES = {"contract", "consulting", "internship", "self_employed"}

# Words too generic to indicate a requirement was exercised in a role.
# Tokens shorter than 3 characters are dropped separately (kills "of", "US", …).
_STOPWORDS = {
    "ability", "and", "etc", "excellent", "experience", "experienced", "for",
    "from", "good", "include", "includes", "including", "knowledge", "other",
    "proven", "related", "relevant", "skill", "skills", "strong", "such",
    "that", "the", "this", "various", "with", "work", "working",
}

# Title-seniority ladder for the trajectory heuristic. A title's level is the MAX
# of all patterns it matches ("Senior Accounts Executive" -> senior=3, not
# executive=1). Deliberately visible and editable — HR can read this table.
_SENIORITY_LADDER: list[tuple[int, re.Pattern[str]]] = [
    (5, re.compile(r"\b(director|vice\s*president|vp|president|chief|c[efo]o|head\s+of)\b", re.I)),
    (4, re.compile(r"\b(manager|head)\b", re.I)),
    (3, re.compile(r"\b(senior|lead|principal|supervisor)\b", re.I)),
    (2, re.compile(r"\b(accountant|analyst|officer|specialist|consultant)\b", re.I)),
    # "fresher" is Indian-English for a new graduate and STAYS — it is a
    # recogniser, not copy, and dropping it would change the trajectory feature
    # for every pilot résumé that uses the word. The rest of the world's ways of
    # saying the same thing are added beside it (2026-09-12). English titles only:
    # a German "Leiter" or a Hungarian "vezető" is not read, which is a known gap
    # for a later profile-prompt pass.
    (1, re.compile(r"\b(junior|trainee|intern|assistant|associate|executive|fresher"
                   r"|graduate|entry[- ]level|apprentice)\b", re.I)),
]

_CERT_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")  # a plausible year in a cert string


# --- Result shapes ----------------------------------------------------------------


class TenureStats(BaseModel):
    permanent_stints: int  # roles counted (unlabeled = permanent)
    excluded_stints: int  # contract/consulting/internship/self-employed — never penalized
    undated_stints: int  # roles we could not measure (no parseable start)
    completed_stints: int
    avg_completed_months: float | None
    current_stint_months: float | None  # None when no current role
    short_stints_in_window: int  # completed stints under the threshold, inside lookback
    short_stint_pattern: bool  # only True at >= min_stints_for_pattern (one short job ≠ pattern)


class MustHaveRecency(BaseModel):
    requirement_id: str
    matched_in_history: bool  # did any role's text mention this requirement's tokens?
    months_since_last_use: float | None  # 0 = exercised in a current role; None = never matched


class TrajectoryStats(BaseModel):
    first_level: int | None  # earliest dated role's seniority level (1..5)
    last_level: int | None  # latest dated role's level
    delta: int | None  # last - first; positive = grew. None when < 2 dated roles.
    dated_roles_considered: int


class CertificationStats(BaseModel):
    total: int
    dated_current: int  # has a year within stale_after_years of as_of
    dated_stale: int  # has a year older than that
    undated: int  # no year stated — neither current nor stale, just unknown


class FeatureSet(BaseModel):
    """Everything Layer 2 receives from this module, plus the audit trail of
    which policy params produced the numbers (`params_used`)."""

    candidate_id: str
    as_of: str  # ISO date all date math used — stamp it in run_meta for replay
    params_used: dict[str, Any]
    tenure: TenureStats
    must_have_recency: list[MustHaveRecency]
    fraction_must_haves_recent: float | None  # recent matches / ALL must-haves
    trajectory: TrajectoryStats
    certifications: CertificationStats
    domain_fit_cosine: float | None  # 0..1, computed by the caller via embed.py


# --- Date helpers ------------------------------------------------------------------


def _parse_ym(value: str | None) -> tuple[int, int] | None:
    """'YYYY-MM' or 'YYYY' -> (year, month). Bare years become January —
    a stable, documented convention rather than a guess."""
    if not value:
        return None
    match = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?", value.strip())
    if not match:
        return None
    year = int(match.group(1))
    month = min(max(int(match.group(2) or 1), 1), 12)
    return year, month


def _months_between(start: tuple[int, int], end: tuple[int, int]) -> float:
    return max(0.0, (end[0] - start[0]) * 12 + (end[1] - start[1]))


# --- Feature computations -----------------------------------------------------------


def tenure_stats(profile: ResumeProfile, params: dict[str, Any], as_of: date) -> TenureStats:
    """Stint-length stats over PERMANENT roles only, thresholds from policy."""
    short_months = float(params.get("short_stint_months", 12))
    lookback_years = int(params.get("lookback_years", 10))
    min_stints = int(params.get("min_stints_for_pattern", 2))
    window_start = (as_of.year - lookback_years, as_of.month)
    now = (as_of.year, as_of.month)

    permanent = excluded = undated = 0
    completed: list[float] = []
    current_months: float | None = None
    short_in_window = 0

    for role in profile.roles:
        if (role.employment_type or "permanent") in NON_PERMANENT_TYPES:
            excluded += 1
            continue
        permanent += 1
        start = _parse_ym(role.start)
        if start is None:
            undated += 1  # can't measure what isn't dated; never penalize it
            continue
        is_current = role.end is None
        end = _parse_ym(role.end) or now
        months = _months_between(start, end)
        if is_current:
            # Multiple "current" roles are rare but possible; keep the longest.
            current_months = max(current_months or 0.0, months)
        else:
            completed.append(months)
            # A stint only joins the short-stint pattern if it ENDED inside the
            # lookback window — ancient history doesn't count against anyone.
            if end >= window_start and months < short_months:
                short_in_window += 1

    return TenureStats(
        permanent_stints=permanent,
        excluded_stints=excluded,
        undated_stints=undated,
        completed_stints=len(completed),
        avg_completed_months=round(statistics.mean(completed), 1) if completed else None,
        current_stint_months=current_months,
        short_stints_in_window=short_in_window,
        short_stint_pattern=short_in_window >= min_stints,
    )


def _salient_tokens(text: str) -> set[str]:
    """Lowercased content words of a requirement (len >= 3, minus stopwords)."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in _STOPWORDS
    }


def must_have_recency(
    profile: ResumeProfile, rubric: Rubric, params: dict[str, Any], as_of: date
) -> tuple[list[MustHaveRecency], float | None]:
    """For each MUST-HAVE requirement: months since a role last 'exercised' it.

    Matching rule (crude by design, see module header): a role exercises a
    requirement when its title+summary contain >= 2 of the requirement's salient
    tokens, or >= half of them for very short requirements. The fraction returned
    counts recent matches over ALL must-haves, so unmatched ones read as
    not-recent rather than disappearing from the denominator.
    """
    recent_years = float(params.get("recent_years", 3))
    now = (as_of.year, as_of.month)
    results: list[MustHaveRecency] = []
    recent_count = 0
    must_haves = [r for r in rubric.requirements if r.kind == "must_have"]

    for requirement in must_haves:
        tokens = _salient_tokens(requirement.text)
        needed = min(2, max(1, len(tokens) // 2))  # 2 normally; 1 for tiny token sets
        best_months: float | None = None
        for role in profile.roles:
            role_text = f"{role.title} {role.summary}".lower()
            hits = sum(1 for t in tokens if t in role_text)
            if hits < needed:
                continue
            if role.end is None:
                best_months = 0.0  # exercised in a current role — as recent as it gets
                break
            end = _parse_ym(role.end)
            if end is None:
                continue  # matched an undated role: proves history, not recency
            months = _months_between(end, now)
            best_months = months if best_months is None else min(best_months, months)
        matched = best_months is not None
        if matched and best_months <= recent_years * 12:
            recent_count += 1
        results.append(
            MustHaveRecency(
                requirement_id=requirement.id,
                matched_in_history=matched,
                months_since_last_use=best_months,
            )
        )

    fraction = round(recent_count / len(must_haves), 3) if must_haves else None
    return results, fraction


def trajectory(profile: ResumeProfile) -> TrajectoryStats:
    """Seniority growth from first dated role to latest, via the title ladder."""

    def level_of(title: str) -> int | None:
        levels = [lvl for lvl, pattern in _SENIORITY_LADDER if pattern.search(title)]
        return max(levels) if levels else None

    dated = [
        (start, level)
        for role in profile.roles
        if (start := _parse_ym(role.start)) is not None
        and (level := level_of(role.title)) is not None
    ]
    dated.sort(key=lambda pair: pair[0])
    if len(dated) < 2:
        # One job (or unrankable titles) is not a trajectory — report unknown,
        # never a penalty. scope_trajectory's direction is plus-only anyway.
        only = dated[0][1] if dated else None
        return TrajectoryStats(
            first_level=only, last_level=only, delta=None, dated_roles_considered=len(dated)
        )
    return TrajectoryStats(
        first_level=dated[0][1],
        last_level=dated[-1][1],
        delta=dated[-1][1] - dated[0][1],
        dated_roles_considered=len(dated),
    )


def certification_currency(
    profile: ResumeProfile, params: dict[str, Any], as_of: date
) -> CertificationStats:
    """Split certifications into dated-current / dated-stale / undated.

    Undated certificates are UNKNOWN, not stale — the signal's direction is
    plus-only, so unknown simply adds nothing (never subtracts).
    """
    stale_after = float(params.get("stale_after_years", 5))
    current = stale = undated = 0
    for cert in profile.certifications:
        years = [int(y) for y in _CERT_YEAR.findall(cert)]
        if not years:
            undated += 1
        elif as_of.year - max(years) <= stale_after:
            current += 1
        else:
            stale += 1
    return CertificationStats(
        total=len(profile.certifications),
        dated_current=current,
        dated_stale=stale,
        undated=undated,
    )


def compute_features(
    profile: ResumeProfile,
    rubric: Rubric,
    policy: EffectivePolicy,
    as_of: date,
    domain_fit_cosine: float | None = None,
) -> FeatureSet:
    """Assemble the full FeatureSet. `domain_fit_cosine` is passed in by the
    caller (it needs the embedding client; this module stays pure)."""

    def params_for(signal_id: str) -> dict[str, Any]:
        override = policy.signal_overrides.get(signal_id)
        return dict(override.params) if override and override.params else {}

    tenure_params = params_for("tenure_pattern_vs_stability")
    recency_params = params_for("skill_recency")
    cert_params = params_for("certification_currency")

    recency_list, fraction_recent = must_have_recency(profile, rubric, recency_params, as_of)

    return FeatureSet(
        candidate_id=profile.candidate_id,
        as_of=as_of.isoformat(),
        # The audit trail: exactly which knobs shaped these numbers, and whose.
        params_used={
            "jurisdiction": policy.jurisdiction,
            "policy_pack": policy.pack,
            **{f"tenure.{k}": v for k, v in tenure_params.items()},
            **{f"skill_recency.{k}": v for k, v in recency_params.items()},
            **{f"certification.{k}": v for k, v in cert_params.items()},
        },
        tenure=tenure_stats(profile, tenure_params, as_of),
        must_have_recency=recency_list,
        fraction_must_haves_recent=fraction_recent,
        trajectory=trajectory(profile),
        certifications=certification_currency(profile, cert_params, as_of),
        domain_fit_cosine=domain_fit_cosine,
    )
