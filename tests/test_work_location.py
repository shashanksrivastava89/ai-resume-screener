"""Job location + search radius, and the floor it must not become (2026-08-20).

A distance-from-the-office field runs straight into `commute_or_address`, a FLOOR
never-scored topic (CLAUDE.md §6.6): commute distance correlates with income, and in
India with community, so scoring it launders a protected attribute into a practical-
looking number. The resolution is a split, and these tests pin both halves:

  * WorkLocation is recruiter INTENT and reviewer CONTEXT. It never reaches layer 1,
    layer 2 or the score.
  * The scored path stays closed. A checklist row phrased as a distance requirement is
    caught by the critic and routed to HR-Head approval, as designed.

The second half needed real work: the topic's trigger phrases were written for
"commute" and "lives near", and did NOT match the natural phrasing of this very
feature ("Current location: Within 25 km from Gurugram"). The floor was evadable by
wording alone, which is the kind of gap that only shows up when someone tries to build
the thing the floor is meant to stop.
"""

from __future__ import annotations

import pathlib

import json

import pytest

from resume_screener.ats.local_folder import _work_location
from resume_screener.models import Requisition, WorkLocation
from resume_screener.prefcheck import classify_text


# --- the recruiter-facing line ------------------------------------------------------


@pytest.mark.parametrize("place,radius,expected", [
    ("Gurugram", 25, 'Current location: Within 25 km from "Gurugram"'),
    ("Mumbai (Andheri East)", 10, 'Current location: Within 10 km from "Mumbai (Andheri East)"'),
    # 0 is a real answer (at this site only) and must not read as "within 0 km".
    ("Gurugram", 0, 'Current location: at "Gurugram"'),
    # Radius not chosen yet — state the place, promise no distance.
    ("Gurugram", None, 'Job location: "Gurugram"'),
    ("", 25, ""),   # nothing to measure from -> say nothing
])
def test_summary_is_rendered_from_data(place, radius, expected):
    """The copy is generated, never hand-written at a call site, so the number and the
    place cannot drift apart in one screen's wording."""
    assert WorkLocation(place=place, radius_km=radius).summary() == expected


# --- the sidecar -------------------------------------------------------------------


def test_absent_from_the_sidecar_means_no_location():
    """Every requisition created before this feature. Must load, not raise."""
    assert _work_location({}) is None
    assert _work_location({"work_location": None}) is None
    assert _work_location({"work_location": "Gurugram"}) is None   # wrong shape


def test_a_radius_without_a_place_is_not_a_location():
    """Rendering "within 25 km of" nothing is worse than showing no location at all."""
    assert _work_location({"work_location": {"radius_km": 25}}) is None
    assert _work_location({"work_location": {"place": "   ", "radius_km": 25}}) is None


def test_a_malformed_radius_degrades_instead_of_breaking_the_list():
    """This is hand-editable JSON behind an intake form. One bad value must cost the
    radius, not the whole requisitions page."""
    wl = _work_location({"work_location": {"place": "Gurugram", "radius_km": "twenty"}})
    assert wl is not None and wl.place == "Gurugram" and wl.radius_km is None

    wl = _work_location({"work_location": {"place": "Gurugram", "radius_km": -5, "max_km": 0}})
    assert wl.radius_km == 0      # clamped, not negative
    assert wl.max_km == 100       # a zero ceiling would make the control unusable


def test_round_trips_through_the_requisition_contract():
    meta = {"work_location": {"place": "Gurugram", "radius_km": 25, "max_km": 150}}
    req = Requisition(req_id="r", role_title="t", jd_ref="jds/r.md",
                      work_location=_work_location(meta))
    again = Requisition.model_validate(json.loads(req.model_dump_json()))
    assert again.work_location == req.work_location
    assert again.work_location.max_km == 150


def test_requisitions_without_one_are_unchanged():
    req = Requisition(req_id="r", role_title="t", jd_ref="jds/r.md")
    assert req.work_location is None


# --- the floor the field must not become -------------------------------------------

