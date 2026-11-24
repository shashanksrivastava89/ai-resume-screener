"""Region base-framework seed (Phase 7): regions, jurisdiction→region mapping, the
feature catalog + per-region defaults, and the dev user's all-regions grant.

This is the SCHEMA + SEED half of the region model. Hard enforcement (filtering
every page by the signed-in user's region) and the SSO region-picker ride on P27;
here we only populate the reference data and expose it read-only, so local dev is
unchanged (the dev user gets an all-regions grant = sees everything).

Idempotent: every step is an upsert keyed by natural code, so `screener db seed`
is safe to re-run.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from resume_screener.db.repositories import identity as identity_repo
from resume_screener.db.repositories import regions as regions_repo

# Regions users are granted against. India is its own region (the pilot); the rest
# group the seed jurisdictions. Codes are stable; names are display-only.
REGIONS: list[tuple[str, str]] = [
    ("IN", "India"),
    ("APAC", "Asia-Pacific"),
    ("EMEA", "Europe, Middle East & Africa"),
    ("AMER", "Americas"),
]

# (jurisdiction code, name, plain-language law, governing region). Mirrors the
# policy packs + service.JURISDICTION_LABELS; the region is the one whose HR
# Head/Legal owns that jurisdiction's rules + audit.
JURISDICTIONS: list[tuple[str, str, str, str]] = [
    ("IN", "India", "DPDP Act 2023", "IN"),
    ("SG", "Singapore", "PDPA + TAFEP fair-employment", "APAC"),
    ("JP", "Japan", "APPI", "APAC"),
    ("EU", "European Union", "AI Act + GDPR", "EMEA"),
    ("US-default", "United States", "EEOC / Title VII / ADA / ADEA", "AMER"),
    ("US-NYC", "New York City", "Local Law 144", "AMER"),
    ("US-CA", "California", "FEHA / CCPA / CPRA", "AMER"),
]

# The feature catalog the app can gate behavior on per region. Keys map to real
# capabilities; the §12 / disabled ones are seeded OFF everywhere.
FEATURES: list[tuple[str, str, str]] = [
    ("blind_review", "Blind review", "Anonymize candidates in Review (Candidate 1..N)."),
    ("audit_export", "Audit pack export", "Export a role's run + audit trail."),
    ("jurisdiction_editor", "Jurisdiction pack editor",
     "Admins edit obligations / retention / monitoring per policy pack."),
    ("candidate_enrichment", "Candidate-links enrichment",
     "Additive enrichment from candidate-provided links — disabled in the PoC (§12)."),
    ("learning_loop", "Governed learning loop",
     "Offline, human-gated calibration from decisions — deferred to §12."),
]
# On by default in every seeded region; the rest stay off (they are §12/disabled).
ENABLED_FEATURES = {"blind_review", "audit_export", "jurisdiction_editor"}


def seed_region_framework(
    session: Session, *, dev_user: str | None = None, dev_role: str = "IT"
) -> dict:
    """Seed/refresh the region reference data + the dev user's all-regions grant.
    Returns a per-kind count report. Idempotent."""
    for code, name in REGIONS:
        regions_repo.upsert_region(session, code, name)
    for code, name, law, region_code in JURISDICTIONS:
        regions_repo.upsert_jurisdiction(session, code, name, law=law, region_code=region_code)
    for key, name, description in FEATURES:
        regions_repo.upsert_feature(session, key, name, description=description)
    for region_code, _ in REGIONS:
        for key, _name, _desc in FEATURES:
            regions_repo.set_region_feature(session, region_code, key, key in ENABLED_FEATURES)

    granted = False
    if dev_user:
        # An all-regions grant (region_id NULL) so local dev sees every region — the
        # same "unrestricted" behavior as before the region framework existed.
        identity_repo.grant_role(session, dev_user, dev_role, region_code=None)
        granted = True

    return {
        "regions": len(REGIONS),
        "jurisdictions": len(JURISDICTIONS),
        "features": len(FEATURES),
        "dev_grant": dev_user if granted else None,
    }
