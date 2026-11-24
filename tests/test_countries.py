"""The country -> hiring-rules-pack table (global rollout, 2026-09-12).

The table names packs, so it can only be right relative to `policies/`: a row
pointing at a pack that does not exist is a requisition nobody can screen. That
pairing is the first test here, and it is why the table is code rather than a
file on the DATA_ROOT share.

The rest pins the two mistakes this module was written to end: a Budapest
requisition resolving to India (Hungary was in no list), and "Toronto, CA"
resolving to California (there was no ISO row for Canada, so a US state rule
caught it). Fully offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_screener import countries

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICIES = REPO_ROOT / "policies"


# --- the table agrees with policies/ ----------------------------------------------


def test_every_referenced_pack_exists_on_disk():
    packs = {p.stem for p in POLICIES.glob("*.yaml")} - {"base"}
    assert countries.pack_codes() <= packs, (
        "the country table names a pack with no policies/<code>.yaml — a "
        "requisition there could never be screened")


def test_every_pack_is_reachable_from_some_country():
    packs = {p.stem for p in POLICIES.glob("*.yaml")} - {"base"}
    assert packs <= countries.pack_codes(), (
        "a policy pack no country maps to is unreachable from the form")


def test_alpha2_and_alpha3_codes_are_unique():
    assert len({c.alpha2 for c in countries.COUNTRIES}) == len(countries.COUNTRIES)
    assert len({c.alpha3 for c in countries.COUNTRIES}) == len(countries.COUNTRIES)


# --- Hungary: the requisition that used to become India ---------------------------


@pytest.mark.parametrize("text", ["HU", "hu", "HUN", "Hungary", "Magyarország"])
def test_hungary_resolves_to_the_eu_pack(text):
    assert countries.resolve(text).pack == "EU"


def test_budapest_location_string_is_eu():
    assert countries.parse_location_string("Budapest, HU").pack == "EU"
    assert countries.parse_location_string("Budapest, Hungary").pack == "EU"


def test_every_eu_member_lands_on_the_eu_pack():
    members = [c for c in countries.COUNTRIES if c.pack == "EU"]
    assert len(members) == 27, "the EU pack should cover the 27 member states"
    for member in members:
        assert countries.resolve(member.alpha2).pack == "EU"


# --- Canada: the requisition that used to become California -----------------------


def test_toronto_ca_is_canada_not_california():
    res = countries.parse_location_string("Toronto, CA")
    assert res.country is not None and res.country.alpha2 == "CA"
    assert res.pack is None
    assert res.known_without_pack is True


def test_a_country_without_a_pack_says_so_by_name():
    res = countries.resolve("Canada")
    assert res.pack is None
    assert res.known_without_pack is True
    assert "Canada" in res.reason


def test_unknown_text_is_not_the_same_as_a_country_without_a_pack():
    res = countries.resolve("Wakanda")
    assert res.pack is None
    assert res.known_without_pack is False


# --- the US: state, and the city that carries its own law -------------------------


def test_hercules_california_us_is_the_california_pack():
    assert countries.parse_location_string("Concord, CA, US").pack == "US-CA"
    assert countries.resolve("US", "California").pack == "US-CA"


def test_austin_texas_us_is_the_federal_baseline():
    assert countries.parse_location_string("Austin, TX, US").pack == "US-default"


def test_new_york_city_is_local_law_144_but_albany_is_not():
    assert countries.parse_location_string("New York, NY, US").pack == "US-NYC"
    assert countries.parse_location_string("Albany, NY, US").pack == "US-default"
    assert countries.resolve("US", "NY", nyc=True).pack == "US-NYC"
    assert countries.resolve("US", "NY", nyc=False).pack == "US-default"


def test_a_us_job_with_no_state_still_has_the_federal_baseline():
    assert countries.resolve("US").pack == "US-default"


def test_two_part_city_state_without_a_country_is_left_manual():
    # "Concord, CA" is Concord in Canada as readily as in California; refusing
    # to guess is the point (the old rule guessed California).
    assert countries.parse_location_string("Concord, CA").pack is None


# --- time zones -------------------------------------------------------------------


def test_time_zone_comes_from_the_country_then_the_state():
    assert countries.tz_for("HU") == "Europe/Budapest"
    assert countries.tz_for("IN") == "Asia/Kolkata"
    assert countries.tz_for("US", "CA") == "America/Los_Angeles"
    assert countries.tz_for("US", "NY") == "America/New_York"
    assert countries.tz_for("US") is None  # no state -> the pack's zone decides
    assert countries.tz_for("ZZ") is None


def test_every_us_state_has_a_zone_and_a_pack():
    for code, (name, pack, tz) in countries.US_STATES.items():
        assert name and tz, code
        assert pack in countries.pack_codes(), code


# --- display + catalog ------------------------------------------------------------


def test_name_of_reads_a_stored_code():
    assert countries.name_of("HU") == "Hungary"
    assert countries.name_of(None) == ""


def test_catalog_is_alphabetical_and_carries_the_no_pack_rows():
    rows = countries.catalog()
    assert [r["name"] for r in rows] == sorted(r["name"] for r in rows)
    canada = next(r for r in rows if r["code"] == "CA")
    assert canada["pack"] is None