# Ordinary requirements that happen to contain "within" or a place name. A floor screen
# that fires on these teaches recruiters to click through it, which makes the floor
# weaker than having no screen at all — this is why the bare word "within" was tried
# as a trigger phrase and then rejected.
CLEAN = [
    "Experience within the pharmaceutical sector",
    "Managed budgets within a listed entity",
    "Able to close books within 5 working days",
    "Willing to travel within the region",
    "Ind AS reporting experience",
    "5+ years in listed-company finance",
    "CA qualified",
]

# Every phrasing of "screen on where they live" we could think of, including the exact
# wording of the feature request itself.
ADDRESS_BASED = [
    'Current location: Within 25 km from "Gurugram"',
    "Within 25 km of Gurugram",
    "Within a radius of 30 km",
    "Candidate must be based in Gurugram",
    "Based out of Bengaluru",
    "Currently located in Delhi NCR",
    "Local candidates only",
    "Lives near the office",
    "Short commute preferred",
    "Willing to relocate",
    "Pin code must be in south Delhi",
    "Native place in Maharashtra",
]


@pytest.mark.parametrize("text", CLEAN)
def test_ordinary_requirements_stay_clean(text):
    assert classify_text(text).level == "ok", text


@pytest.mark.parametrize("text", ADDRESS_BASED)
def test_a_scored_distance_requirement_is_caught(text):
    """The gap this closed: the topic's phrases were written for "commute"/"lives near"
    and matched NONE of the natural ways a recruiter writes a distance requirement, so
    the floor was evadable by wording. A flagged row still saves — it is routed to
    HR-Head approval rather than silently dropped — which is the designed path for
    "this needs a named human to own it"."""
    assert classify_text(text).level in {"warn", "blocked"}, text


def test_the_non_scoring_field_is_not_itself_gated():
    """The sanctioned path must stay frictionless. WorkLocation carries the same words
    the critic flags, but it is data on the requisition rather than a scored
    requirement, so nothing about capturing it runs through classify_text."""
    wl = WorkLocation(place="Gurugram", radius_km=25)
    assert "km from" in wl.summary()          # the flagged phrasing, deliberately
    assert wl.radius_km == 25                  # captured, no approval involved


# --- the DB backend must not drop it (found on UAT, 2026-08-25) ---------------------


def test_the_db_backend_hydrates_work_location_from_the_sidecar(tmp_path):
    """THE bug this test exists for, and it only showed up on a real deployment.

    `fetch_requisitions` short-circuits to the Postgres repo when
    REQUISITIONS_BACKEND=db, and that table has no work_location column — so the field
    came back None forever on any DB-backed deployment. The PUT returned 200, the value
    landed in .req.json, and the read came from a table that does not carry it: the whole
    feature silently did nothing. It passed the entire local suite because the file
    backend is the default, which is exactly why it took one click on UAT to surface.

    work_location belongs to the same class as lifecycle, hm and the iCIMS id: sidecar in
    BOTH backends, no column, no migration. So the DB branch overlays it.
    """
    from resume_screener.ats.local_folder import hydrate_sidecar_only_fields, save_req_meta

    jd = tmp_path / "some-role.md"
    jd.write_text("# Some Role\n\nBody.\n", encoding="utf-8")
    save_req_meta(jd, work_location={"place": "Gurugram", "radius_km": 25, "max_km": 100})

    # What the DB repo hands back: the contract WITHOUT work_location.
    db_row = Requisition(req_id="some-role", role_title="Some Role", jd_ref=str(jd),
                         job_location="IN", icims_req_id="R-1")
    assert db_row.work_location is None

    got = hydrate_sidecar_only_fields([db_row])
    assert len(got) == 1
    assert got[0].work_location is not None, "the DB branch dropped work_location again"
    assert got[0].work_location.place == "Gurugram"
    assert got[0].work_location.radius_km == 25
    # the DB-sourced identity fields must survive the overlay untouched
    assert got[0].icims_req_id == "R-1"
    assert got[0].role_title == "Some Role"
    assert got[0].job_location == "IN"


