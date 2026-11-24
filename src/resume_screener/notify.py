"""Notification stub (P41). Real delivery (email/Teams) is a §12 concern; here a
notify() just records the INTENT on the app event spine so the flow is complete
and auditable end-to-end offline. No network, no secrets."""

from __future__ import annotations

from resume_screener.events import emit_event


def notify(
    to: str, summary: str, *, actor: str, actor_role: str = "",
    req_id: str | None = None, data: dict | None = None,
) -> dict:
    """Record a notification to `to` (an email). Emits a `notify` event carrying
    the recipient + a plain-language summary; delivery itself is deferred to §12."""
    payload = {"to": to, **(data or {})}
    return emit_event("notify", actor=actor, actor_role=actor_role, req_id=req_id,
                      summary=summary, data=payload)
