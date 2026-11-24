"""Careers-portal auto-fill (temporary iCIMS bridge).

All offline: the HTTP layer (`careers_lookup._get`) is monkeypatched with saved
fixture HTML — the live careers site is never hit. Covers the single/zero/multiple
result gates, the schema.org JobPosting parse with boilerplate stripped, the
location mapping, and the flag-gated route.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from resume_screener import careers_lookup
from tests.test_sso_identity import _as

REPO_ROOT = Path(__file__).resolve().parents[1]

UUID1 = "00000000-0000-4000-8000-dbb1ecf2e9a6"
UUID2 = "00000000-0000-4000-8000-c8cf93e3a7cd"
POST1 = f"/jobs/sr-accountant-gurgaon-india-{UUID1}"
POST2 = f"/jobs/sr-accountant-noida-india-{UUID2}"


def _posting_html(req_id: str, *, title: str = "Sr Accountant - GL Accounting",
                  locality: str = "Gurgaon", country: str = "India") -> str:
    ld = {
        "@context": "https://schema.org", "@type": "JobPosting", "title": title,
        "identifier": {"@type": "PropertyValue", "name": "Req", "value": req_id},
        "jobLocation": {"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": locality, "addressCountry": country}},
        "description": (
            "<h2>How You'll Make an Impact</h2><p>Own the monthly GL close.</p>"
            "<h2>What You Bring</h2><p>US GAAP, Blackline, SAP.</p>"
            "<h2>Who We Are</h2><p>For 70 years Acme has focused on quality.</p>"
            "<h2>Benefits</h2><p>Great perks and coverage.</p>"
            "<h2>EEO Statement</h2><p>Acme is an equal opportunity employer.</p>"
        ),
    }
    return (f'<html><head><script type="application/ld+json">'
            f'{json.dumps(ld)}</script></head><body></body></html>')


def _fake_get(pages: dict[str, str]):
    """Return a _get replacement resolving by URL substring."""
    def fake(url: str):
        for key, html in pages.items():
            if key in url:
                return html
        return None
    return fake


@pytest.fixture(autouse=True)
def _fresh_posting_cache(monkeypatch):
    """lookup() caches successful postings for the process lifetime — isolate it.
    Also zero the retry backoff so the 202-retry path never sleeps in tests."""
    monkeypatch.setattr(careers_lookup, "_POSTING_CACHE", {})
    monkeypatch.setattr(careers_lookup, "_GET_BACKOFF_S", 0)


# --- lookup() gates ---------------------------------------------------------------


def test_single_result_ok_strips_boilerplate(monkeypatch):
    monkeypatch.setattr(careers_lookup, "_get", _fake_get({
        "search": f'<a href="{POST1}">Sr Accountant</a>',
        UUID1: _posting_html("2026-38563"),
    }))
    result = careers_lookup.lookup("2026-38563", known_locations={"IN", "EU", "US-default"})
    assert result["status"] == "ok"
    assert result["title"] == "Sr Accountant - GL Accounting"
    assert result["job_location"] == "IN"
    assert result["location_label"] == "Gurgaon, India"
    jd = result["jd_text"]
    assert "How You'll Make an Impact" in jd and "What You Bring" in jd
    assert "Who We Are" not in jd and "Benefits" not in jd and "equal opportunity" not in jd


def test_live_site_shape_hash_identifier_and_iso_country(monkeypatch):
    """Regression for the real careers site: the JobPosting `identifier` is an
    INTERNAL hash (not the Req ID), the Req ID appears only as visible page text,
    and addressCountry is an ISO code ("Gurgaon, IN"). The lookup must still
    verify the posting and map the location."""
    ld = {
        "@context": "http://schema.org/", "@type": "JobPosting",
        "title": "Sr Accountant -GL Accounting",
        "identifier": {"@type": "PropertyValue", "name": "Acme Life Sciences, Inc.",
                       "value": "8e0b379dce0ea5134a2a6f468243621e"},
        "jobLocation": {"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": "Gurgaon",
            "addressCountry": "IN"}},
        "description": "<h2>What You Bring</h2><p>US GAAP, SAP.</p>",
    }
    html = (f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script>'
            f'</head><body><ul><li><span>2026-38563</span></li></ul></body></html>')
    monkeypatch.setattr(careers_lookup, "_get", _fake_get({
        "search": f'<a href="{POST1}">Sr Accountant</a>',
        UUID1: html,
    }))
    result = careers_lookup.lookup("2026-38563", known_locations={"IN", "EU", "US-default"})
    assert result["status"] == "ok"
    assert result["title"] == "Sr Accountant -GL Accounting"
    assert result["job_location"] == "IN"
    assert result["location_label"] == "Gurgaon, IN"
    assert "US GAAP" in result["jd_text"]


def test_table_view_slug_link_resolves(monkeypatch):
    """Regression for the table results view (seen live for 2026-38438): the row
    links its title as /jobs/<slug> with NO uuid, and pairs it with a
    'Requisition Identifier' cell. The lookup must follow that link."""
    ld = {
        "@context": "http://schema.org/", "@type": "JobPosting", "title": "RA Spec II",
        "identifier": {"@type": "PropertyValue", "name": "Acme Life Sciences, Inc.",
                       "value": "0123456789abcdef0123456789abcdef"},
        "jobLocation": {"@type": "Place", "address": {
            "@type": "PostalAddress", "addressLocality": "Gurgaon",
            "addressCountry": "IN"}},
        "description": "<p>Direct, organize and coordinate RA &amp; QA activities.</p>",
    }
    search = (
        "<table><thead><tr><th>Title</th><th>Requisition Identifier</th>"
        "<th>Department</th><th>Location</th></tr></thead><tbody>"
        '<tr><td><a href="https://careers.example.com/jobs/ra-spec-ii-gurgaon-india">'
        "RA Spec II</a></td><td>2026-38438</td><td>Quality &amp; Regulatory</td>"
        "<td>Gurgaon, India</td></tr></tbody></table>"
    )
    posting = (f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script>'
               f"</head><body><h1>RA Spec II</h1><span>2026-38438</span></body></html>")
    monkeypatch.setattr(careers_lookup, "_get", _fake_get({
        "search": search, "ra-spec-ii-gurgaon-india": posting,
    }))
    result = careers_lookup.lookup("2026-38438", known_locations={"IN", "EU"})
    assert result["status"] == "ok"
    assert result["title"] == "RA Spec II"
    assert result["job_location"] == "IN"
    assert "RA" in result["jd_text"]


def test_table_view_fetches_only_the_matching_row(monkeypatch):
    """With several rows in the results table, only the row displaying the target
    req id is followed — no wasted fetches (the site rate-limits bursts)."""
    def row(slug: str, req: str) -> str:
        return (f'<tr><td><a href="/jobs/{slug}">Job</a></td><td>{req}</td>'
                f"<td>Dept</td><td>Gurgaon, India</td></tr>")
    ld = {"@type": "JobPosting", "title": "RA Spec II",
          "description": "<p>RA duties.</p>",
          "jobLocation": {"address": {"addressLocality": "Gurgaon", "addressCountry": "IN"}}}
    posting = (f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script>'
               f"</head><body><span>2026-38438</span></body></html>")
    fetched: list[str] = []
    fake = _fake_get({
        "search": f"<table>{row('ra-spec-ii', '2026-38438')}{row('other-job', '2026-99999')}</table>",
        "ra-spec-ii": posting,
    })

    def counting(url: str):
        if "search" not in url:
            fetched.append(url)
        return fake(url)

    monkeypatch.setattr(careers_lookup, "_get", counting)
    assert careers_lookup.lookup("2026-38438")["status"] == "ok"
    assert len(fetched) == 1 and "ra-spec-ii" in fetched[0]


def test_posting_hrefs_exclude_nav_and_foreign_links():
    html = ('<a href="/jobs/search?page=2">next</a>'
            '<a href="https://careers.example.com/de/jobs/search">de</a>'
            '<a href="https://evil.example.com/jobs/fake-posting">x</a>'
            '<a href="/jobs/near-me">near</a>'
            '<a href="/jobs/real-posting-title">yes</a>')
    hrefs = careers_lookup._posting_hrefs_in(html)
    assert hrefs == ["https://careers.example.com/jobs/real-posting-title"]


def test_zero_results_not_found(monkeypatch):
    monkeypatch.setattr(careers_lookup, "_get", _fake_get({
        "search": "<html><body>No jobs matched your search.</body></html>",
    }))
    assert careers_lookup.lookup("2026-0000")["status"] == "not_found"


def test_multiple_matches_ambiguous(monkeypatch):
    monkeypatch.setattr(careers_lookup, "_get", _fake_get({
        "search": f'<a href="{POST1}">A</a><a href="{POST2}">B</a>',
        UUID1: _posting_html("2026-38563", locality="Gurgaon"),
        UUID2: _posting_html("2026-38563", locality="Noida"),
    }))
    result = careers_lookup.lookup("2026-38563")
    assert result["status"] == "ambiguous" and result["count"] == 2


def test_site_unreachable_is_error_not_notfound(monkeypatch):
    """Unreachable/blocked is reported as an honest 'couldn't reach' error — NOT
    as 'no posting found', which would wrongly suggest the req id is bad."""
    monkeypatch.setattr(careers_lookup, "_get", lambda url: None)  # site unreachable
    result = careers_lookup.lookup("2026-38563")
    assert result["status"] == "error"
    assert "try again" in result["note"]


def test_bot_challenge_page_is_rejected(monkeypatch):
    """The live site's bot protection answers 202 (or 200 with a 'verify you're
    not a robot' shell). Both must read as fetch failures, never as content."""
    class _Resp:
        def __init__(self, status: int, text: str):
            self.status_code, self.text = status, text

    pages = {
        "http://x/202": _Resp(202, "<html>shell</html>"),
        "http://x/robot": _Resp(200, "In order to continue, we need to verify "
                                     "that you're not a robot."),
        "http://x/real": _Resp(200, "<html>real content</html>"),
    }

    class _Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get(self, url): return pages[url]

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    assert careers_lookup._get("http://x/202") is None
    assert careers_lookup._get("http://x/robot") is None
    assert careers_lookup._get("http://x/real") == "<html>real content</html>"


