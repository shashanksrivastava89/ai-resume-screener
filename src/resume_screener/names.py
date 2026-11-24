"""Human display names for emails (2026-07-20).

Notifications, dashboards and audit rails identify people by their SSO EMAIL —
the only identity the engine stores on an event or decision. Recruiters asked to
see NAMES instead ("Rahul suggested…", the recruiter column on the dashboard),
with the raw email kept on hover. This module is the single email→name resolver,
so every surface agrees on what to call someone.

Resolution order (first hit wins), all failure-safe:
  1. an explicit name the caller already holds — a stored ``actor_name`` or the
     live SSO principal's ``name`` claim — passed as ``known``; never re-derived
     when we already know it;
  2. the people directory (``people.py``, 2026-09-06) — the name from the
     person's own sign-in (the ``name`` claim), Graph when enabled, or a name a
     recruiter typed beside the address; this is what turns a payroll UPN into
     a person on every surface at once;
  3. the per-requisition hiring-manager name the recruiter typed at invite
     (``req_meta.hm.name``), when the email is that HM's;
  4. a title-cased guess from the email local-part
     ("arjun.test@example.com" → "Arjun Test") — always available, so a name is
     never blank.

Names are for display only; this module holds no PII the app didn't already
have (the email itself).
"""

from __future__ import annotations

import re

_SPLIT = re.compile(r"[._\-+]+")


def humanize_email(email: str) -> str:
    """The always-available fallback: the email local-part as a name.

    'arjun.test@example.com' → 'Arjun Test'; a local-part with no separators is
    title-cased as one word; an empty/garbage value returns itself stripped."""
    local = str(email or "").split("@", 1)[0]
    parts = [p for p in _SPLIT.split(local) if p]
    if not parts:
        return str(email or "").strip()
    return " ".join(p[:1].upper() + p[1:] for p in parts)


def display_name(email: str, *, req_meta: dict | None = None, known: str = "") -> str:
    """The best human name for `email`. `known` is an already-resolved name the
    caller holds (a stored actor_name / SSO principal name) and always wins.
    `req_meta` lets an HM's typed name resolve when the email is that HM's."""
    email = str(email or "").strip()
    if known and known.strip():
        return known.strip()
    if not email:
        return ""
    from resume_screener import people

    learned = people.display_name(email)
    if learned:
        return learned
    if req_meta:
        hm = req_meta.get("hm") or {}
        if (str(hm.get("email", "")).strip().lower() == email.lower()
                and str(hm.get("name", "")).strip()):
            return str(hm["name"]).strip()
    return humanize_email(email)


def first_name(email: str, *, req_meta: dict | None = None, known: str = "") -> str:
    """Just the given name, for prose ('Rahul suggested a checklist edit')."""
    full = display_name(email, req_meta=req_meta, known=known)
    return full.split()[0] if full else ""