def test_hydration_is_a_no_op_when_there_is_no_sidecar(tmp_path):
    """A DB row whose JD has no sidecar, and a row with no jd_ref at all, must both pass
    through rather than raise — the overlay is additive, never a new failure mode."""
    from resume_screener.ats.local_folder import hydrate_sidecar_only_fields

    jd = tmp_path / "bare.md"
    jd.write_text("# Bare\n", encoding="utf-8")
    rows = [
        Requisition(req_id="bare", role_title="Bare", jd_ref=str(jd)),
        Requisition(req_id="noref", role_title="No Ref", jd_ref=""),
    ]
    got = hydrate_sidecar_only_fields(rows)
    assert [r.work_location for r in got] == [None, None]
    assert [r.req_id for r in got] == ["bare", "noref"]


# --- the distance engine (2026-08-26) ----------------------------------------------
#
# The upgrade: distance is now a real number, computed at SCREENING time from a local
# gazetteer, and frozen into the run artifact. Three properties matter more than the
# arithmetic, and the tests below defend them in this order:
#   1. it is DETERMINISTIC and replayable (a table, not a geocoding service, which also
#      means no candidate location leaves the tenant);
#   2. an unknown distance is NEVER "far away";
#   3. nothing it produces can reach a score.

from resume_screener import location_context as lc


def test_gazetteer_loads_with_aliases_and_canonicalises():
    """Gurgaon/Gurugram, Bombay/Mumbai, Bangalore/Bengaluru are the same places. The
    requisition and the résumé must land on ONE spelling or every distance is silently
    unknown — the likeliest failure mode in the Indian pilot."""
    assert lc.load_places(), "signals/places.yaml did not load"
    assert lc.resolve_place("Gurgaon")[0] == "Gurugram"
    assert lc.resolve_place("Bombay")[0] == "Mumbai"
    assert lc.resolve_place("Bangalore")[0] == "Bengaluru"
    # a locality in brackets still resolves via its city
    assert lc.resolve_place("Mumbai (Andheri East)")[0] == "Mumbai"
    assert lc.resolve_place("Atlantis") is None


def test_distances_are_right_to_within_a_sane_tolerance():
    """Spot-checks against known real-world separations. Great-circle, city centre to
    city centre, so these are shorter than a road route — the tolerance reflects that,
    and the UI renders every value as "≈"."""
    assert 25 <= lc.distance_between("Gurugram", "Noida") <= 40
    assert 15 <= lc.distance_between("Gurugram", "Delhi") <= 32
    assert 1050 <= lc.distance_between("Gurugram", "Mumbai") <= 1200
    assert 1600 <= lc.distance_between("Gurugram", "Bengaluru") <= 1800
    # symmetric, and zero to itself
    assert lc.distance_between("Pune", "Mumbai") == lc.distance_between("Mumbai", "Pune")
    assert lc.distance_between("Gurugram", "Gurgaon") == 0.0


def test_distance_is_none_when_either_place_is_unknown():
    """The failure mode has to be "unknown", never a wrong number."""
    assert lc.distance_between("Gurugram", "Atlantis") is None
    assert lc.distance_between("Atlantis", "Gurugram") is None
    assert lc.distance_between("", "Gurugram") is None


def test_it_is_deterministic():
    """The property that makes this auditable, and the reason it is a table rather than
    a model call: identical inputs, identical output, every time."""
    runs = {lc.read_context("Current location: Pune", "Gurugram").distance_km for _ in range(5)}
    assert len(runs) == 1


@pytest.mark.parametrize("text,expected", [
    ("Current location: Pune, Maharashtra", "Pune"),
    ("Location - Gurgaon (Haryana)", "Gurugram"),
    ("Based in Bengaluru", "Bengaluru"),
    ("Permanent Address: Sector 62, Noida, UP 201301", "Noida"),
    ("Resident of Bombay", "Mumbai"),
])
def test_reads_the_stated_place_out_of_a_resume(text, expected):
    assert lc.stated_place(text) == expected


