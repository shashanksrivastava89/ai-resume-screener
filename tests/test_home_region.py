"""Where a person's region lens opens (global rollout, 2026-09-12). Offline.

The lens defaulted to "All regions" and lived in one browser's localStorage, so a
recruiter in Budapest opened a dashboard of every region's requisitions on every
new device, every time. Nothing derived a starting point, because nothing read a
country from anywhere — no claim and no directory field for it was ever requested.

Precedence: what they chose > the country their directory record carries > their
only granted region > nothing. A value outside their grant is ignored at every
step, because this is a STARTING POINT and never an access decision — and "nothing"
is a perfectly good answer, since "All regions" shows more, not less.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine
from tests.test_sso_identity import principal

from resume_screener import people, user_settings
from resume_screener.api import service
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import identity as identity_repo
from resume_screener.db.seed import seed_region_framework

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A workspace with a seeded region framework and a real client."""
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(REPO_ROOT / "policies", tmp_path / "policies")
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)

    eng = make_test_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)

    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    class Handle:
        client = TestClient(app)

        def seed(self):
            with session_mod.session_scope() as s:
                seed_region_framework(s)
            service.invalidate_region_context()

        def grant(self, email: str, region: str | None, role: str = "TASpecialist"):
            with session_mod.session_scope() as s:
                identity_repo.grant_role(s, email, role, region_code=region)
            service.invalidate_region_context()

        def me(self, email: str, role: str = "TASpecialist") -> dict:
            return self.client.get("/api/me", headers={
                "X-MS-CLIENT-PRINCIPAL": principal(email, [role])}).json()

    return Handle()


# --- the precedence chain ------------------------------------------------------------


def test_no_grants_and_no_country_means_no_default(env):
    env.seed()
    assert env.me("nobody@example.com")["regions"]["home"] is None


def test_a_sole_granted_region_becomes_the_default(env):
    env.seed()
    env.grant("solo@example.com", "EMEA")
    regions = env.me("solo@example.com")["regions"]
    assert (regions["home"], regions["home_source"]) == ("EMEA", "sole_grant")


def test_two_granted_regions_leave_it_open(env):
    """Two is not an answer — "All regions" is, and it shows more."""
    env.seed()
    env.grant("both@example.com", "EMEA")
    env.grant("both@example.com", "AMER")
    assert env.me("both@example.com")["regions"]["home"] is None


def test_the_directorys_country_decides_over_a_sole_grant(env):
    env.seed()
    env.grant("hu@example.com", "EMEA")
    env.grant("hu@example.com", "AMER")
    people.remember("hu@example.com", country="HU", source="graph")
    regions = env.me("hu@example.com")["regions"]
    assert (regions["home"], regions["home_source"]) == ("EMEA", "country")


def test_what_they_chose_beats_everything(env):
    env.seed()
    env.grant("me@example.com", "EMEA")
    env.grant("me@example.com", "AMER")
    people.remember("me@example.com", country="HU", source="graph")
    user_settings.set("me@example.com", "home_region", "AMER")
    service.invalidate_region_context()
    regions = env.me("me@example.com")["regions"]
    assert (regions["home"], regions["home_source"]) == ("AMER", "chosen")


def test_a_default_outside_the_grant_is_ignored_not_obeyed(env):
    """A grant that is later narrowed must not leave someone pointing at a region
    they can no longer reach — it is a starting point, never an access decision."""
    env.seed()
    env.grant("moved@example.com", "EMEA")
    user_settings.set("moved@example.com", "home_region", "AMER")
    service.invalidate_region_context()
    regions = env.me("moved@example.com")["regions"]
    assert regions["home"] == "EMEA"  # fell through to the sole grant
    assert regions["home_source"] == "sole_grant"


def test_a_country_we_have_no_rules_for_yields_no_default(env):
    env.seed()
    people.remember("orchard@example.com", country="CA", source="graph")  # Canada
    assert env.me("orchard@example.com")["regions"]["home"] is None


# --- the region block on /api/me ------------------------------------------------------


def test_every_region_carries_the_name_a_person_reads(env):
    env.seed()
    env.grant("x@example.com", None)  # all regions
    regions = env.me("x@example.com")["regions"]
    assert regions["names"]["EMEA"] == "Europe, Middle East & Africa"
    assert set(regions["names"]) == set(regions["codes"])


def test_an_unseeded_deployment_still_answers(env):
    """No region framework: unrestricted, no codes, no default — exactly as
    before any of this existed."""
    regions = env.me("anyone@example.com")["regions"]
    assert regions["all"] is True and regions["codes"] == []
    assert regions["home"] is None


# --- setting it ------------------------------------------------------------------------


def test_a_person_sets_and_clears_their_own_default(env):
    env.seed()
    env.grant("p@example.com", "EMEA")
    env.grant("p@example.com", "AMER")
    headers = {"X-MS-CLIENT-PRINCIPAL": principal("p@example.com", ["TASpecialist"])}

    set_it = env.client.put("/api/me/home-region", headers=headers, json={"region": "AMER"})
    assert set_it.status_code == 200, set_it.text
    assert set_it.json()["regions"]["home"] == "AMER"

    cleared = env.client.put("/api/me/home-region", headers=headers, json={"region": None})
    assert cleared.status_code == 200
    assert cleared.json()["regions"]["home"] is None


def test_nobody_can_default_to_a_region_they_cannot_reach(env):
    env.seed()
    env.grant("p@example.com", "EMEA")
    bad = env.client.put(
        "/api/me/home-region",
        headers={"X-MS-CLIENT-PRINCIPAL": principal("p@example.com", ["TASpecialist"])},
        json={"region": "AMER"})
    assert bad.status_code == 400
    assert "access" in bad.json()["detail"].lower()


def test_someone_with_no_app_role_cannot_set_one(env):
    env.seed()
    refused = env.client.put(
        "/api/me/home-region",
        headers={"X-MS-CLIENT-PRINCIPAL": principal("stranger@example.com", [])},
        json={"region": "EMEA"})
    assert refused.status_code == 403
