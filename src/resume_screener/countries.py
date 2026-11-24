"""Country -> hiring-rules pack: the one table that decides which law applies.

WHY THIS EXISTS (2026-09-12, the global rollout). A requisition's `job_location`
IS its policy-pack code, and until now the only thing that turned a place into a
code lived inside careers_lookup.py as two half-overlapping tables. Hungary was in
neither list, so a Budapest requisition silently became an India one: the India
pack's retention (1095/400 days), and `language_evidence` scoring at cap 3 where
the EU pack disables it precisely because a language requirement can proxy
nationality there. Nobody was told. That is the failure this module closes.

WHY CODE AND NOT A DATA FILE. Every other governed artifact (packs, the scoring
model, places.yaml) lives on the DATA_ROOT share so HR can change it without a
release. This one must not:

* It names the packs it maps to. A row pointing at a pack that does not exist is
  a requisition nobody can screen, so the table and `policies/` have to ship and
  be tested together (tests/test_countries.py pins exactly that).
* signals/ is seeded into DATA_ROOT and NEVER overwritten. A share copy would
  freeze at whatever the first deploy shipped — the trap features.yaml hit in
  pass 6 and places.yaml hit again in the 2026-09-11 pass. A country gaining a
  pack is a release, not a hosted edit.
* It carries a legal consequence, not a preference. Choosing the wrong row is
  choosing the wrong law; that belongs in code review with a test beside it.

HR still READS it — `GET /api/locations` serves the whole table, and the New
requisition form is built from it — so nobody needs to open this file to know
what the app will do.

NO SILENT DEFAULTS. A country we have no pack for returns `pack=None`, which the
API turns into a plain refusal naming the country. That is the same principle
policy._resolve_pack_path already holds ("other unknown locations are an error,
never a silent default") moved forward to where the recruiter can act on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- the shapes -----------------------------------------------------------------


@dataclass(frozen=True)
class Country:
    """One country and the pack that governs hiring there.

    `pack=None` means "we have no hiring rules for this country yet" — a real,
    speakable state, not a gap to paper over with a default.
    """

    alpha2: str
    alpha3: str
    name: str
    pack: str | None
    aliases: tuple[str, ...] = ()
    tz: str | None = None  # the country's single IANA zone; None when it has several


@dataclass(frozen=True)
class Resolution:
    """What a location string or a (country, state) pair resolved to."""

    country: Country | None = None
    us_state: str | None = None
    pack: str | None = None
    reason: str = ""
    #: True when the text named a country we know but have no pack for — the
    #: caller says "no rules for Canada yet", not "I didn't understand you".
    known_without_pack: bool = field(default=False)


# --- the table ------------------------------------------------------------------

# EU/EEA members map to the EU overlay (AI Act + GDPR). The 27 member states are
# listed individually rather than by a region flag so adding a national overlay
# later (a Hungary pack, say) is one row's edit and nothing else's.
_EU = "EU"

COUNTRIES: tuple[Country, ...] = (
    # --- countries with a pack ---------------------------------------------------
    Country("IN", "IND", "India", "IN", ("Vedhika",), "Asia/Kolkata"),
    Country("SG", "SGP", "Singapore", "SG", (), "Asia/Singapore"),
    Country("JP", "JPN", "Japan", "JP", ("Nippon", "Nihon"), "Asia/Tokyo"),
    # The US resolves through US_STATES below; "US-default" is the federal baseline.
    Country("US", "USA", "United States", "US-default",
            ("United States of America", "U.S.", "U.S.A.", "USA", "America"), None),
    # --- the EU / EEA ------------------------------------------------------------
    Country("AT", "AUT", "Austria", _EU, ("Österreich", "Osterreich"), "Europe/Vienna"),
    Country("BE", "BEL", "Belgium", _EU, ("België", "Belgique"), "Europe/Brussels"),
    Country("BG", "BGR", "Bulgaria", _EU, ("България",), "Europe/Sofia"),
    Country("HR", "HRV", "Croatia", _EU, ("Hrvatska",), "Europe/Zagreb"),
    Country("CY", "CYP", "Cyprus", _EU, (), "Asia/Nicosia"),
    Country("CZ", "CZE", "Czechia", _EU, ("Czech Republic", "Česko"), "Europe/Prague"),
    Country("DK", "DNK", "Denmark", _EU, ("Danmark",), "Europe/Copenhagen"),
    Country("EE", "EST", "Estonia", _EU, ("Eesti",), "Europe/Tallinn"),
    Country("FI", "FIN", "Finland", _EU, ("Suomi",), "Europe/Helsinki"),
    Country("FR", "FRA", "France", _EU, (), "Europe/Paris"),
    Country("DE", "DEU", "Germany", _EU, ("Deutschland",), "Europe/Berlin"),
    Country("GR", "GRC", "Greece", _EU, ("Hellas", "Ελλάδα"), "Europe/Athens"),
    Country("HU", "HUN", "Hungary", _EU, ("Magyarország", "Magyarorszag"),
            "Europe/Budapest"),
    Country("IE", "IRL", "Ireland", _EU, ("Éire", "Republic of Ireland"),
            "Europe/Dublin"),
    Country("IT", "ITA", "Italy", _EU, ("Italia",), "Europe/Rome"),
    Country("LV", "LVA", "Latvia", _EU, ("Latvija",), "Europe/Riga"),
    Country("LT", "LTU", "Lithuania", _EU, ("Lietuva",), "Europe/Vilnius"),
    Country("LU", "LUX", "Luxembourg", _EU, ("Lëtzebuerg",), "Europe/Luxembourg"),
    Country("MT", "MLT", "Malta", _EU, (), "Europe/Malta"),
    Country("NL", "NLD", "Netherlands", _EU,
            ("The Netherlands", "Holland", "Nederland"), "Europe/Amsterdam"),
    Country("PL", "POL", "Poland", _EU, ("Polska",), "Europe/Warsaw"),
    Country("PT", "PRT", "Portugal", _EU, (), "Europe/Lisbon"),
    Country("RO", "ROU", "Romania", _EU, ("România", "Romania"), "Europe/Bucharest"),
    Country("SK", "SVK", "Slovakia", _EU, ("Slovensko",), "Europe/Bratislava"),
    Country("SI", "SVN", "Slovenia", _EU, ("Slovenija",), "Europe/Ljubljana"),
    Country("ES", "ESP", "Spain", _EU, ("España", "Espana"), "Europe/Madrid"),
    Country("SE", "SWE", "Sweden", _EU, ("Sverige",), "Europe/Stockholm"),
    # --- known, no pack yet ------------------------------------------------------
    # Listed on purpose: the form can name them and refuse with a reason, instead
    # of the recruiter guessing which of the seven packs is least wrong.
    Country("AE", "ARE", "United Arab Emirates", None, ("UAE", "Emirates"), "Asia/Dubai"),
    Country("AR", "ARG", "Argentina", None, (), "America/Argentina/Buenos_Aires"),
    Country("AU", "AUS", "Australia", None, (), None),
    Country("BR", "BRA", "Brazil", None, ("Brasil",), None),
    Country("CA", "CAN", "Canada", None, (), None),
    Country("CH", "CHE", "Switzerland", None, ("Schweiz", "Suisse", "Svizzera"),
            "Europe/Zurich"),
    Country("CN", "CHN", "China", None, ("PRC", "People's Republic of China"),
            "Asia/Shanghai"),
    Country("GB", "GBR", "United Kingdom", None,
            ("UK", "Great Britain", "Britain", "England", "Scotland", "Wales"),
            "Europe/London"),
    Country("HK", "HKG", "Hong Kong", None, ("Hong Kong SAR",), "Asia/Hong_Kong"),
    Country("ID", "IDN", "Indonesia", None, (), None),
    Country("IL", "ISR", "Israel", None, (), "Asia/Jerusalem"),
    Country("KR", "KOR", "South Korea", None, ("Korea", "Republic of Korea"),
            "Asia/Seoul"),
    Country("MX", "MEX", "Mexico", None, ("México",), None),
    Country("MY", "MYS", "Malaysia", None, (), "Asia/Kuala_Lumpur"),
    Country("NO", "NOR", "Norway", None, ("Norge",), "Europe/Oslo"),
    Country("NZ", "NZL", "New Zealand", None, (), "Pacific/Auckland"),
    Country("PH", "PHL", "Philippines", None, (), "Asia/Manila"),
    Country("SA", "SAU", "Saudi Arabia", None, (), "Asia/Riyadh"),
    Country("TH", "THA", "Thailand", None, (), "Asia/Bangkok"),
    Country("TR", "TUR", "Türkiye", None, ("Turkey", "Turkiye"), "Europe/Istanbul"),
    Country("TW", "TWN", "Taiwan", None, (), "Asia/Taipei"),
    Country("VN", "VNM", "Vietnam", None, ("Viet Nam",), "Asia/Ho_Chi_Minh"),
    Country("ZA", "ZAF", "South Africa", None, (), "Africa/Johannesburg"),
)

# US state/territory -> (name, pack, IANA zone). Only California has its own pack
# today; every other state takes the federal baseline. New York is US-default too:
# Local Law 144 is a CITY law, so US-NYC is reached by the "in New York City"
# answer on the form (see NYC_TOKENS), never by the state alone.
US_STATES: dict[str, tuple[str, str, str]] = {
    "AL": ("Alabama", "US-default", "America/Chicago"),
    "AK": ("Alaska", "US-default", "America/Anchorage"),
    "AZ": ("Arizona", "US-default", "America/Phoenix"),
    "AR": ("Arkansas", "US-default", "America/Chicago"),
    "CA": ("California", "US-CA", "America/Los_Angeles"),
    "CO": ("Colorado", "US-default", "America/Denver"),
    "CT": ("Connecticut", "US-default", "America/New_York"),
    "DE": ("Delaware", "US-default", "America/New_York"),
    "DC": ("District of Columbia", "US-default", "America/New_York"),
    "FL": ("Florida", "US-default", "America/New_York"),
    "GA": ("Georgia", "US-default", "America/New_York"),
    "HI": ("Hawaii", "US-default", "Pacific/Honolulu"),
    "ID": ("Idaho", "US-default", "America/Boise"),
    "IL": ("Illinois", "US-default", "America/Chicago"),
    "IN": ("Indiana", "US-default", "America/Indiana/Indianapolis"),
    "IA": ("Iowa", "US-default", "America/Chicago"),
    "KS": ("Kansas", "US-default", "America/Chicago"),
    "KY": ("Kentucky", "US-default", "America/New_York"),
    "LA": ("Louisiana", "US-default", "America/Chicago"),
    "ME": ("Maine", "US-default", "America/New_York"),
    "MD": ("Maryland", "US-default", "America/New_York"),
    "MA": ("Massachusetts", "US-default", "America/New_York"),
    "MI": ("Michigan", "US-default", "America/Detroit"),
    "MN": ("Minnesota", "US-default", "America/Chicago"),
    "MS": ("Mississippi", "US-default", "America/Chicago"),
    "MO": ("Missouri", "US-default", "America/Chicago"),
    "MT": ("Montana", "US-default", "America/Denver"),
    "NE": ("Nebraska", "US-default", "America/Chicago"),
    "NV": ("Nevada", "US-default", "America/Los_Angeles"),
    "NH": ("New Hampshire", "US-default", "America/New_York"),
    "NJ": ("New Jersey", "US-default", "America/New_York"),
    "NM": ("New Mexico", "US-default", "America/Denver"),
    "NY": ("New York", "US-default", "America/New_York"),
    "NC": ("North Carolina", "US-default", "America/New_York"),
    "ND": ("North Dakota", "US-default", "America/Chicago"),
    "OH": ("Ohio", "US-default", "America/New_York"),
    "OK": ("Oklahoma", "US-default", "America/Chicago"),
    "OR": ("Oregon", "US-default", "America/Los_Angeles"),
    "PA": ("Pennsylvania", "US-default", "America/New_York"),
    "PR": ("Puerto Rico", "US-default", "America/Puerto_Rico"),
    "RI": ("Rhode Island", "US-default", "America/New_York"),
    "SC": ("South Carolina", "US-default", "America/New_York"),
    "SD": ("South Dakota", "US-default", "America/Chicago"),
    "TN": ("Tennessee", "US-default", "America/Chicago"),
    "TX": ("Texas", "US-default", "America/Chicago"),
    "UT": ("Utah", "US-default", "America/Denver"),
    "VT": ("Vermont", "US-default", "America/New_York"),
    "VA": ("Virginia", "US-default", "America/New_York"),
    "WA": ("Washington", "US-default", "America/Los_Angeles"),
    "WV": ("West Virginia", "US-default", "America/New_York"),
    "WI": ("Wisconsin", "US-default", "America/Chicago"),
    "WY": ("Wyoming", "US-default", "America/Denver"),
}

#: New York City by any of its usual names — Local Law 144 is a city ordinance, so
#: this is what turns a New York State requisition into a US-NYC one.
NYC_TOKENS = frozenset({
    "new york city", "nyc", "new york", "manhattan", "brooklyn", "queens",
    "the bronx", "bronx", "staten island",
})
NYC_PACK = "US-NYC"
NYC_STATE = "NY"

# --- lookups --------------------------------------------------------------------

_BY_ALPHA2 = {c.alpha2: c for c in COUNTRIES}
_BY_ALPHA3 = {c.alpha3: c for c in COUNTRIES}


def _fold(text: str) -> str:
    """Casefold + collapse punctuation/space so "U.S.A." and "usa" compare equal."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").strip().casefold())


