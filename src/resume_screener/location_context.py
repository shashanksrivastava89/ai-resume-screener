"""How far is this candidate from the job? (2026-08-26)

NON-SCORING, and structurally so. Four separate things keep it out of the score:

1. It reads the ORIGINAL document, never the redacted text — because `redact.py` strips
   address lines before anything scores, so the scoring path is incapable of seeing
   location even if someone wired it in by mistake. Same pattern as `contacts.py`, which
   regex-extracts email and phone for the recruiter and likewise never scores.
2. The result is attached to the run artifact as CONTEXT, alongside extraction
   confidence — not to `ScoreResult`, and not to any `Adjustment`.
3. `commute_or_address` is a FLOOR never-scored topic (CLAUDE.md §6.6), so scoring this
   would take a SuperAdmin structural proposal and a second leader. The critic
   independently flags a *checklist row* phrased as a distance requirement, so the scored
   path stays closed while this one is open.
4. Nothing here can return a band, a weight or a point value. The only number is
   kilometres.

WHAT IT HONESTLY KNOWS. Distance between two place-name centroids from
`signals/places.yaml`, great-circle. That means:
  * it is an APPROXIMATION — city centre to city centre, at ~1 km coordinate precision,
    and shorter than any real road route. The UI renders "≈ N km" for that reason.
  * it is DETERMINISTIC and replayable, the same property Layer 1 guarantees: the same
    résumé and requisition always yield the same number and anyone can check it. That is
    why this is a table and not a geocoding service — and it also means no candidate's
    location ever leaves the tenant.
  * coverage is the table. An unrecognised place is `None` ("distance unknown"), which is
    NEVER treated as far away and never filters anyone out.

THE DISTINCTION THAT MATTERS MOST. "Stated a place we could not locate", "stated a place
X km away" and "stated no place at all" are three different facts, and the last one is
the absence of evidence. A résumé that omits a city is not a candidate who lives far
away, so `LocationContext.stated` is None for it and every filter keeps it visible.
"""

from __future__ import annotations

import functools
import math
import re
import time
import unicodedata
from pathlib import Path

from pydantic import BaseModel

PLACES_FILE = "signals/places.yaml"

# Punctuation that appears around résumé location text and must not defeat a match:
# "Gurgaon (Haryana)", "Mumbai - Andheri East", "Bangalore, India".
#
# The PERIOD is in here for a reason worth keeping: without it, "Based in Bengaluru."
# folded to " bengaluru. 6 years… " and the lookup for " bengaluru " missed, so a
# perfectly clear sentence fell through to the raw-text fallback and reported the city
# as "Bengaluru. 6 years in" with an unknown distance. A single trailing full stop was
# enough to silently disable the lens for that candidate.
_TRIM = re.compile(r"[\s.,;:/|\-–—()\[\]'\"]+")


class LocationContext(BaseModel):
    """The non-scoring location read for one candidate against one requisition.

    Frozen into the run artifact at screening time, so Review renders it without
    re-reading a single résumé — which is also what keeps the Review payload fast.
    """

    job_place: str = ""
    """The requisition's place, as the recruiter/JD stated it."""

    stated: str | None = None
    """The place the RÉSUMÉ states, canonicalised to the gazetteer name where we could
    resolve it, else the raw matched token. None = the résumé states no location, which
    is never treated as a miss."""

    distance_km: float | None = None
    """Great-circle km between job_place and stated. None = unknown (either side
    unresolvable), which is never treated as far away."""

    resolved: bool = False
    """True only when BOTH places were found in the gazetteer, i.e. distance_km is real.
    Lets the UI distinguish "42 km" from "we could not work it out"."""

    def note(self, radius_km: int | None = None, job_known: bool = True) -> str:
        """The one-line recruiter-facing sentence. States what is known and stops —
        no invented distance, and never phrased as a disqualification.

        `job_known=False` means the unresolvable side is the REQUISITION's own place,
        not this candidate's. Saying "distance unknown" on all 40 rows then blames the
        résumés for a gap in the places table and leaves the reader with nothing to
        act on — so the row states the fact it has, and the reason is said ONCE, on the
        filter that is failing (2026-09-11: a Budapest requisition read as 40 unknowns).
        """
        if not self.job_place:
            return ""
        if self.stated is None:
            return "No location stated on the résumé"
        if not job_known:
            return f"States {self.stated}"
        if self.distance_km is None:
            return f"States {self.stated} — distance from {self.job_place} unknown"
        km = round(self.distance_km)
        if km <= 1:
            return f"States {self.stated} — in {self.job_place}"
        within = ""
        if radius_km is not None:
            within = f" (within {radius_km} km)" if km <= radius_km else f" (beyond {radius_km} km)"
        return f"States {self.stated} — ≈{km} km from {self.job_place}{within}"


