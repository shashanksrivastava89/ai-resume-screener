"""Careers-portal lookup — TEMPORARY iCIMS bridge (built to be removed).

Until the native iCIMS adapter (§12) ships, this resolves a requisition's title,
job location and job-description text from the PUBLIC Acme careers site, keyed
by its iCIMS Req ID. The whole bridge is four things:

    1. this module,
    2. the GET /api/careers/lookup route (api/app.py),
    3. the `careers_autofill` env flag (signals/features.yaml / FEATURE_CAREERS_AUTOFILL),
    4. the onBlur auto-fill handler in web/src/pages/NewRequisition.tsx.

Delete those four and the feature is gone with zero impact on anything else.

Guardrails
----------
- Best-effort and NON-FATAL: every failure maps to ``status="error"`` with a plain
  note and the recruiter falls back to typing the details. ``lookup()`` never raises.
- The careers site is server-rendered; the authoritative data is the schema.org
  ``JobPosting`` embedded as ``<script type="application/ld+json">`` on each posting
  page (title, location, description). We prefer that over scraping CSS classes so
  a template tweak on the site doesn't silently break us. NOTE: the JobPosting
  ``identifier`` on the live site is an INTERNAL hash, not the iCIMS Req ID — but
  the Req ID is displayed as visible text on the posting page, so verification
  checks the page for it. If a first-party JSON API is discovered later, swap the
  internals of ``_search`` / ``_fetch_posting`` — the ``lookup()`` contract stays
  the same.
- Fetched JD text is DATA the recruiter reviews and edits before saving, exactly
  like a pasted JD (guardrail §6.1). It never becomes instructions.
- No new dependency: ``httpx`` ships with the ``openai`` SDK; parsing uses the
  stdlib ``html.parser``.
"""

from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from html import unescape
from urllib.parse import quote, urljoin, urlsplit

from resume_screener import countries

DEFAULT_BASE = "https://careers.example.com"
TIMEOUT_S = 10.0
_MAX_POSTINGS = 8  # cap how many candidate postings we open per lookup

# Cookie-consent handling for a SERVER-SIDE fetch: there is no popup to click (no
# browser, no JS), but some consent frameworks serve a consent wall instead of the
# real page unless "consent given" cookies are present. So we send the common
# accepted-consent cookies up front — the HTTP equivalent of dismissing the banner —
# and browse with a real browser User-Agent (some sites serve bots a consent wall).
# Unknown cookies are simply ignored by the server; any Set-Cookie during a redirect
# is carried within the request's client.
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/203.0.113.17 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # Look like a real top-level navigation — some bot walls answer 202 to
    # requests missing these (cheap to send, no downside if ignored).
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
_CONSENT_COOKIES = {
    "OptanonAlertBoxClosed": "2024-01-01T00:00:00.000Z",  # OneTrust — banner dismissed
    "OptanonConsent": "isGpcEnabled=0&datestamp=Mon+Jan+01+2024&consentId=1&groups=C0001:1,C0002:1,C0003:1,C0004:1",
    "cookieconsent_status": "dismiss",                     # cookieconsent.js
    "CookieConsent": "true",                               # Cookiebot
}

# schema.org JobPosting sections that are NOT job-specific — dropped from the JD.
_BOILERPLATE_HEADINGS = (
    "who we are", "who are we", "about acme", "about us", "about the company",
    "our story", "benefits", "total rewards", "what we offer", "perks",
    "eeo", "equal opportunity", "equal employment", "agency non-solicitation",
    "agency", "diversity", "accommodation",
)

# Careers location string -> jurisdiction code lives in countries.py now (the
# global rollout, 2026-09-12). This module used to carry two half-overlapping
# tables of its own; Hungary was in neither, and there was no ISO row for Canada,
# so "Toronto, CA" matched a US state rule and got California's law. One table,
# tested against policies/, ends both.

