"""Deterministic fairness classifier for free-text HR inputs (P39; reused by P43).

`classify_text` decides whether a plain-language preference or checklist
requirement is OK to apply, needs HR-Head approval, or is blocked outright.
It is OFFLINE and precision-first. Severest level wins.

- BLOCKED = protected attributes (age/DOB/graduation-year phrasing, gender,
  religion, caste/community, marital status, pregnancy, nationality, disability,
  sexual orientation) plus any region-specific legal block. No approval path —
  these can never be applied. As of 2026-07-21 the blocked set is GOVERNED DATA
  (the jurisdiction ``blocked_patterns`` in policies/base.yaml + overlays),
  controlled by Legal + HR Head through a second-leader change proposal — NOT
  hard-coded. Callers pass the resolved set for the req's jurisdiction; the code
  lexicon below survives only as an unreachable-config fallback.
- WARN = the governed never-scored topics whose ``stages`` include this input's
  stage ("preferences" or "checklist"), plus jurisdiction ``warn`` patterns
  (location/city preference, language preference, employer age — also governed
  data now) and any pack additional_exclusions keywords passed via
  `extra_exclusions`. A human (HR Head) decides.
- OK = no conflict. A checklist row applies; a preference still waits for an HR
  Head's sign-off (2026-10-16 — preferences.py).

Per-jurisdiction: something allowed in one region can be blocked in another
(e.g. salary history is a WARN topic generally but BLOCKED for California roles).
Preferences are user-global, so at authoring they are classified against the
strictest UNION of all jurisdictions (blocked anywhere ⇒ shown); per-req scoring
re-checks against that req's jurisdiction (policy.resolve_policy).

The reason string is plain recruiter language and is shown to the user VERBATIM.

Spelling does not get past it (2026-10-16, pen-test RS-01). The governed phrases and
regexes run against the text as typed AND against folded copies of it — Unicode
compatibility forms (fullwidth letters, the long s, ligatures, no-break spaces),
invisible characters removed (zero-width spaces and joiners, soft hyphens),
accents dropped, Cyrillic/Greek look-alike letters read as the Latin ones they
imitate, letter-for-digit swaps inside words ("pr3gn@ncy", "fema1e") and spaced-out
letters ("p r e g n a n t") rejoined. The text as typed is always checked first, so a
fold can only ADD a match, never remove one: no existing verdict moves down. The
folds are matching mechanics, like compile_phrases' word boundaries — the vocabulary
is still governed data.

What spelling cannot reach — a proxy phrased in ordinary words ("no extended career
breaks"), another language — is the intent screen's job (prefcheck_semantic), which
may only ESCALATE a level, never downgrade; the deterministic result here is the floor.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence

from pydantic import BaseModel

from resume_screener.models import BlockedPattern
from resume_screener.scoring_model import TopicRule, compile_phrases, match_topics


class TextCheck(BaseModel):
    """The classifier verdict. `basis_ids` name what matched (for the audit
    record); `reason` is the plain-language sentence shown to the user."""

    level: str  # "ok" | "warn" | "blocked"
    basis_ids: list[str] = []
    reason: str = ""
    # How the verdict was reached, for the audit record (prefcheck_semantic,
    # 2026-10-16). `level` above is already the merged one; these say what the
    # phrase lists found, what the intent screen found, and whether it ran:
    # "ok" (ran, agreed) | "flagged" (ran, escalated) | "unavailable" | "off".
    deterministic_level: str | None = None
    semantic_level: str | None = None
    semantic: str = "off"


# --- FALLBACK SEED: the universal floor, if policies/base.yaml is unreachable ----------
# These are now GOVERNED DATA in base.yaml (blocked_patterns). They remain here
# ONLY as the code fallback so the fairness floor is never absent (the same
# belt-and-suspenders pattern scoring_model uses with _fallback_model). base.yaml
# is authoritative; edit the floor there (via a change proposal), not here.
_BLOCKED_PATTERNS: dict[str, re.Pattern[str]] = {
    # Mirrors base.yaml's protected_demographic exactly
    # (test_blocked_patterns.test_the_code_seed_mirrors_the_base_floor).
    "protected_demographic": re.compile(
        r"\b(?:males?|females?|gender|(?:un)?married|marital|pregnan\w*|caste|"
        r"religion|nationality|ethnic(?:ity)?|(?:wo)?men\s+(?:only|candidates?)|"
        r"only\s+(?:wo)?men|lad(?:y|ies)\s+(?:only|candidates?))\b",
        re.IGNORECASE,
    ),
    "age": re.compile(
        r"\b(?:under|below|over|above)\s*\d{2}\b"
        r"|\baged?\s*\d{2}\b|\bage of\b|\b\d{2}\s*(?:years?\s*old|yo)\b"
        r"|\b(?:young|old)(?:er)?\s+candidates?\b"
        r"|\bdate of birth\b|\bdob\b|\bgraduation year\b|\bborn\s+(?:before|after|in)\b",
        re.IGNORECASE,
    ),
    "disability": re.compile(
        r"\bdisabilit(?:y|ies)\b|\bdisabled\b|\bdifferently[- ]abled\b|\bhandicap(?:ped)?\b",
        re.IGNORECASE,
    ),
    "sexual_orientation": re.compile(
        r"\bsexual orientation\b|\blgbtq?\+?\b|\b(?:gay|lesbian|homosexual|heterosexual|bisexual)\b",
        re.IGNORECASE,
    ),
}

# --- FALLBACK SEED (warn proxies) — also governed in base.yaml now ----------------------
# (Everything topic-shaped — gaps, salary history, school prestige, culture fit,
# similarity, commute — comes from the governed scoring-model topics instead.)
_WARN_PATTERNS: dict[str, re.Pattern[str]] = {
    "location_preference": re.compile(
        r"\btier[- ]?(?:1|one|2|two)\s*(?:cit(?:y|ies)|metro)\b"
        r"|\bmetro(?:politan)?\s+(?:cit(?:y|ies)|candidates?)\b"
        r"|\bprefers?\s+candidates?\s+(?:in|from|based\s+in|located\s+in|living\s+in)\b"
        r"|\blocal\s+candidates?\s+(?:only|preferred)\b"
        r"|\b(?:willing|able)\s+to\s+relocat|\bmust\s+relocat",
        re.IGNORECASE,
    ),
    "language_preference": re.compile(
        r"\bnative\s+\w+\s+speakers?\b|\bprefers?\s+\w+[- ]speaking\b"
        r"|\blanguage\s+preference\b|\bmother\s*tongue\b",
        re.IGNORECASE,
    ),
    "employer_age": re.compile(
        r"\bstart[- ]?ups?\b|\bearly[- ]stage\s+(?:compan(?:y|ies)|firms?)\b"
        r"|\byoung\s+compan(?:y|ies)\b|\bestablished\s+(?:compan(?:y|ies)|firms?)\b"
        r"|\bemployer\s+age\b|\bcompany\s+age\b",
        re.IGNORECASE,
    ),
}

# Plain-language reason lead-ins keyed by the FIRST matched basis (shown verbatim).
# Plain sentences a recruiter can act on. They say WHY it stops here and what the
# rule is — never how to word it so it would pass (2026-09-07): suggesting a rewrite
# is coaching someone around a fairness rule, which is the opposite of the point.
_BLOCKED_REASONS: dict[str, str] = {
    "protected_demographic": "Hiring here can never be based on gender, religion, caste, "
                             "ethnicity, marital status or nationality — so this can't be "
                             "used, in any wording.",
    "age": "Hiring here can never be based on age — including things that stand in for it, "
           "like a date of birth or the year someone graduated. This can't be used, in any "
           "wording.",
    "disability": "Hiring here can never be based on disability — so this can't be used, in "
                  "any wording.",
    "sexual_orientation": "Hiring here can never be based on sexual orientation — so this "
                          "can't be used, in any wording.",
}
_WARN_REASONS: dict[str, str] = {
    "location_preference": "This looks like a location or city preference — a common proxy. "
                           "It needs HR Head approval before it applies.",
    "language_preference": "This looks like a language preference — a possible proxy. It "
                           "needs HR Head approval before it applies.",
    "employer_age": "This looks like it's about a company's age or size (e.g. start-ups) — a "
                    "proxy. It needs HR Head approval before it applies.",
    "pack_exclusion": "This matches a factor your policy pack has ruled out. It needs HR "
                      "Head approval before it applies.",
}

# Not "applies right away": since 2026-10-16 a preference still waits for sign-off.
_OK_REASON = "No conflicts with the fairness rules."


# --- spelling folds (2026-10-16, pen-test RS-01) --------------------------------------
# Look-alike letters that render as Latin ones — Cyrillic and Greek, lower case
# (fold_text casefolds first, so the upper-case look-alikes arrive here as these).
# A representation fold, not vocabulary: it says what a character LOOKS like.
_CONFUSABLES = str.maketrans({
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s",
    "\u0501": "d", "\u04bb": "h", "\u04cf": "l", "\u0432": "b", "\u043d": "h",
    "\u0442": "t", "\u043a": "k", "\u043c": "m", "\u0457": "i", "\u0491": "r",
    "\u03b1": "a", "\u03b5": "e", "\u03bf": "o", "\u03c1": "p", "\u03bd": "v",
    "\u03ba": "k", "\u03b9": "i", "\u03c4": "t", "\u03c5": "u", "\u03c7": "x",
    "\u03b2": "b", "\u03b7": "n",
})
# Digits and symbols standing in for letters — applied only INSIDE a word that also
# has letters, so "2019", "404" and "ISO 27001" are never touched.
_LEET = {"3": "e", "4": "a", "@": "a", "0": "o", "5": "s", "$": "s", "7": "t", "!": "i"}
_SPACED = re.compile(r"(?<![^\W_])((?:[^\W_][ .\-]){3,}[^\W_])(?![^\W_])")


def _clean(text: str) -> str:
    """Compatibility forms folded (NFKC: fullwidth letters, the long s, ligatures,
    no-break spaces) and invisible format characters dropped (zero-width spaces and
    joiners, soft hyphens, BOM), case folded. Accents are KEPT."""
    nfkc = unicodedata.normalize("NFKC", text)
    visible = "".join(ch for ch in nfkc if unicodedata.category(ch) != "Cf")
    return " ".join(visible.casefold().split())


def fold_text(text: str) -> str:
    """_clean, plus accents dropped and look-alike letters read as Latin."""
    decomposed = unicodedata.normalize("NFKD", _clean(text))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return plain.translate(_CONFUSABLES)


def _join_spaced(text: str, *, keep_first: bool = False) -> str:
    """"p r e g n a n t" -> "pregnant": four or more single letters separated by one
    space, dot or hyphen. Three ("C P A", "U.S.A") are left alone. keep_first leaves
    the run's first letter as its own word, for a one-letter word just before the
    spaced one: "start a f a m i l y" -> "start a family", not "start afamily"."""
    def join(m: re.Match[str]) -> str:
        run = m.group(1)
        if keep_first:
            return run[:2] + re.sub(r"[ .\-]", "", run[2:])
        return re.sub(r"[ .\-]", "", run)
    return _SPACED.sub(join, text)


def _unleet(text: str, one: str) -> str:
    out = []
    for token in text.split(" "):
        if any(c.isalpha() for c in token) and any(c in _LEET or c == "1" for c in token):
            token = "".join(_LEET.get(c, one if c == "1" else c) for c in token)
        out.append(token)
    return " ".join(out)


def text_variants(text: str) -> list[str]:
    """What the governed patterns are run against, the text as typed FIRST: it,
    cleaned, folded, and folded with digit swaps undone ("1" read as "i", then as
    "l": "d1sab1l1ty", "fema1e"). Escalate-only by construction."""
    folded = fold_text(text)
    candidates = [text, _clean(text), folded]
    for joined in (_join_spaced(folded), _join_spaced(folded, keep_first=True)):
        candidates += [_unleet(joined, "i"), _unleet(joined, "l")]
    out: list[str] = []
    for v in candidates:
        if v and v not in out:
            out.append(v)
    return out


def _governed_topics() -> list[TopicRule]:
    from resume_screener.scoring_model import load_scoring_model

    return list(load_scoring_model().topics)


def _fallback_blocked_patterns() -> list[BlockedPattern]:
    """Rebuild the governed floor from the code seed when base.yaml is absent."""
    out: list[BlockedPattern] = []
    for bid, pat in _BLOCKED_PATTERNS.items():
        out.append(BlockedPattern(
            id=bid, name=bid.replace("_", " ").capitalize(), reason=_BLOCKED_REASONS[bid],
            severity="blocked", floor=True, source="seed",
            stages=["preferences", "checklist"], regexes=[pat.pattern]))
    for wid, pat in _WARN_PATTERNS.items():
        out.append(BlockedPattern(
            id=wid, name=wid.replace("_", " ").capitalize(), reason=_WARN_REASONS[wid],
            severity="warn", floor=True, source="seed",
            stages=["preferences", "checklist"], regexes=[pat.pattern]))
    return out


def _floor_patterns() -> list[BlockedPattern]:
    """The universal floor for a caller that did not pass a jurisdiction set —
    base.yaml's blocked_patterns, or the code seed if base is unreachable."""
    from resume_screener import policy

    pats = policy.base_blocked_patterns()
    return pats if pats else _fallback_blocked_patterns()