# --- the gazetteer ------------------------------------------------------------------
#
# TWO TABLES, MERGED, THE SHARE ON TOP. The build ships `signals/places.yaml`, and
# `dataroot` seeds a copy into DATA_ROOT on first run and NEVER overwrites it. So a
# release that adds cities would reach a fresh deployment and no other — the exact trap
# features.yaml hit in pass 6, where a hosted app kept rendering a flag under
# definitions the build had already replaced. Here it would be worse than stale copy:
# every candidate in a requisition whose city arrived with the release reads "distance
# unknown", which looks like a broken filter rather than a missing row.
#
# So the shipped table is the FLOOR and the share's entries are laid over it by name:
# cities added in a release appear without touching the share, and a correction made on
# the share (a wrong coordinate, a local spelling) survives every release.


class Places:
    """The places table, parsed once and then queried in memory.

    Constructed per file STATE, never per candidate. `resolve_place` used to `stat()`
    the table twice per lookup — fine for one résumé inside the run worker, and 666
    filesystem round trips on an SMB share now that Review re-derives the distance for
    a whole 333-candidate batch on every load.
    """

    __slots__ = ("coords", "canon", "_by_length", "_sweepable")

    def __init__(self, coords: dict[str, tuple[float, float]], canon: dict[str, str],
                 no_sweep: set[str] | None = None) -> None:
        self.coords = coords
        self.canon = canon
        # Longest first, so "Navi Mumbai" wins over "Mumbai" in the document sweep.
        self._by_length = sorted(coords, key=len, reverse=True)
        blocked = no_sweep or set()
        self._sweepable = [n for n in self._by_length if n not in blocked]

    def __bool__(self) -> bool:
        return bool(self.coords)

    def resolve(self, place: str) -> tuple[str, float, float] | None:
        """(canonical name, lat, lon), or None when the table has no entry. Tries the
        whole string first, then the leading city before any bracket or dash — so
        "Mumbai (Andheri East)" resolves via "Mumbai" and "Budapest, Hungary" via
        "Budapest"."""
        if not place or not place.strip():
            return None
        folded = _fold(place).strip()
        candidates = [folded]
        head = folded.split("(")[0].strip()
        if head and head != folded:
            candidates.append(head)
        if head:
            first = head.split()[0]
            if first != head:
                candidates.append(first)
        for cand in candidates:
            if cand in self.coords:
                lat, lon = self.coords[cand]
                return self.canon.get(cand, place.strip()), lat, lon
        return None

    def km_from(self, origin: tuple[float, float] | None, place: str | None) -> float | None:
        """km between a resolved origin and a place NAME, or None when either side is
        unknown. Takes the origin pre-resolved because the job location is the same for
        every candidate in a batch."""
        if origin is None or not place:
            return None
        hit = self.resolve(place)
        if hit is None:
            return None
        return round(haversine_km(origin, (hit[1], hit[2])), 1)

    def names_longest_first(self) -> list[str]:
        """Every name, longest first — for matching inside an EXPLICIT location line,
        where the reader has already said this is where they are."""
        return self._by_length

    def sweep_names(self) -> list[str]:
        """The names safe to look for in ordinary résumé prose. Excludes `sweep: false`
        entries — "Austin", "Phoenix", "Nice" are cities and also a name, a metaphor and
        an adjective, and reading one out of a sentence invents a home town."""
        return self._sweepable


def _read_entries(path: Path) -> list[dict]:
    """The raw `places:` rows of one file, order preserved. A broken table degrades to
    "unknown" rather than 500-ing a page."""
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    return [e for e in (raw.get("places") or []) if isinstance(e, dict)]


