"""Region access becomes administrable (global rollout, 2026-09-12). Offline.

THE STATE THIS ENDS. Region scoping has been declared since P38 and unusable ever
since: `grant_role` had no caller outside `screener db seed`, so every real SSO
user held no grants, and `service._region_context_query` deliberately treats an
unscoped person as UNRESTRICTED rather than locked out — "a gate nobody can
administer is not access control, it is a trap".

That default does NOT change here, and the first test says so: someone nobody has
scoped still sees everything. What changes is that scoping is now a deliberate act
an administrator can take, undo, and see the record of.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine
from tests.test_sso_identity import principal

from resume_screener import events
from resume_screener.api import service
from resume_screener.ats.local_folder import save_req_meta
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.seed import seed_region_framework

REPO_ROOT = Path(__file__).resolve().parents[1]


def _as(role: str, email: str | None = None) -> dict:
    return {"X-MS-CLIENT-PRINCIPAL": principal(email or f"{role.lower()}@example.com", [role])}


@pytest.fixture()
def env(tmp_path, monkeypatch):
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

    for stem, title, location in (("eu-controller", "EU Controller", "EU"),
                                  ("india-analyst", "India Analyst", "IN")):
        jd = Path("jds") / f"{stem}.md"
        jd.write_text(f"# {title}\n\nOwns the numbers.", encoding="utf-8")
        save_req_meta(jd, icims_req_id=f"REQ-{stem}", job_location=location,
                      status="active", created_by="owner@example.com")

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

        def grants(self):
            return self.client.get("/api/access/grants", headers=_as("SuperAdmin")).json()

        def give(self, email, region, actor="SuperAdmin"):
            return self.client.post("/api/access/grants", headers=_as(actor),
                                    json={"email": email, "region": region})

        def take(self, email, region, actor="SuperAdmin"):
            return self.client.delete(f"/api/access/grants/{email}/{region}",
                                      headers=_as(actor))

        def reqs_for(self, email, role="HRHead"):
            """What this person's region lens lets them SEE.

            Read as an HR Head (who holds reqs_all) on purpose: a TA Specialist is
            also scoped to their OWN requisitions, and that second filter would
            mask the one these tests are about."""
            body = self.client.get("/api/roles", headers=_as(role, email)).json()
            return {r["req_id"] for r in body["roles"]}

    return Handle()


# --- the default this does NOT change --------------------------------------------


def test_someone_nobody_has_scoped_still_sees_everything(env):
    """The trap this endpoint exists to open, left exactly as it was: a recruiter
    joining a global team must not need a second manual step to see their work."""
    env.seed()
    assert env.reqs_for("newcomer@example.com") == {"eu-controller", "india-analyst"}
    person = next(p for p in env.grants()["people"]
                  if p["email"] == "newcomer@example.com") \
        if any(p["email"] == "newcomer@example.com" for p in env.grants()["people"]) else None
    if person:
        assert person["unscoped"] is True


# --- granting and taking back ------------------------------------------------------


def test_a_grant_narrows_what_that_person_sees(env):
    env.seed()
    assert env.reqs_for("bhavika@example.com") == {"eu-controller", "india-analyst"}

    given = env.give("bhavika@example.com", "EMEA")
    assert given.status_code == 200, given.text
    assert env.reqs_for("bhavika@example.com") == {"eu-controller"}


def test_removing_the_last_region_says_it_widened_them_again(env):
    """Quietly widening someone's access is exactly the surprise this should not
    spring, so the response says so and the page can warn first."""
    env.seed()
    env.give("bhavika@example.com", "EMEA")
    taken = env.take("bhavika@example.com", "EMEA")
    assert taken.status_code == 200, taken.text
    assert taken.json()["now_unscoped"] is True
    assert env.reqs_for("bhavika@example.com") == {"eu-controller", "india-analyst"}


def test_all_regions_replaces_scoped_grants(env):
    """A NULL row reads as "all", so holding both would be all-regions with dead
    rows beside it — a state nobody chose and nobody could read off the page."""
    env.seed()
    env.give("bhavika@example.com", "EMEA")
    env.give("bhavika@example.com", None)
    person = next(p for p in env.grants()["people"] if p["email"] == "bhavika@example.com")
    assert person["all_regions"] is True
    assert person["regions"] == []


def test_scoping_someone_who_already_sees_everything_is_refused_with_a_reason(env):
    env.seed()
    env.give("bhavika@example.com", None)
    clash = env.give("bhavika@example.com", "EMEA")
    assert clash.status_code == 409
    assert "every region" in clash.json()["detail"]


def test_an_unknown_region_is_refused(env):
    env.seed()
    assert env.give("bhavika@example.com", "ZZ").status_code == 400


def test_a_malformed_address_is_refused(env):
    env.seed()
    assert env.give("not-an-address", "EMEA").status_code == 400


def test_taking_back_something_they_never_had_is_a_404(env):
    env.seed()
    assert env.take("stranger@example.com", "EMEA").status_code == 404


# --- a grant is access, never capability -------------------------------------------


def test_granting_a_region_to_a_non_recruiter_grants_them_nothing(env):
    """Region and capability are two axes. The grant's `role` column is one
    constant on purpose, so it can never read as a second source of permission."""
    env.seed()
    env.give("hm@example.com", "EMEA")
    listed = env.client.get("/api/roles", headers=_as("HiringManager", "hm@example.com"))
    # a hiring manager sees only what they are assigned — a region grant adds nothing
    assert listed.status_code in (200, 403)
    if listed.status_code == 200:
        assert listed.json()["roles"] == []


# --- who may do this ----------------------------------------------------------------


def test_only_the_super_admin_grants_regions(env):
    env.seed()
    assert env.give("bhavika@example.com", "EMEA", actor="HRHead").status_code == 403
    assert env.take("bhavika@example.com", "EMEA", actor="HRHead").status_code == 403
    assert env.client.get("/api/access/grants", headers=_as("HRHead")).status_code == 403


# --- it never 500s ------------------------------------------------------------------


def test_an_unseeded_deployment_explains_itself_instead_of_failing(env):
    body = env.grants()
    assert body["status"] == "unseeded"
    assert "isn't switched on" in body["message"]
    assert body["people"] == []
    # and a write says the same thing rather than erroring obscurely
    assert env.give("bhavika@example.com", "EMEA").status_code == 409


def test_an_unreachable_database_is_a_state_not_a_crash(env, monkeypatch):
    def boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(session_mod, "_maker", boom)
    service.invalidate_region_context()
    body = env.grants()
    assert body["status"] == "unavailable"
    assert "database isn't reachable" in body["message"]
    assert env.give("bhavika@example.com", "EMEA").status_code == 503


# --- the record ----------------------------------------------------------------------


def test_every_change_lands_on_the_event_spine_in_plain_words(env):
    env.seed()
    env.give("bhavika@example.com", "EMEA")
    env.take("bhavika@example.com", "EMEA")

    kinds = [e["kind"] for e in events.load_events()]
    assert "region_granted" in kinds and "region_revoked" in kinds
    granted = next(e for e in events.load_events() if e["kind"] == "region_granted")
    assert "Europe, Middle East & Africa" in granted["summary"]
    # and the person is told
    notes = [e for e in events.load_events() if e["kind"] == "notify"]
    assert any(n["data"]["to"] == "bhavika@example.com" for n in notes)
