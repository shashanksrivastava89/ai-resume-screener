"""The distance lens has to work for a requisition anywhere (2026-09-11).

A pilot requisition for BUDAPEST, screened against résumés that plainly state Gurugram,
showed "distance is unknown" for every single candidate. Neither the filter nor the
haversine was wrong: Budapest was not in `signals/places.yaml`, so the JOB's own side of
every pair was unresolvable and no candidate could ever produce a number. The table's own
comment had predicted it — the non-India list existed "so a non-India requisition is not
silently 100% unknown, not as real coverage" — and a second requisition found the edge.

Three things are pinned here, because all three failed together:

  1. COVERAGE. The table now carries the places a requisition plausibly names, and the
     coordinates are checked against published distances rather than trusted.
  2. REACH. `signals/` is seeded into DATA_ROOT and never overwritten, so a release that
     adds cities would reach a fresh deployment and no other. The shipped table is now
     merged UNDER the share's, which is the same lesson features.yaml taught in pass 6.
  3. HONESTY. When the unresolvable side IS the job's own city, no row may report it as
     that candidate's missing distance — the gap belongs to us, and saying it forty
     times sends the reader to the résumés.
"""

from __future__ import annotations

import pytest
import yaml

from resume_screener import location_context as lc
from resume_screener.api import review as review_mod
from resume_screener.api import service
from resume_screener.models import Requisition, WorkLocation


@pytest.fixture(autouse=True)
def _fresh_table():
    """The table is cached on file state with a 15 s net; tests write their own."""
    lc.invalidate()
    yield
    lc.invalidate()


# --- 1. coverage --------------------------------------------------------------------


def test_the_requisition_that_started_this_can_be_located():
    """Budapest to Gurugram, both sides resolved, one real number."""
    assert lc.resolve_place("Budapest") is not None
    assert lc.resolve_place("Budapest, Hungary")[0] == "Budapest"
    km = lc.distance_between("Budapest", "Gurugram")
    assert km is not None and 5_000 < km < 5_800, km

    ctx = lc.read_context("Current location: Gurugram", "Budapest")
    assert ctx.resolved is True
    assert "unknown" not in ctx.note(25)
    assert "beyond 25 km" in ctx.note(25)   # far, and said as a fact, not a verdict


# Published great-circle distances. These exist to catch a mistyped coordinate, which is
# the one failure mode of a hand-maintained table: a wrong number here is worse than a
# missing city, because nothing about it looks wrong on screen.
@pytest.mark.parametrize("a,b,published_km", [
    ("Budapest", "Vienna", 214),
    ("London", "Paris", 344),
    ("Delhi", "Mumbai", 1153),
    ("New York", "San Francisco", 4129),
    ("Sydney", "Melbourne", 714),
    ("Tokyo", "Singapore", 5320),
    ("Berlin", "Munich", 504),
    ("Sao Paulo", "Buenos Aires", 1673),
    ("Dubai", "Mumbai", 1926),
    ("Gurugram", "Noida", 31),
])
def test_the_coordinates_agree_with_published_distances(a, b, published_km):
    got = lc.distance_between(a, b)
    assert got is not None, f"{a} or {b} is missing from the table"
    assert abs(got - published_km) <= max(3, published_km * 0.02), f"{a}->{b} = {got} km"


def test_every_entry_is_a_real_point_on_earth():
    raw = yaml.safe_load(open("signals/places.yaml", encoding="utf-8"))
    entries = raw["places"]
    assert len(entries) > 200, "the table shrank"
    for entry in entries:
        assert -90 <= entry["lat"] <= 90, entry
        assert -180 <= entry["lon"] <= 180, entry
        # 0,0 is in the Atlantic and is what a missing coordinate looks like.
        assert (entry["lat"], entry["lon"]) != (0, 0), entry


def test_no_two_cities_share_a_name():
    """The first entry wins, so a duplicate would report the loser's residents thousands
    of km from home. "Unknown" is the honest answer for an ambiguous name; this is why
    Cambridge and Birmingham are deliberately absent.

    The same check catches an alias that does nothing, which is worth removing for a
    different reason: it suggests to the next editor that accents need spelling out, and
    they do not — comparison happens on an accent-stripped fold.
    """
    raw = yaml.safe_load(open("signals/places.yaml", encoding="utf-8"))
    seen: dict[str, str] = {}
    for entry in raw["places"]:
        for name in [entry["name"], *(entry.get("aliases") or [])]:
            key = lc._fold(str(name)).strip()
            owner = seen.get(key)
            assert owner != entry["name"], \
                f"{name!r} adds nothing to {entry['name']} — the fold already matches it"
            assert owner is None, \
                f"{name!r} is claimed by both {owner} and {entry['name']}"
            seen[key] = entry["name"]


# --- 2. the sweep rule --------------------------------------------------------------


def test_a_name_that_is_also_an_ordinary_word_stays_out_of_the_document_sweep():
    """Growing the table grows the risk of the LAST-RESORT scan reading a city out of
    ordinary prose. A confidently wrong home town is worse than an honest unknown, so
    those names carry `sweep: false` — and stay fully usable everywhere else."""
    assert lc.stated_place("Rose from the ashes like a phoenix after the merger.") is None
    assert lc.stated_place("Reported to Austin Fernandes, Finance Director.") is None
    # ... but they are still real places when someone actually says so, or when the
    # requisition is there.
    assert lc.stated_place("Current location: Austin") == "Austin"
    assert lc.resolve_place("Phoenix")[0] == "Phoenix"
    assert lc.distance_between("Phoenix", "Los Angeles") is not None


def test_an_ordinary_city_is_still_found_in_prose():
    assert lc.stated_place("Worked in Budapest for three years.") == "Budapest"
    assert lc.stated_place("Relocated to Bengaluru in 2021.") == "Bengaluru"


