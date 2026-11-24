"""ATS port (P2, CLAUDE.md §1): the ONE interface every ATS adapter implements.

Why a port: the engine must not know whether candidates come from a local folder
(PoC) or iCIMS/Greenhouse/Lever (production, §12). Everything downstream talks
to these five methods and the three shapes they exchange (Requisition,
CandidateRef, DocumentRef). Swapping ATS = writing one new adapter + one config
line (ats.provider) — no core changes. That seam is a spec requirement, not an
abstraction luxury; don't bypass it with direct folder reads outside ats/.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from resume_screener.models import CandidateRef, DocumentRef, Requisition


class ATSPort(ABC):
    @abstractmethod
    def fetch_requisitions(self) -> list[Requisition]: ...

    @abstractmethod
    def fetch_candidates(self, req: Requisition) -> list[CandidateRef]: ...

    @abstractmethod
    def fetch_documents(self, candidate: CandidateRef) -> list[DocumentRef]: ...

    @abstractmethod
    def write_back(self, req: Requisition, candidate_id: str, payload: dict[str, Any]) -> None:
        """Push score/band/result-URL back to the ATS (production; local = logged no-op)."""

    @abstractmethod
    def on_stage_change(self, req: Requisition, candidate_id: str, stage: str) -> None:
        """React to an ATS stage change (production; local = logged no-op)."""
