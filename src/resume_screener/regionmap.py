"""Region + timezone constants (P29, Addendum 11 Part D4).

Region is an ACCESS/DISPLAY lens (list pills, dual-timezone stamps, P38
enforcement, P45 reports) — never a law lens; jurisdiction→policy resolution
stays in policy.py. P38 pins these constants to db/seed.py's region framework
with a sync test so the two can never drift.
"""

from __future__ import annotations

REGION_OF_JURISDICTION: dict[str, str] = {
    "IN": "IN",
    "SG": "APAC",
    "JP": "APAC",
    "EU": "EMEA",
    "US-default": "AMER",
    "US-NYC": "AMER",
    "US-CA": "AMER",
}

# IANA zones for "requisition-local" time rendering (viewer TZ is the browser's).
TZ_OF_JURISDICTION: dict[str, str] = {
    "IN": "Asia/Kolkata",
    "SG": "Asia/Singapore",
    "JP": "Asia/Tokyo",
    "EU": "Europe/Brussels",
    "US-NYC": "America/New_York",
    "US-CA": "America/Los_Angeles",
    "US-default": "America/Chicago",
}


def tz_for_jurisdiction(code: str) -> str:
    """The requisition-local IANA zone for a jurisdiction code (same US-
    fallback as region_for_jurisdiction). Unknown codes render in UTC — a
    display default, never an error."""
    code = (code or "").strip()
    for candidate in (code, code.upper()):
        if candidate in TZ_OF_JURISDICTION:
            return TZ_OF_JURISDICTION[candidate]
    if code.upper().startswith("US"):
        return TZ_OF_JURISDICTION["US-default"]
    return "UTC"


def region_for_jurisdiction(code: str) -> str:
    """The governing region for a jurisdiction code — the same US- fallback logic
    as policy._resolve_pack_path: exact match, then uppercase, then any unknown
    US-* code lands on US-default's region. An unknown non-US code maps to itself
    (a display default, mirroring IN→IN), never an error — this is a lens."""
    code = (code or "").strip()
    for candidate in (code, code.upper()):
        if candidate in REGION_OF_JURISDICTION:
            return REGION_OF_JURISDICTION[candidate]
    if code.upper().startswith("US"):
        return REGION_OF_JURISDICTION["US-default"]
    return code.upper()


def region_for_country(alpha2: str | None) -> str | None:
    """The access region a COUNTRY belongs to, via the pack that governs hiring
    there (the global rollout, 2026-09-12).

    Used to give a new user a sensible starting lens when their directory record
    says where they are. None when we cannot say — and None is a real answer: the
    lens then opens on "All regions", which shows MORE, never less."""
    from resume_screener import countries

    found = countries.lookup(alpha2)
    if found is None or found.pack is None:
        return None
    region = region_for_jurisdiction(found.pack)
    return region if region in set(REGION_OF_JURISDICTION.values()) else None


def region_name(code: str | None) -> str:
    """The display name for a region code. Unknown codes read as themselves."""
    return REGION_NAMES.get((code or "").upper(), code or "")


#: Display names, so a picker never has to carry its own copy of them.
REGION_NAMES: dict[str, str] = {
    "IN": "India",
    "APAC": "Asia-Pacific",
    "EMEA": "Europe, Middle East & Africa",
    "AMER": "Americas",
}