def test_the_longest_name_wins_inside_a_location_line():
    """"Current location: Navi Mumbai" is not Mumbai. The document sweep already went
    longest-first; the explicit-line match went in dict order and did not."""
    assert lc.stated_place("Current location: Navi Mumbai") == "Navi Mumbai"


# --- 3. the build's table reaches a deployment that already has one ------------------


def test_the_build_adds_cities_and_the_share_corrects_them(tmp_path):
    """DATA_ROOT seeds `signals/places.yaml` once and NEVER overwrites it, so without a
    merge the cities added in a release would reach a fresh deployment and no other —
    exactly the trap features.yaml hit. The shipped table is the floor; the share's
    entries are laid over it by name."""
    stored = tmp_path / "places.yaml"
    stored.write_text(yaml.safe_dump({"schema": 1, "places": [
        # a correction of a shipped city (deliberately absurd coordinates)
        {"name": "Gurugram", "lat": 0.5, "lon": 0.5},
        # a city only this deployment knows about
        {"name": "Ruritania City", "lat": 47.0, "lon": 19.5},
    ]}), encoding="utf-8")

    table = lc.places(stored)
    assert table.resolve("Gurugram")[1] == 0.5, "the share's correction was ignored"
    assert table.resolve("Ruritania City") is not None, "a share-only city was dropped"
    assert table.resolve("Budapest") is not None, "the build's cities never arrived"
    assert table.resolve("Gurgaon") is not None, "the shipped alias was lost"


def test_a_broken_table_degrades_to_unknown_rather_than_500(tmp_path):
    bad = tmp_path / "places.yaml"
    bad.write_text("places: [[[not a mapping", encoding="utf-8")
    assert lc.places(bad).resolve("Ruritania City") is None
    # the build's own cities still answer, so one bad edit on the share is not an outage
    assert lc.places(bad).resolve("Budapest") is not None


# --- 4. the review says WHOSE side is unknown ---------------------------------------


def _req(place: str, radius: int = 25) -> Requisition:
    return Requisition(req_id="r", role_title="Financial Analyst", jd_ref="jds/r.md",
                       work_location=WorkLocation(place=place, radius_km=radius, max_km=100))


def _card_for(place: str, stored_location: dict, radius: int = 25) -> dict:
    card = {"candidate_id": "c1"}
    review_mod._attach_location_context(
        _req(place, radius), [card], {"c1": {"location": stored_location}})
    return card


def test_a_corrected_table_reaches_candidates_already_screened():
    """The distance is re-derived at READ time from the place the résumé stated, which
    is the half that was expensive to work out. Adding a city therefore fixes the 300
    candidates already screened against it — the alternative was asking a pilot to
    re-run a screening job to pick up a one-line data fix."""
    # What the artifact holds for someone screened while the table had no Budapest:
    # the stated place (real work), and no distance (arithmetic that could not be done).
    stored = {"job_place": "Budapest", "stated": "Gurugram",
              "distance_km": None, "resolved": False}
    card = _card_for("Budapest", stored)
    assert card["location_km"] is not None
    assert card["location_resolved"] is True
    assert card["location_within"] is False          # 5,000 km is not within 25
    assert "unknown" not in card["location_note"]


def test_editing_the_job_location_re_measures_from_the_new_place():
    """A frozen distance is measured from wherever the job WAS. After an edit the note
    names the new city beside a number belonging to the old one, which is worse than
    unknown because it looks right."""
    stored = {"job_place": "Budapest", "stated": "Gurugram",
              "distance_km": 5362.6, "resolved": True}
    # The requisition moved to Noida, half an hour away rather than half a continent.
    card = _card_for("Noida", stored, radius=50)
    assert card["location_km"] < 60, card["location_km"]
    assert card["location_within"] is True


def test_when_the_job_place_is_unknown_no_row_blames_the_resume():
    """The reason is OURS, and it is the same for everyone in the list, so it is said
    once on the filter (place_known) rather than forty times on the rows."""
    stored = {"job_place": "Ruritania City", "stated": "Gurugram",
              "distance_km": None, "resolved": False}
    card = _card_for("Ruritania City", stored)
    assert card["location_note"] == "States Gurugram"
    assert "unknown" not in card["location_note"]
    assert card["location_km"] is None
    # and nobody is filtered out by a gap in our table
    assert card["location_within"] is None

    assert service.work_location_view(_req("Ruritania City"))["place_known"] is False
    assert service.work_location_view(_req("Budapest"))["place_known"] is True


def test_a_resume_that_states_nothing_still_says_so():
    """The distinction that matters most: an omission is not evidence of distance."""
    card = _card_for("Budapest", {"job_place": "Budapest", "stated": None})
    assert card["location_note"] == "No location stated on the résumé"
    assert card["location_within"] is None


def test_the_place_is_resolved_once_per_review_not_once_per_candidate():
    """Review re-derives the arithmetic for the whole batch, so the table must be loaded
    once. `resolve_place` used to stat() the file twice per lookup — 666 filesystem round
    trips on an SMB share for a 333-candidate list."""
    cards = [{"candidate_id": f"c{i}"} for i in range(50)]
    records = {f"c{i}": {"location": {"stated": "Pune"}} for i in range(50)}
    calls: list[str] = []
    real_state = lc._state

    def counting_state(path):
        calls.append(str(path))
        return real_state(path)

    lc._state = counting_state           # noqa: SLF001 - narrow, restored below
    try:
        review_mod._attach_location_context(_req("Gurugram"), cards, records)
    finally:
        lc._state = real_state
    assert len(calls) <= 2, calls        # the stored file and the packaged one, once
    assert all(c["location_km"] is not None for c in cards)