def test_get_retries_transient_202_then_succeeds(monkeypatch):
    """The 202 bot/rate wall is transient (worse from datacenter IPs) — retry on
    the same client and take the next 200, so a first-attempt 202 doesn't surface
    as a spurious 'couldn't reach the site' to the recruiter."""
    seq = iter([202, 202, 200])
    calls = {"n": 0}

    class _Resp:
        def __init__(self, status: int):
            self.status_code = status
            self.text = "<html>real</html>" if status == 200 else ""

    class _Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get(self, url):
            calls["n"] += 1
            return _Resp(next(seq))

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    assert careers_lookup._get("http://x/y") == "<html>real</html>"
    assert calls["n"] == 3  # two 202s retried, third attempt won


def test_get_gives_up_after_persistent_challenge(monkeypatch):
    """A sustained challenge (rate-limited IP) still fails honestly after a bounded
    number of attempts — no infinite retry, the recruiter falls back to manual."""
    calls = {"n": 0}

    class _Resp:
        status_code = 202
        text = ""

    class _Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get(self, url):
            calls["n"] += 1
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    assert careers_lookup._get("http://x/y") is None
    assert calls["n"] == careers_lookup._GET_ATTEMPTS


def test_junk_posting_page_never_fills_the_form(monkeypatch):
    """A posting URL that serves a JS shell (no schema.org JobPosting) must fail
    honestly — never auto-fill the form with the shell's text."""
    monkeypatch.setattr(careers_lookup, "_get", _fake_get({
        "search": f'<a href="{POST1}">Sr Accountant</a>',
        UUID1: "<html><body>JavaScript is disabled</body></html>",
    }))
    result = careers_lookup.lookup("2026-38563")
    assert result["status"] == "error"
    assert "jd_text" not in result