def test_an_explicit_location_line_beats_a_place_mentioned_in_passing():
    """A résumé naming client cities must not be read as living in the first one. The
    explicit line wins."""
    text = "Handled vendors across Mumbai and Chennai.\nCurrent Location: Jaipur\n"
    assert lc.stated_place(text) == "Jaipur"


def test_no_location_at_all_is_none_not_a_guess():
    """THE fairness invariant. A résumé that omits a city is not a candidate who lives
    far away, so this must be None — and every filter keeps a None visible."""
    assert lc.stated_place("Senior Accountant with Ind AS experience. SAP FICO.") is None
    assert lc.stated_place("") is None


def test_read_context_shapes_the_three_outcomes():
    known = lc.read_context("Current location: Pune", "Gurugram")
    assert known.stated == "Pune" and known.resolved is True
    assert known.distance_km and known.distance_km > 1000

    unresolvable = lc.read_context("Current location: Ruritania", "Gurugram")
    assert unresolvable.stated is not None          # it DID state something
    assert unresolvable.distance_km is None         # we just cannot place it
    assert unresolvable.resolved is False

    silent = lc.read_context("No city anywhere in this document.", "Gurugram")
    assert silent.stated is None and silent.distance_km is None

    # No job place -> the lens is simply off, not an error.
    off = lc.read_context("Current location: Pune", "")
    assert off.job_place == "" and off.distance_km is None


def test_the_note_never_invents_a_distance_or_rejects_anyone():
    assert "≈" in lc.read_context("Current location: Pune", "Gurugram").note(25)
    assert lc.read_context("nothing here", "Gurugram").note(25) == \
        "No location stated on the résumé"
    assert "unknown" in lc.read_context("Location: Ruritania", "Gurugram").note(25)
    # a candidate AT the job location reads naturally, not as "≈0 km"
    assert lc.read_context("Location: Gurgaon", "Gurugram").note(25) == \
        "States Gurugram — in Gurugram"
    for text in ("Current location: Pune", "nothing here", "Location: Ruritania"):
        note = lc.read_context(text, "Gurugram").note(25).lower()
        assert "reject" not in note and "not a fit" not in note and "ineligible" not in note


def test_the_context_never_carries_a_score_shaped_field():
    """Belt and braces on the guardrail: the contract frozen into the artifact must
    expose kilometres and place names, and nothing that could be summed into a score."""
    fields = set(lc.LocationContext.model_fields)
    assert fields == {"job_place", "stated", "distance_km", "resolved"}
    for banned in ("points", "score", "band", "weight", "cap", "adjustment"):
        assert not any(banned in f for f in fields)


# --- two bugs found while wiring the chain, both silent ------------------------------


@pytest.mark.parametrize("text,expected", [
    # A single trailing full stop used to defeat the whole lookup: "Based in Bengaluru."
    # folded to " bengaluru. 6 years… " so the search for " bengaluru " missed, the
    # gazetteer sweep missed too, and it fell through to the raw-text fallback —
    # reporting the city as "Bengaluru. 6 years in" with an unknown distance. Punctuation
    # around a place name must never disable the lens.
    ("Based in Bengaluru. 6 years in payables.", "Bengaluru"),
    ("Resident of Bombay.", "Mumbai"),
    ("Location: Pune, Maharashtra.", "Pune"),
    ("Currently located in Hyderabad; open to travel.", "Hyderabad"),
])
def test_punctuation_around_a_place_never_defeats_the_match(text, expected):
    assert lc.stated_place(text) == expected


def test_a_bare_keyword_in_prose_is_not_a_location_statement():
    """The other silent bug: the field-label pattern matched the bare word "city" in
    ordinary prose and captured the rest of the sentence as the stated place, so a
    résumé with no location at all reported "States <junk> — distance unknown" instead
    of the truthful "No location stated". A label now requires a separator; only
    specific prose phrases ("based in X") work without one."""
    assert lc.stated_place("No city anywhere in this document.") is None
    assert lc.stated_place("Managed address data for the vendor master.") is None
    # but a real label, with its separator, still works
    assert lc.stated_place("City: Indore") == "Indore"


