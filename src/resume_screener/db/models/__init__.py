"""ORM models aggregator.

Importing this package registers every model on `Base.metadata`, so Alembic's
env.py (which imports it) sees the full schema for autogenerate, and callers can
`from resume_screener.db.models import Region, Requisition, ...`.

Grouping:
- region      : Region, Jurisdiction, Feature, RegionFeature
- identity    : AppUser, UserRegionRole
- requisition : JdDocument, Requisition, RequisitionContact
- rubric      : Rubric, Requirement (a hard gate = kind='hard_gate' row, P33)
- candidate   : Candidate, RequisitionCandidate
- preference  : HrPreference (P49; replaces HrPreferenceSet/Override)
- event       : AppEvent (P49; the app-event spine mirror)
- collaboration: HmTake, ChecklistApproval, LegalHold (P49)
- governance  : Signal, ExcludedSignal, RegistryVersion, PolicyPack, PolicySignalOverride
- runtime     : Run, Decision, ScreeningJob, AuditEntry, ReviewSignoff
"""

from __future__ import annotations

from resume_screener.db.models.candidate import Candidate, RequisitionCandidate
from resume_screener.db.models.collaboration import (
    ChecklistApproval,
    HmTake,
    LegalHold,
)
from resume_screener.db.models.event import AppEvent
from resume_screener.db.models.governance import (
    ExcludedSignal,
    PolicyPack,
    PolicySignalOverride,
    RegistryVersion,
    Signal,
)
from resume_screener.db.models.identity import ROLES, AppUser, UserRegionRole
from resume_screener.db.models.preference import HrPreference
from resume_screener.db.models.region import Feature, Jurisdiction, Region, RegionFeature
from resume_screener.db.models.requisition import (
    JdDocument,
    Requisition,
    RequisitionContact,
)
from resume_screener.db.models.rubric import Requirement, Rubric
from resume_screener.db.models.runtime import (
    AuditEntry,
    Decision,
    ReviewSignoff,
    Run,
    ScreeningJob,
)

__all__ = [
    "ROLES",
    "AppEvent",
    "AppUser",
    "AuditEntry",
    "Candidate",
    "ChecklistApproval",
    "Decision",
    "ExcludedSignal",
    "Feature",
    "HmTake",
    "HrPreference",
    "JdDocument",
    "Jurisdiction",
    "LegalHold",
    "PolicyPack",
    "PolicySignalOverride",
    "Region",
    "RegionFeature",
    "RegistryVersion",
    "Requirement",
    "Requisition",
    "RequisitionCandidate",
    "RequisitionContact",
    "ReviewSignoff",
    "Rubric",
    "Run",
    "ScreeningJob",
    "Signal",
    "UserRegionRole",
]