def test_lookup_caches_successful_postings(monkeypatch):
    """A req id that resolved once keeps answering (zero new requests) even if
    the site starts bot-challenging afterwards."""
    calls: list[str] = []
    fake = _fake_get({
        "search": f'<a href="{POST1}">Sr Accountant</a>',
        UUID1: _posting_html("2026-38563"),
    })

    def counting(url: str):
        calls.append(url)
        return fake(url)

    monkeypatch.setattr(careers_lookup, "_get", counting)
    assert careers_lookup.lookup("2026-38563")["status"] == "ok"
    made = len(calls)
    monkeypatch.setattr(careers_lookup, "_get", lambda url: None)  # site now blocked
    again = careers_lookup.lookup("2026-38563", known_locations={"IN"})
    assert again["status"] == "ok" and again["job_location"] == "IN"
    assert len(calls) == made  # served from cache


# --- pure helpers -----------------------------------------------------------------


def test_map_location_prefers_most_specific():
    assert careers_lookup._map_location("Gurgaon, India", {"IN"}) == "IN"
    assert careers_lookup._map_location("Singapore", {"SG"}) == "SG"
    assert careers_lookup._map_location("Berlin, Germany", {"EU"}) == "EU"
    assert careers_lookup._map_location("Budapest, Hungary", {"EU"}) == "EU"
    # never return a code the picker doesn't offer
    assert careers_lookup._map_location("Gurgaon, India", {"US-default"}) is None
    assert careers_lookup._map_location("Atlantis", {"IN"}) is None


