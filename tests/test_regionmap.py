"""P38: the region ↔ jurisdiction constants and the seed-sync pin.

regionmap.py is the pure, DB-free copy of the region model the engine/API use for
the access lens and dual-timezone stamps. It MUST never drift from db/seed.py's
region framework — this suite imports the seed tables and asserts they agree, so a
future edit to one without the other fails here. Fully offline (no DB, no Azure).
"""

from __future__ import annotations

from resume_screener import regionmap
from resume_screener.db.seed import JURISDICTIONS, REGIONS


# --- mapping + US- fallback (mirrors policy._resolve_pack_path) ---------------------


def test_known_jurisdictions_map_to_their_region():
    assert regionmap.region_for_jurisdiction("IN") == "IN"
    assert regionmap.region_for_jurisdiction("SG") == "APAC"
    assert regionmap.region_for_jurisdiction("JP") == "APAC"
    assert regionmap.region_for_jurisdiction("EU") == "EMEA"
    assert regionmap.region_for_jurisdiction("US-default") == "AMER"
    assert regionmap.region_for_jurisdiction("US-NYC") == "AMER"
    assert regionmap.region_for_jurisdiction("US-CA") == "AMER"


def test_uppercase_normalization():
    assert regionmap.region_for_jurisdiction("us-ca") == "AMER"
    assert regionmap.region_for_jurisdiction("in") == "IN"


def test_unknown_us_code_falls_back_to_amer():
    # An unlisted US-* jurisdiction lands on US-default's region — never an error.
    assert regionmap.region_for_jurisdiction("US-TX") == "AMER"
    assert regionmap.region_for_jurisdiction("us-wa") == "AMER"


def test_unknown_non_us_code_maps_to_itself():
    # A display default (mirrors IN→IN), uppercased, never an error.
    assert regionmap.region_for_jurisdiction("ZZ") == "ZZ"
    assert regionmap.region_for_jurisdiction("") == ""


def test_tz_lookup_and_fallback():
    assert regionmap.tz_for_jurisdiction("IN") == "Asia/Kolkata"
    assert regionmap.tz_for_jurisdiction("US-NYC") == "America/New_York"
    assert regionmap.tz_for_jurisdiction("US-TX") == "America/Chicago"  # US fallback
    assert regionmap.tz_for_jurisdiction("ZZ") == "UTC"  # unknown → UTC display default


# --- seed-sync pin: regionmap constants must equal db/seed.py's tables --------------


def test_region_of_jurisdiction_matches_seed():
    seed_map = {code: region for code, _name, _law, region in JURISDICTIONS}
    assert regionmap.REGION_OF_JURISDICTION == seed_map


def test_every_mapped_region_is_a_seeded_region():
    seeded = {code for code, _name in REGIONS}
    assert set(regionmap.REGION_OF_JURISDICTION.values()) <= seeded


def test_tz_and_region_tables_cover_the_same_jurisdictions():
    assert set(regionmap.TZ_OF_JURISDICTION) == set(regionmap.REGION_OF_JURISDICTION)