_BY_NAME: dict[str, Country] = {}
for _c in COUNTRIES:
    for _label in (_c.name, *_c.aliases):
        _BY_NAME.setdefault(_fold(_label), _c)

_STATE_BY_NAME = {_fold(name): code for code, (name, _p, _tz) in US_STATES.items()}


def lookup(text: str | None) -> Country | None:
    """A country from an ISO alpha-2/alpha-3 code, an English name or an alias.

    Deliberately strict about codes: only an EXACT two- or three-letter match
    counts, so "CA" is Canada (the ISO code) and never California. The US state
    is a separate question with its own argument — conflating the two is how
    "Toronto, CA" used to be screened under California's law.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    upper = raw.upper()
    if len(upper) == 2 and upper in _BY_ALPHA2:
        return _BY_ALPHA2[upper]
    if len(upper) == 3 and upper in _BY_ALPHA3:
        return _BY_ALPHA3[upper]
    return _BY_NAME.get(_fold(raw))


def state_code(text: str | None) -> str | None:
    """A USPS state code from the code itself or the state's name."""
    raw = (text or "").strip()
    if not raw:
        return None
    upper = raw.upper()
    if upper in US_STATES:
        return upper
    return _STATE_BY_NAME.get(_fold(raw))


def is_nyc(city: str | None) -> bool:
    return bool(city) and (city or "").strip().casefold() in NYC_TOKENS