def _state(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def _packaged_path() -> Path:
    from resume_screener.dataroot import REPO_ROOT

    return REPO_ROOT / PLACES_FILE


@functools.lru_cache(maxsize=8)
def _build(stored_str: str, _stored_state: tuple[int, int] | None,
           _packaged_state: tuple[int, int] | None) -> Places:
    """Parse and index the merged table. Keyed on both files' state, so an edit on
    either is picked up and an unchanged pair is parsed once per process."""
    stored_path, packaged_path = Path(stored_str), _packaged_path()
    entries = _read_entries(packaged_path)
    same_file = False
    try:  # local dev: DATA_ROOT unset, so both names point at one file
        same_file = stored_path.resolve() == packaged_path.resolve()
    except OSError:
        pass
    if not same_file:
        by_name: dict[str, int] = {}
        for i, entry in enumerate(entries):
            key = _fold(str(entry.get("name") or "")).strip()
            if key:
                by_name[key] = i
        for entry in _read_entries(stored_path):
            key = _fold(str(entry.get("name") or "")).strip()
            if not key:
                continue
            if key not in by_name:
                by_name[key] = len(entries)
                entries.append(entry)
                continue
            # The share corrects the build FIELD BY FIELD, the way stored flag values
            # are read under the build's definitions. Someone fixing a coordinate on the
            # share writes `{name, lat, lon}` and would otherwise silently delete that
            # city's aliases — dropping "Gurgaon", the single likeliest cause of a false
            # "unknown" in this pilot — and its `sweep` guard with them.
            base = entries[by_name[key]]
            merged = {**base, **entry}
            merged["aliases"] = list(dict.fromkeys(
                [*(base.get("aliases") or []), *(entry.get("aliases") or [])]))
            entries[by_name[key]] = merged

    coords: dict[str, tuple[float, float]] = {}
    canon: dict[str, str] = {}
    no_sweep: set[str] = set()
    for entry in entries:
        try:
            lat, lon = float(entry["lat"]), float(entry["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        canonical = str(entry.get("name") or "").strip()
        sweepable = entry.get("sweep", True) is not False
        for name in [entry.get("name", "")] + list(entry.get("aliases") or []):
            folded = _fold(str(name)).strip()
            if not folded:
                continue
            coords.setdefault(folded, (lat, lon))
            if canonical:
                canon.setdefault(folded, canonical)
            if not sweepable:
                no_sweep.add(folded)
    return Places(coords, canon, no_sweep)


_TTL_SECONDS = 15.0
_recent: dict[str, tuple[float, Places]] = {}


def places(path: str | Path = PLACES_FILE) -> Places:
    """The merged table. Re-stats at most every 15 s — the same net the flag catalog
    uses for another process's in-place edit — so a per-candidate lookup costs nothing
    while an edit on the share still lands without a restart."""
    key = str(path)
    now = time.monotonic()
    hit = _recent.get(key)
    if hit is not None and now - hit[0] < _TTL_SECONDS:
        return hit[1]
    table = _build(key, _state(Path(key)), _state(_packaged_path()))
    _recent[key] = (now, table)
    return table


def invalidate() -> None:
    """Drop the parsed table (tests, and any writer that edits the table in process)."""
    _recent.clear()
    _build.cache_clear()


def load_places(path: str | Path = PLACES_FILE) -> dict[str, tuple[float, float]]:
    return places(path).coords


def canonical_names(path: str | Path = PLACES_FILE) -> dict[str, str]:
    """{folded alias -> the canonical `name` from the table}, so a résumé saying
    "Gurgaon" reports as "Gurugram" and two spellings never look like two places."""
    return places(path).canon


def _fold(text: str) -> str:
    """Lowercase, strip accents, collapse punctuation to single spaces. Résumés arrive
    from PDF extraction with inconsistent separators, so comparison happens on a
    normalised form rather than the raw string."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return f" {_TRIM.sub(' ', stripped.lower()).strip()} "


def resolve_place(place: str, path: str | Path = PLACES_FILE) -> tuple[str, float, float] | None:
    """(canonical name, lat, lon) for a place string, or None when the table has no
    entry. Tries the whole string first, then the leading city before any bracket or
    dash — so "Mumbai (Andheri East)" resolves via "Mumbai"."""
    return places(path).resolve(place)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km. Shorter than a road route, which is why every
    rendering of it is prefixed "≈"."""
    radius = 6371.0088
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def distance_between(place_a: str, place_b: str, path: str | Path = PLACES_FILE) -> float | None:
    """km between two place names, or None when either is not in the gazetteer."""
    ra, rb = resolve_place(place_a, path), resolve_place(place_b, path)
    if ra is None or rb is None:
        return None
    return round(haversine_km((ra[1], ra[2]), (rb[1], rb[2])), 1)


# --- reading a place out of a résumé -----------------------------------------------

# Two ways a résumé states where someone is, kept apart deliberately. Both are checked
# before the bare gazetteer sweep, so an explicit "Current location: Pune" beats a
# passing mention of a client city.
#
# A FIELD LABEL must be followed by a separator ("Location: Pune", "Address - Noida").
# Requiring it is not pedantry: without it the bare word "city" matched ordinary prose
# like "No city anywhere in this document" and captured the rest of the sentence as the
# stated place. That renders as "States <junk> — distance unknown" instead of the
# truthful "No location stated" — confidently wrong, which is the one thing this lens
# must never be.
# The labels a résumé puts in front of where someone lives. English and the Indian
# idioms ("native place", "domicile") were the whole list until 2026-09-12, so a
# Hungarian résumé's "Lakcím:" or a German one's "Wohnort:" stated a location the
# lens could not see — every candidate read "distance unknown", which is safe (an
# unknown distance never hides anyone) but turns the filter off entirely for the
# two markets going live next.
#
# The separator after the label stays MANDATORY. That is what keeps an ordinary
# word out: "city" appears in prose constantly, and an earlier version of this
# pattern invented a home town out of a sentence.
_LABEL_LINE = re.compile(
    r"(?:current\s+location|present\s+location|current\s+address|permanent\s+address"
    r"|location|address|city|hometown|native\s+place|domicile|pin\s?code"
    # Hungarian
    r"|lakcím|lakhely|tartózkodási\s+hely|lakóhely|város"
    # German
    r"|wohnort|anschrift|adresse|standort"
    # French
    r"|adresse|domicile|ville|lieu\s+de\s+résidence"
    # Spanish / Portuguese
    r"|domicilio|dirección|ciudad|residencia|morada|endereço|cidade"
    # Italian
    r"|città|residenza|indirizzo"
    # Dutch
    r"|woonplaats|adres|stad"
    # Polish
    r"|miejsce\s+zamieszkania|adres|miasto"
    # Romanian
    r"|domiciliu|adres[ăa]|oraș"
    # Czech
    r"|bydliště|adresa|město"
    r")"
    r"\s*[:\-–]\s*(?P<rest>[^\n\r|]{0,80})",
    re.IGNORECASE,
)
# PROSE states it without any separator, so these phrases must be specific enough to
# carry the meaning alone — "based in X", never a bare "city".
_PROSE_LINE = re.compile(
    r"(?:based\s+(?:in|at|out\s+of)|residing\s+(?:in|at)|resident\s+of"
    r"|currently\s+(?:located|based)(?:\s+(?:in|at))?"
    # Each of these carries the meaning on its own, the way "based in" does — a
    # bare preposition would not, which is why "in" is not on this list.
    r"|wohnhaft\s+in|ansässig\s+in"
    r"|basé[e]?\s+à|résidant\s+à"
    r"|residente\s+(?:en|a|em)"
    r"|woonachtig\s+in"
    r"|zamieszkał[ya]\s+w"
    r"|cu\s+domiciliul\s+în"
    r"|bytem\s+v"
    r")"
    r"\s+(?P<rest>[^\n\r|]{0,80})",
    re.IGNORECASE,
)


def _location_phrases(text: str):
    """Every explicit location statement in the text — labels first, then prose."""
    for pattern in (_LABEL_LINE, _PROSE_LINE):
        for match in pattern.finditer(text):
            yield match.group("rest")


def stated_place(resume_text: str, path: str | Path = PLACES_FILE) -> str | None:
    """The place the résumé states, canonicalised where the gazetteer knows it.

    None means NO location signal at all — the absence of evidence, which must never be
    read as "far away". A location line naming somewhere unknown returns that raw text,
    so the recruiter sees what the résumé actually said.
    """
    if not resume_text or not resume_text.strip():
        return None
    table = places(path)
    canon, by_length = table.canon, table.names_longest_first()

    head = resume_text[:6000]
    # 1) An explicit statement wins over a place mentioned in passing. Longest names
    #    first here too, or "Current location: Navi Mumbai" reports as Mumbai.
    for rest in _location_phrases(head):
        folded_rest = _fold(rest)
        for folded in by_length:
            if f" {folded} " in folded_rest:
                return canon.get(folded, folded.strip().title())
    # 2) Otherwise the first gazetteer place named anywhere in the document. Longest
    #    names first so "Navi Mumbai" wins over "Mumbai", and only names that carry
    #    their meaning alone (see Places.sweep_names).
    haystack = _fold(resume_text)
    for folded in table.sweep_names():
        if f" {folded} " in haystack:
            return canon.get(folded, folded.strip().title())
    # 3) An explicit statement naming somewhere the gazetteer does not know — report it
    #    verbatim, so the recruiter sees what the résumé actually said instead of a
    #    misleading "no location stated".
    for rest in _location_phrases(head):
        cleaned = rest.strip(" :-–\t")
        if cleaned:
            return cleaned[:40]
    return None


def read_context(resume_text: str, job_place: str,
                 path: str | Path = PLACES_FILE) -> LocationContext:
    """The whole non-scoring read for one candidate. Cheap and pure: text in, context
    out, no network and no model call."""
    if not job_place or not job_place.strip():
        return LocationContext()
    stated = stated_place(resume_text, path)
    if stated is None:
        return LocationContext(job_place=job_place)
    km = distance_between(job_place, stated, path)
    return LocationContext(
        job_place=job_place, stated=stated, distance_km=km, resolved=km is not None,
    )