def test_location_is_frozen_into_the_artifact_and_never_inside_the_score():
    """The screening step writes this once so Review reads a number instead of parsing
    42 PDFs off the share on every page load (measured: 26s before). It has to sit
    BESIDE the score in the record, never inside it — a location field within
    ScoreResult would be one refactor away from being summed into a total."""
    from resume_screener.findings import FindingsRecord
    from resume_screener.models import Layer1Result, ScoreResult
    from resume_screener.report import CandidateBundle, _candidate_json

    l1 = Layer1Result(candidate_id="c1", rubric_version=1, coverage_score=0.9,
                      base_score=90.0, eligible=True)
    score = ScoreResult(candidate_id="c1", role_title="t", jurisdiction="IN", layer1=l1,
                        final_score=90, fit_band="Strong", confidence="high",
                        summary_rationale="x")
    ctx = lc.read_context("Permanent Address: Sector 62, Noida", "Gurugram")
    rec = _candidate_json(CandidateBundle(
        score=score,
        findings=FindingsRecord(candidate_id="c1", role_title="t", rubric_version=1, findings=[]),
        location=ctx,
    ))
    assert rec["location"]["stated"] == "Noida"
    assert rec["location"]["distance_km"] == pytest.approx(32.2, abs=1.0)
    assert "location" not in rec["score"], "location must never live inside ScoreResult"

    # A record from before this feature simply has no location block.
    bare = _candidate_json(CandidateBundle(
        score=score,
        findings=FindingsRecord(candidate_id="c1", role_title="t", rubric_version=1, findings=[]),
    ))
    assert bare["location"] is None


def test_the_requisitions_list_carries_the_place_not_just_the_detail():
    """The dashboard's Location column shows the CITY, so the LIST payload must
    carry work_location — not only the per-requisition detail.

    This was a real gap: `_role_detail` emitted it and `role_summary` did not, so
    a requisition with "Gurugram" on record still showed "India" on the list and
    the city could never appear no matter what the client did."""
    from resume_screener.api import service
    from resume_screener.models import Requisition, WorkLocation

    req = Requisition(req_id="finance-analyst", role_title="Finance Analyst",
                      jd_ref="jds/finance-analyst.md", job_location="IN",
                      work_location=WorkLocation(place="Gurugram", radius_km=25, max_km=100))
    card = service.role_summary(service.config(), req)
    assert card["work_location"]["place"] == "Gurugram"
    assert card["work_location"]["radius_km"] == 25
    assert card["work_location"]["summary"]           # resolved server-side
    assert card["location_display"]                   # the jurisdiction still ships

    # A requisition with no place keeps the key (same shape), so the client's
    # fallback to the jurisdiction is a plain `||`, never an undefined access.
    plain = service.role_summary(service.config(),
                                 Requisition(req_id="x", role_title="X",
                                             jd_ref="jds/x.md", job_location="IN"))
    assert "work_location" in plain and plain["work_location"] is None


def test_the_degraded_card_has_the_same_shape():
    """A broken requisition degrades to a "needs attention" card; it must expose the
    same keys, or the client crashes on the row it was meant to rescue."""
    from resume_screener.api import service
    from resume_screener.models import Requisition

    card = service._unavailable_card(Requisition(req_id="broken", role_title="Broken",
                                                 jd_ref="jds/broken.md", job_location="IN"))
    assert "work_location" in card and card["work_location"] is None


# --- the place is derived, never typed (2026-08-27) ------------------------------------