def resolve(country: str | None, us_state: str | None = None,
            city: str | None = None, nyc: bool | None = None) -> Resolution:
    """(country, state, city) -> the governing pack.

    `nyc` is the form's own answer to "is this job in New York City?"; `city` is a
    free-text fallback for callers that only have a location string. Either one
    turns a New York State requisition into US-NYC.
    """
    found = lookup(country)
    if found is None:
        return Resolution(reason="no country")
    if found.alpha2 != "US":
        if found.pack is None:
            return Resolution(country=found, reason=f"no pack for {found.name}",
                              known_without_pack=True)
        return Resolution(country=found, pack=found.pack, reason=found.name)

    state = state_code(us_state)
    in_nyc = bool(nyc) if nyc is not None else is_nyc(city)
    if state == NYC_STATE and in_nyc:
        return Resolution(country=found, us_state=state, pack=NYC_PACK,
                          reason="New York City")
    if state is None:
        # A US job with no state still has the federal baseline — better than
        # refusing a requisition whose law we do know something about.
        return Resolution(country=found, pack=found.pack, reason="United States")
    name, pack, _tz = US_STATES[state]
    return Resolution(country=found, us_state=state, pack=pack, reason=name)


def parse_location_string(loc: str | None) -> Resolution:
    """"Budapest, HU" / "Concord, CA, US" / "Gurgaon, India" -> a Resolution.

    The TRAILING component is read as the country first, which is the careers
    site's own contract (its addressCountry is an ISO code). That ordering is the
    whole fix: "Toronto, CA" is Canada, and California is reached only when the
    string also says US — or spells "California" out.
    """
    if not loc or not loc.strip():
        return Resolution(reason="empty")
    parts = [p.strip() for p in loc.split(",") if p.strip()]

    country = lookup(parts[-1]) if parts else None
    rest = parts[:-1] if country else parts
    if country is None:
        # No country component: a spelled-out country anywhere in the string still
        # counts ("Gurgaon, India" has it last, "India - Gurgaon" does not).
        for part in parts:
            country = lookup(part)
            if country is not None:
                rest = [p for p in parts if p is not part]
                break
    if country is None:
        return Resolution(reason="no country recognised")

    if country.alpha2 != "US":
        return resolve(country.alpha2)

    state = next((s for s in (state_code(p) for p in reversed(rest)) if s), None)
    city = rest[0] if rest else None
    return resolve("US", state, city=city)