# Posting URLs come in TWO forms on the live site: /jobs/<slug>-<uuid> (card-style
# results) and plain /jobs/<slug> with no uuid (the table results view — Title |
# Requisition Identifier | Department | ... — links its title cell this way).
_POSTING_HREF_RE = re.compile(
    r"/jobs/[a-z0-9\-]+-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_POSTING_PATH_RE = re.compile(r"^/jobs/([a-z0-9\-]+)/?$", re.I)
_NAV_SLUGS = {"search", "near-me", "all", "alerts"}  # /jobs/<x> pages that are not postings


def base_url() -> str:
    """The careers site root; overridable via CAREERS_PORTAL_BASE_URL (also lets
    tests point at a local fixture server)."""
    return (os.environ.get("CAREERS_PORTAL_BASE_URL") or DEFAULT_BASE).rstrip("/")


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


# The careers site sits behind a bot/rate wall that INTERMITTENTLY answers
# HTTP 202 (empty body, sometimes a tiny "verify that you're not a robot" shell)
# instead of content — typically after a burst of requests, and more often from a
# datacenter IP (e.g. the hosted app) than a laptop. It's transient, so we RETRY a
# couple of times on the SAME client (carrying any challenge cookie) with a short
# backoff before giving up; a persistent challenge still fails honestly and the
# recruiter enters the details (or uploads/pastes the JD). We never fill the form
# with a challenge page's text.
_BLOCKED_MARKERS = ("verify that you're not a robot",)
# Transient statuses worth a retry: 202 (bot/rate challenge), 429 (rate limit),
# 503 (temporarily unavailable). Everything else (404, 403, …) is definitive.
_RETRY_STATUSES = frozenset({202, 429, 503})
_GET_ATTEMPTS = 3          # 1 try + 2 retries — bounded so a blur stays responsive
_GET_BACKOFF_S = 1.0       # base backoff, scaled by attempt (1s, 2s)


def _get(url: str) -> str | None:
    """Fetch a page as text; None on a definitive error or a persistent
    bot/rate challenge (non-fatal). Sends accepted-consent cookies + a browser
    User-Agent so a consent-gated page serves real content, and retries a
    transient 202/429/503 on the same client with a short backoff. A 200 whose
    body is the JS 'verify you're not a robot' shell is NOT retried — a
    server-side fetch can't run the JS, so retrying can't help."""
    import time

    import httpx

    try:
        with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True,
                          headers=_BROWSER_HEADERS, cookies=dict(_CONSENT_COOKIES)) as client:
            for attempt in range(_GET_ATTEMPTS):
                try:
                    resp = client.get(url)
                except httpx.HTTPError:
                    resp = None  # connection reset / timeout — treat as transient
                if resp is not None and resp.status_code == 200:
                    low = resp.text.casefold()
                    if any(marker in low for marker in _BLOCKED_MARKERS):
                        return None
                    return resp.text
                transient = resp is None or resp.status_code in _RETRY_STATUSES
                if transient and attempt + 1 < _GET_ATTEMPTS:
                    time.sleep(_GET_BACKOFF_S * (attempt + 1))
                    continue
                return None
        return None
    except Exception:  # noqa: BLE001 — network/parse issues degrade to manual entry
        return None


# --- HTML -> text -------------------------------------------------------------

_BLOCK_TAGS = {"p", "br", "li", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "ul", "ol", "section", "header"}


class _TextExtractor(HTMLParser):
    """Collapse HTML to plain text, inserting newlines at block boundaries so
    heading lines survive for section filtering."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0  # depth inside <script>/<style>

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 — malformed markup still yields best-effort text
        pass
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in parser.text().splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):  # keep single blank separators, drop runs
            out.append(line)
    return "\n".join(out).strip()


_HEADING_PREFIX_RE = re.compile(r"^([^:.;]{1,64}):\s+\S")  # "Heading: content..." on one line


def _heading_text(line: str) -> str | None:
    """The heading a line carries, or None. Two forms: a standalone heading line
    (short, no sentence punctuation; a trailing colon is fine), or an inline
    "Heading: content..." line — bold headings inside a paragraph collapse to
    this when the posting's HTML description is flattened to text."""
    if not line:
        return None
    bare = line.rstrip(":").rstrip()
    if bare and len(bare) <= 64 and not bare.endswith((".", ",", ";")):
        return bare
    m = _HEADING_PREFIX_RE.match(line)
    return m.group(1).strip() if m else None


def _drop_boilerplate_sections(text: str) -> str:
    """Remove marketing/EEO/benefits sections by heading. Conservative: only a
    heading matching a boilerplate phrase starts a skip; the next non-boilerplate
    heading ends it. When nothing matches, the text is unchanged."""
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        head = _heading_text(line)
        if head is not None:
            low = head.casefold()
            if any(h in low for h in _BOILERPLATE_HEADINGS):
                skipping = True
                continue
            skipping = False
        if not skipping:
            kept.append(line)
    cleaned = "\n".join(kept).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


# --- schema.org JobPosting ----------------------------------------------------

_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