_REGEX_CACHE: dict[str, re.Pattern[str]] = {}


def _compiled(rx: str) -> re.Pattern[str]:
    pat = _REGEX_CACHE.get(rx)
    if pat is None:
        pat = re.compile(rx, re.IGNORECASE)
        _REGEX_CACHE[rx] = pat
    return pat


def _pattern_fires(p: BlockedPattern, variants: Sequence[str], stage: str) -> bool:
    """Deterministic match: the pattern's literal phrases OR any of its regexes, on
    any spelling variant of the text, but only where the pattern is enforced
    (stage membership)."""
    if stage not in p.stages:
        return False
    if p.phrases:
        phrase_pat = compile_phrases(p.phrases)
        if phrase_pat is not None and any(phrase_pat.search(v) for v in variants):
            return True
    return any(_compiled(rx).search(v) for rx in p.regexes for v in variants)


def classify_text(
    text: str,
    *,
    blocked_patterns: Sequence[BlockedPattern] | None = None,
    topics: Sequence[TopicRule] | None = None,
    extra_exclusions: list[str] | None = None,
    stage: str = "preferences",
) -> TextCheck:
    """Classify one free-text input for `stage` ("preferences" | "checklist").

    `blocked_patterns` is the jurisdiction floor resolved for the req (or the
    strictest union for a user-global preference); when None the universal base
    floor is used. Governed topics enforced at this stage contribute their
    trigger phrases; `extra_exclusions` are pack additional_exclusions keywords
    that escalate to WARN. Severest level wins — a blocked phrase is never
    downgraded by a warn match."""
    text = (text or "").strip()
    if not text:
        return TextCheck(level="ok", basis_ids=[], reason=_OK_REASON)
    variants = text_variants(text)

    # An EMPTY set is treated as "not resolved" and falls back to the universal
    # floor (base.yaml, then the code seed): every real jurisdiction includes at
    # least the universal floor, so an empty list only ever means a policy read
    # failed — the fairness floor must never silently vanish (guardrail §6).
    patterns = list(blocked_patterns) if blocked_patterns else _floor_patterns()

    # BLOCKED first — severest wins, no approval path.
    hard = [p for p in patterns if p.severity == "blocked" and _pattern_fires(p, variants, stage)]
    if hard:
        return TextCheck(level="blocked", basis_ids=[p.id for p in hard], reason=hard[0].reason)

    warn: list[str] = []
    reason = ""
    # Governed never-scored topics first (parity with the pre-2026-07-21 order),
    # then jurisdiction warn patterns, then pack additional_exclusions keywords.
    active = list(topics) if topics is not None else _governed_topics()
    for variant in variants:
        for topic in match_topics(variant, active, stage):
            if topic.id in warn:
                continue
            warn.append(topic.id)
            if not reason:
                reason = (f"This touches “{topic.name}” — never scored here. "
                          "It needs HR Head approval before it applies.")
    for p in patterns:
        if p.severity == "warn" and _pattern_fires(p, variants, stage):
            warn.append(p.id)
            if not reason:
                reason = p.reason
    for kw in extra_exclusions or []:
        kw = (kw or "").strip()
        if kw and any(re.search(re.escape(kw), v, re.IGNORECASE) for v in variants):
            warn.append("pack_exclusion")
            if not reason:
                reason = _WARN_REASONS["pack_exclusion"]
            break
    if warn:
        return TextCheck(level="warn", basis_ids=warn, reason=reason)

    return TextCheck(level="ok", basis_ids=[], reason=_OK_REASON)