def test_map_location_never_guesses_a_country_from_a_state_code():
    """"Concord, CA" is Concord in Canada as readily as in California, and
    "Toronto, CA" is plainly Canada. The old rule read a trailing ", CA" as the
    state and screened a Canadian requisition under California's law — wrong
    retention (four years), wrong statute. Both are left manual now; the careers
    site's own three-part form still auto-fills California."""
    known = {"IN", "SG", "JP", "EU", "US-default", "US-NYC", "US-CA"}
    assert careers_lookup._map_location("Toronto, CA", known) is None
    assert careers_lookup._map_location("Concord, CA", known) is None
    assert careers_lookup._map_location("Concord, CA, US", known) == "US-CA"


def test_map_location_iso_country_codes():
    """The live site's addressCountry is an ISO code, not a spelled-out name."""
    known = {"IN", "SG", "JP", "EU", "US-default", "US-NYC", "US-CA"}
    assert careers_lookup._map_location("Gurgaon, IN", known) == "IN"
    assert careers_lookup._map_location("Tokyo, JP", known) == "JP"
    assert careers_lookup._map_location("Berlin, DE", known) == "EU"
    assert careers_lookup._map_location("Singapore, SG", known) == "SG"
    # US country code still narrows by state rules first
    assert careers_lookup._map_location("Concord, CA, US", known) == "US-CA"
    assert careers_lookup._map_location("New York, NY, US", known) == "US-NYC"
    assert careers_lookup._map_location("Austin, TX, US", known) == "US-default"


def test_get_sends_consent_cookies_and_browser_ua(monkeypatch):
    """The server-side equivalent of dismissing the cookie popup: accepted-consent
    cookies + a browser UA go out so a consent-gated careers page serves content."""
    captured: dict = {}

    class _Resp:
        status_code = 200
        text = "<html>ok</html>"

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            captured["url"] = url
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    assert careers_lookup._get("https://careers.example/jobs/x") == "<html>ok</html>"
    assert "OptanonAlertBoxClosed" in captured["cookies"]  # OneTrust "banner dismissed"
    assert "Mozilla" in captured["headers"]["User-Agent"]
    assert captured["follow_redirects"] is True


def test_drop_boilerplate_sections():
    text = ("How You'll Make an Impact\nOwn the close.\n"
            "Who We Are\nMarketing copy.\n"
            "What You Bring\nGAAP.\nBenefits\nPerks.")
    cleaned = careers_lookup._drop_boilerplate_sections(text)
    assert "Own the close." in cleaned and "GAAP." in cleaned
    assert "Marketing copy." not in cleaned and "Perks." not in cleaned


def test_drop_boilerplate_colon_and_inline_headings():
    """Real postings render headings with trailing colons, or as bold inline
    text that flattens to a single 'Heading: content...' line."""
    text = ("What You Bring:\nGAAP.\n"
            "Who We Are: For 70 years Acme has been marketing copy.\n"
            "Benefits: Perks galore.\n"
            "Requirements: CA certification preferred.")
    cleaned = careers_lookup._drop_boilerplate_sections(text)
    assert "GAAP." in cleaned and "CA certification" in cleaned
    assert "marketing copy" not in cleaned and "Perks galore" not in cleaned


# --- the route --------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app
    return TestClient(app)


def test_route_disabled_by_default(client):
    r = client.get("/api/careers/lookup?req=2026-38563", headers=_as("TASpecialist"))
    assert r.status_code == 200 and r.json()["status"] == "disabled"


def test_route_ok_when_flag_on(client, monkeypatch):
    monkeypatch.setattr("resume_screener.feature_flags.flag_enabled", lambda *a, **k: True)
    monkeypatch.setattr(careers_lookup, "_get", _fake_get({
        "search": f'<a href="{POST1}">Sr Accountant</a>',
        UUID1: _posting_html("2026-38563"),
    }))
    r = client.get("/api/careers/lookup?req=2026-38563", headers=_as("TASpecialist"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["job_location"] == "IN"
    assert "How You'll Make an Impact" in body["jd_text"]