def _iter_json_objects(node: object):
    """Walk parsed ld+json (object, @graph list, or bare list) yielding dicts."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_json_objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_json_objects(item)


def _find_job_posting(html: str) -> dict | None:
    for match in _LD_JSON_RE.finditer(html or ""):
        try:
            data = json.loads(unescape(match.group(1)).strip())
        except (ValueError, TypeError):
            continue
        for obj in _iter_json_objects(data):
            types = obj.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(str(t).lower() == "jobposting" for t in types):
                return obj
    return None


def _identifier_of(posting: dict) -> str | None:
    ident = posting.get("identifier")
    if isinstance(ident, dict):
        return str(ident.get("value") or "").strip() or None
    if isinstance(ident, (str, int)):
        return str(ident).strip() or None
    return None


def _location_str(posting: dict) -> str | None:
    loc = posting.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address")
    if isinstance(addr, dict):
        country = addr.get("addressCountry")
        if isinstance(country, dict):  # schema.org Country object
            country = country.get("name")
        parts = [addr.get("addressLocality"), addr.get("addressRegion"), country]
        parts = [str(p) for p in parts if p]
        return ", ".join(parts) or None
    if isinstance(addr, str):
        return addr
    return None


def _fetch_posting(url: str, req_id: str = "") -> dict | None:
    """Parse one posting page into {title, identifier, location, jd_text, url,
    req_id_present}, or None. A schema.org JobPosting is REQUIRED — a page
    without one is not a real posting (e.g. an error shell), and auto-filling
    the form with junk is worse than asking for manual entry. ``req_id_present``
    records whether the Req ID is displayed anywhere on the page — the live
    site's JobPosting ``identifier`` is an internal hash, so this visible
    marker is what actually verifies the posting."""
    html = _get(url)
    if html is None:
        return None
    posting = _find_job_posting(html)
    if posting is None:
        return None
    description = posting.get("description") or ""
    return {
        "title": (posting.get("title") or "").strip() or None,
        "identifier": _identifier_of(posting),
        "location": _location_str(posting),
        "jd_text": _drop_boilerplate_sections(_html_to_text(description)),
        "url": url,
        "req_id_present": bool(req_id) and _norm(req_id) in _norm(html),
    }


def _posting_hrefs_in(html: str) -> list[str]:
    """Every href that plausibly points at a job posting (both URL forms), in
    document order, absolute, deduped. Same-site links only; /jobs/search and
    other nav pages excluded."""
    base_host = urlsplit(base_url()).netloc
    seen: list[str] = []
    for match in re.finditer(r'href="([^"]+)"', html or ""):
        href = unescape(match.group(1))
        parts = urlsplit(href)
        if parts.netloc and parts.netloc != base_host:
            continue
        path_m = _POSTING_PATH_RE.match(parts.path)
        if not path_m or path_m.group(1).lower() in _NAV_SLUGS:
            continue
        absolute = urljoin(base_url() + "/", parts.path)
        if absolute not in seen:
            seen.append(absolute)
    return seen


def _row_scoped_hrefs(html: str, req_id: str) -> list[str]:
    """Posting hrefs inside table rows that display the req id. The table results
    view pairs each title link with a 'Requisition Identifier' cell, so a row that
    shows the req id IS the match — the strongest signal the search page offers."""
    target = _norm(req_id)
    out: list[str] = []
    for row in re.finditer(r"<tr[^>]*>(.*?)</tr>", html or "", re.I | re.S):
        chunk = row.group(1)
        if target not in _norm(chunk):
            continue
        out.extend(h for h in _posting_hrefs_in(chunk) if h not in out)
    return out


def _search(req_id: str) -> list[str] | None:
    """Posting URLs listed on the careers search page for this req id (dedup, in
    order). None when the search page itself couldn't be fetched (site down or
    bot-challenged) — distinct from an empty list, which means "searched fine,
    nothing matched". Prefers links row-paired with the req id (table view);
    otherwise falls back to every posting-shaped link on the page — downstream,
    lookup() only accepts postings whose page displays the req id."""
    url = f"{base_url()}/jobs/search?page=1&query={quote(req_id)}"
    html = _get(url)
    if html is None:
        return None
    urls = _row_scoped_hrefs(html, req_id)
    if urls:
        return urls
    urls = _posting_hrefs_in(html)
    # uuid-form links can also appear outside href attributes (script blobs)
    for match in _POSTING_HREF_RE.finditer(html):
        absolute = urljoin(base_url() + "/", match.group(0))
        if absolute not in urls:
            urls.append(absolute)
    return urls


def _map_location(loc_str: str | None, known: set[str] | None) -> str | None:
    """Careers location string -> jurisdiction code, or None to leave manual.
    Only returns a code the picker offers (when `known` is provided)."""
    code = countries.parse_location_string(loc_str).pack
    if code is None:
        return None
    return code if (known is None or code in known) else None


# Successful lookups are kept for the life of the process: repeat lookups of the
# same req id (retyping, editing, demos) answer instantly and add zero requests —
# which also keeps us under the careers site's bot-protection burst threshold.
_POSTING_CACHE: dict[str, dict] = {}


def _work_place(loc_str: str | None) -> str | None:
    """The CITY from the careers posting's location, canonicalised through the
    gazetteer — "Gurgaon, Haryana, India" -> "Gurugram".

    Two different things come out of one posting field: `job_location` is a
    JURISDICTION code that resolves the policy pack, and this is the PLACE the desk
    is in. The place is what the Location column shows and what the non-scoring
    distance lens measures from; the posting is the only place it exists, because
    `_drop_boilerplate_sections` strips the location out of the JD body (a real
    requisition's stored JD says only "Legal Entity: … (India) Pvt Ltd").

    Canonicalising here rather than at read time means one spelling is stored, so
    Gurgaon and Gurugram can never look like two different sites."""
    if not loc_str:
        return None
    from resume_screener.location_context import resolve_place

    resolved = resolve_place(loc_str)
    if resolved is not None:
        return resolved[0]
    # Unknown to the gazetteer: keep the locality the posting stated (the part before
    # the first comma) rather than nothing — a recruiter reading "Kochi" is better
    # served than one reading the country, and the distance lens simply stays off.
    head = loc_str.split(",")[0].strip()
    return head or None


def _ok_response(posting: dict, known_locations: set[str] | None) -> dict:
    loc_str = posting.get("location")
    # The posting names a PLACE; the form asks for a country and derives the pack
    # from it, so hand back both halves of the same answer rather than only the
    # code (2026-09-12) — otherwise a filled-in form shows the right rules over an
    # empty country and refuses to continue.
    res = countries.parse_location_string(loc_str)
    pack = _map_location(loc_str, known_locations)
    return {
        "status": "ok",
        "title": posting.get("title"),
        "jd_text": posting.get("jd_text") or "",
        "job_location": pack,
        "location_label": loc_str,
        "country": res.country.alpha2 if (res.country and pack) else None,
        "us_state": res.us_state if pack else None,
        "work_place": _work_place(loc_str),
        "source_url": posting.get("url"),
    }


def lookup(req_id: str, known_locations: set[str] | None = None) -> dict:
    """Resolve a requisition from the careers site by iCIMS Req ID.

    Returns ``{status, ...}`` where status is:
      - ``ok``        -> title, jd_text, job_location?, location_label?, source_url
      - ``not_found`` -> searched fine, no posting matched the req id
      - ``ambiguous`` -> more than one posting matched (count) -> fill manually
      - ``error``     -> the site was unreachable or blocked the request -> fill manually
    Never raises.
    """
    req_id = (req_id or "").strip()
    if not req_id:
        return {"status": "error", "note": "Enter an iCIMS Req ID first."}
    target = _norm(req_id)
    cached = _POSTING_CACHE.get(target)
    if cached is not None:
        return _ok_response(cached, known_locations)
    try:
        urls = _search(req_id)
        if urls is None:
            return {"status": "error",
                    "note": "Couldn't reach the careers site just now — "
                            "enter the details below (or try again in a minute)."}
        if not urls:
            return {"status": "not_found",
                    "note": f"No posting found for {req_id} on the careers site — "
                            "enter the details below."}

        matches: list[dict] = []
        unverified: list[dict] = []  # opened postings we couldn't tie to the req id
        fetched_any = False
        for url in urls[:_MAX_POSTINGS]:
            posting = _fetch_posting(url, req_id)
            if posting is None:
                continue
            fetched_any = True
            # Verified when the Req ID is displayed on the page (the live site's
            # behavior) or the structured identifier happens to equal it.
            ident = posting.get("identifier")
            if posting.get("req_id_present") or (ident and _norm(ident) == target):
                matches.append(posting)
                if len(matches) >= 2:  # ambiguity proven — stop burning requests
                    break
            else:
                unverified.append(posting)

        # If nothing verified but the search returned exactly one posting, trust
        # that single result (best-effort).
        if not matches and len(urls) == 1 and unverified:
            matches = unverified[:1]

        if not matches:
            if not fetched_any:
                return {"status": "error",
                        "note": "The careers site couldn't be read just now — "
                                "enter the details below (or try again in a minute)."}
            return {"status": "not_found",
                    "note": f"No posting matched {req_id} on the careers site — "
                            "enter the details below."}
        if len(matches) > 1:
            return {"status": "ambiguous", "count": len(matches),
                    "note": f"{len(matches)} postings matched {req_id} — please enter "
                            "the details manually."}

        _POSTING_CACHE[target] = matches[0]
        return _ok_response(matches[0], known_locations)
    except Exception as exc:  # noqa: BLE001 — any failure is non-fatal, fall back to manual
        return {"status": "error",
                "note": f"Couldn't reach the careers site ({exc}). Enter the details below."}
