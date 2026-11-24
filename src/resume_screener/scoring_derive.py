"""Seam: derive the org scoring model from an uploaded hiring-policy document.

The scoring model is the organization's policy layer (Legal + HR Head). The
intended future capability — parallel to policy_derive on the jurisdiction side —
is: upload the company hiring policy, and an LLM PROPOSES scoring-model topics
(never-scored factors) + signal justifications, which a second leader approves
through the normal scoring_model change proposal. Enforcement stays deterministic.

This module is the SEAM only (interface + guardrail note); the derivation itself
is a later phase. Kept here so the call site and contract exist now, exactly as
policy_derive.derive_blocked_patterns does for the jurisdiction floor.
"""

from __future__ import annotations

from resume_screener.scoring_model import ScoringModel


class ScoringDerivationUnavailable(RuntimeError):
    """Raised (plain message) until the org-policy-doc derivation phase lands."""


def derive_scoring_model_from_doc(
    doc_text: str, current_model: ScoringModel
) -> dict:  # pragma: no cover - seam, not yet implemented
    """Propose scoring-model topics/signals from an uploaded hiring-policy doc.

    Returns the same PROPOSAL shape the Scoring page would submit
    (``{topics: [...], signals: [...], note: str}``) once implemented. Today it
    is a declared seam — callers should treat a raised
    ``ScoringDerivationUnavailable`` as "compose the model by hand for now"."""
    raise ScoringDerivationUnavailable(
        "Deriving the scoring model from an uploaded hiring-policy document is a "
        "planned capability — for now, edit the scoring model directly on the "
        "Scoring page (each change is still a second-leader proposal).")