def _req_with_jd(tmp_path, jd_body: str):
    """A requisition whose JD lives in a workspace that HAS the gazetteer — deriving a
    place is a lookup in signals/places.yaml, which dataroot seeds for real runs, so a
    workspace without it resolves nothing (which is how this test first failed)."""
    import shutil

    from resume_screener.models import Requisition

    signals = tmp_path / "signals"
    signals.mkdir(exist_ok=True)
    repo_places = pathlib.Path(__file__).resolve().parents[1] / "signals" / "places.yaml"
    shutil.copyfile(repo_places, signals / "places.yaml")
    jds = tmp_path / "jds"
    jds.mkdir(exist_ok=True)
    jd = jds / "analyst.md"
    jd.write_text(jd_body, encoding="utf-8")
    return Requisition(req_id="analyst", role_title="Analyst", jd_ref=str(jd),
                       job_location="IN")


def test_the_place_is_derived_from_the_job_description(tmp_path, monkeypatch):
    """Auto-populated, because the free-text box invited someone to retype what the JD
    already said — and two spellings of one city read as two sites."""
    monkeypatch.chdir(tmp_path)
    from resume_screener.api import service

    req = _req_with_jd(tmp_path, "# Analyst\nBased at our Gurgaon office.\n")
    filled = service.ensure_work_place(req)
    # Canonicalised on the way in: Gurgaon is stored as Gurugram.
    assert filled.work_location is not None
    assert filled.work_location.place == "Gurugram"
    # ...and persisted, so the dashboard and the filter agree without re-deriving.
    from resume_screener.ats.local_folder import load_req_meta
    assert (load_req_meta(req.jd_ref)["work_location"]["place"]) == "Gurugram"


def test_a_jd_naming_no_known_city_yields_no_place(tmp_path, monkeypatch):
    """The honest outcome. A real pilot JD says only "Legal Entity: … (India) Pvt Ltd",
    so there is nothing to derive — the lens stays off rather than inventing a location
    or nagging for one."""
    monkeypatch.chdir(tmp_path)
    from resume_screener.api import service

    req = _req_with_jd(tmp_path, "# Analyst\nLegal Entity: (IND_3006) Acme (India) Pvt Ltd\n")
    assert service.ensure_work_place(req).work_location is None


def test_deriving_never_overwrites_a_place_already_on_record(tmp_path, monkeypatch):
    """An existing place — and any radius attached to it — is left alone."""
    monkeypatch.chdir(tmp_path)
    from resume_screener.api import service
    from resume_screener.models import WorkLocation

    req = _req_with_jd(tmp_path, "# Analyst\nBased in Pune.\n")
    req = req.model_copy(update={"work_location": WorkLocation(
        place="Mumbai", radius_km=30, max_km=100)})
    kept = service.ensure_work_place(req)
    assert kept.work_location.place == "Mumbai" and kept.work_location.radius_km == 30


# --- a résumé states where it lives in its own language (2026-09-12) -----------------


def test_a_hungarian_address_line_is_read():
    """"Lakcím:" matched nothing before, so every Hungarian candidate read
    "distance unknown" — safe (an unknown distance never hides anyone) and the
    lens was simply switched off for the market going live next."""
    assert lc.stated_place("Kovács Péter\nLakcím: 1117 Budapest, Irinyi utca 4.\n") == "Budapest"


def test_a_german_and_a_dutch_address_line_are_read():
    assert lc.stated_place("Wohnort: München\n") == "Munich"
    assert lc.stated_place("Woonplaats: Amsterdam\n") == "Amsterdam"


def test_prose_in_another_language_states_a_location():
    assert lc.stated_place("Derzeit wohnhaft in Berlin und offen für Umzug.\n") == "Berlin"


def test_a_bare_preposition_is_not_a_location_statement():
    """Each prose phrase carries the meaning on its own, the way "based in" does.
    A bare "in" does not, so a line like this is never read as the candidate
    SAYING where they live — it falls through to the last-resort sweep, which is
    a different and weaker claim."""
    line = "Projekte in Berlin und Wien geleitet."
    assert not lc._LABEL_LINE.search(line)
    assert not lc._PROSE_LINE.search(line)


def test_a_label_still_needs_its_separator():
    """The rule that keeps an ordinary word from inventing a home town."""
    assert lc.stated_place("Managed the city centre rollout across three sites.\n") is None