def pack_codes() -> set[str]:
    """Every pack code this table can produce — what the location picker offers."""
    codes = {c.pack for c in COUNTRIES if c.pack}
    codes.update(pack for _n, pack, _tz in US_STATES.values())
    codes.add(NYC_PACK)
    return codes


def tz_for(country: str | None, us_state: str | None = None) -> str | None:
    """The requisition-local zone for a country (US: through its state).

    None when we cannot say — the caller falls back to the pack's zone, which is
    what the app did before countries existed.
    """
    found = lookup(country)
    if found is None:
        return None
    if found.alpha2 == "US":
        state = state_code(us_state)
        return US_STATES[state][2] if state else None
    return found.tz


def name_of(alpha2: str | None) -> str:
    """The display name for a stored country code ("HU" -> "Hungary")."""
    found = lookup(alpha2)
    return found.name if found else (alpha2 or "")


def state_name(code: str | None) -> str:
    found = state_code(code)
    return US_STATES[found][0] if found else (code or "")


def catalog() -> list[dict]:
    """The whole table as the API serves it (alphabetical by name)."""
    return [
        {"code": c.alpha2, "name": c.name, "pack": c.pack, "aliases": list(c.aliases)}
        for c in sorted(COUNTRIES, key=lambda c: c.name)
    ]


def state_catalog() -> list[dict]:
    return [
        {"code": code, "name": name, "pack": pack}
        for code, (name, pack, _tz) in sorted(US_STATES.items(), key=lambda kv: kv[1][0])
    ]
