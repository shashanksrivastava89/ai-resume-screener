"""P38: hard per-region access enforcement (10-DATA-MODEL §6, deferred from P27).

A region-scoped user sees only their region's requisitions and 403s on anything
outside the grant; an all-regions grant is unrestricted; and — the critical
degradation pin — an UNSEEDED region framework leaves everything visible with no
403s, so local dev is byte-identical to before enforcement existed.

Offline: a tmp_path workspace (config + packs + jds) plus an in-memory SQLite
region framework, both monkeypatched under the request path.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker
from tests.dbharness import make_test_engine
from tests.test_sso_identity import principal

from resume_screener.api import service
from resume_screener.ats.local_folder import save_req_meta
from resume_screener.db import engine as engine_mod
from resume_screener.db import session as session_mod
from resume_screener.db.repositories import identity as identity_repo
from resume_screener.db.seed import seed_region_framework

REPO_ROOT = Path(__file__).resolve().parents[1]


def _workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)


def _mk_role(stem, title, location):
    jd = Path("jds") / f"{stem}.md"
    jd.write_text(f"# {title}\n\nOwns the numbers.", encoding="utf-8")
    save_req_meta(jd, icims_req_id=f"REQ-{stem}", job_location=location,
                  status="active", created_by="taspecialist@example.com")
    return stem


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A workspace with two requisitions (one EU=EMEA, one IN=IN) and a seeded
    in-memory region framework wired under the request path. Returns a small
    handle for granting region roles + a TestClient."""
    _workspace(tmp_path, monkeypatch)
    _mk_role("eu-controller", "EU Controller", "EU")
    _mk_role("india-analyst", "India Analyst", "IN")

    eng = make_test_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(engine_mod, "get_engine", lambda url=None: eng)
    monkeypatch.setattr(session_mod, "_maker", lambda: maker)

    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    class Handle:
        def __init__(self):
            self.client = TestClient(app)
            self.engine = eng

        def seed(self):
            with session_mod.session_scope() as s:
                seed_region_framework(s)  # no dev grant → grants are explicit below

        def grant(self, email, role, region_code):
            with session_mod.session_scope() as s:
                identity_repo.grant_role(s, email, role, region_code=region_code)

    handle = Handle()
    try:
        yield handle
    finally:
        eng.dispose()


def _headers(email, role="HRHead"):
    # HRHead holds reqs_all (2026-07-19), so these tests keep probing REGION
    # access in isolation — the own-req recruiter lens is covered in test_rbac.
    return {"X-MS-CLIENT-PRINCIPAL": principal(email, [role])}


# --- scoped grant: see only your region, 403 outside it -----------------------------


def test_scoped_user_sees_only_their_region(env):
    env.seed()
    env.grant("emea.rec@example.com", "TASpecialist", "EMEA")
    headers = _headers("emea.rec@example.com")

    roles = env.client.get("/api/roles", headers=headers).json()["roles"]
    ids = {r["req_id"] for r in roles}
    assert ids == {"eu-controller"}  # the IN req is hidden


def test_scoped_user_403s_on_out_of_region_req(env):
    env.seed()
    env.grant("emea.rec@example.com", "TASpecialist", "EMEA")
    headers = _headers("emea.rec@example.com")

    # in-region: reachable
    assert env.client.get("/api/roles/eu-controller", headers=headers).status_code == 200
    # out-of-region: a plain 403 through the per-req guard
    out = env.client.get("/api/roles/india-analyst", headers=headers)
    assert out.status_code == 403
    assert "region you don't have access to" in out.json()["detail"]


def test_scoped_user_403s_on_out_of_region_mutation(env):
    env.seed()
    env.grant("emea.rec@example.com", "TASpecialist", "EMEA")
    headers = _headers("emea.rec@example.com")
    # a per-req mutation (snooze) is guarded the same way as the reads
    out = env.client.post("/api/roles/india-analyst/snooze", json={"days": 10}, headers=headers)
    assert out.status_code == 403


def test_region_query_lens_narrows_within_the_grant(env):
    env.seed()
    # an all-regions user picks a lens; the ?region= param narrows the list
    env.grant("global@example.com", "TASpecialist", None)
    headers = _headers("global@example.com")
    roles = env.client.get("/api/roles?region=IN", headers=headers).json()["roles"]
    assert {r["req_id"] for r in roles} == {"india-analyst"}


# --- all-regions grant: unrestricted ------------------------------------------------


def test_all_regions_grant_is_unrestricted(env):
    env.seed()
    env.grant("global@example.com", "TASpecialist", None)  # region_id NULL = all
    headers = _headers("global@example.com")
    roles = env.client.get("/api/roles", headers=headers).json()["roles"]
    assert {r["req_id"] for r in roles} == {"eu-controller", "india-analyst"}
    assert env.client.get("/api/roles/india-analyst", headers=headers).status_code == 200


# --- the degradation pin: unseeded framework = everything visible, no 403s ----------


def test_unseeded_framework_leaves_everything_visible(env):
    # deliberately do NOT seed the region framework (tables exist, no rows)
    headers = _headers("nobody@example.com")
    roles = env.client.get("/api/roles", headers=headers).json()["roles"]
    assert {r["req_id"] for r in roles} == {"eu-controller", "india-analyst"}
    # and no per-req 403s
    assert env.client.get("/api/roles/india-analyst", headers=headers).status_code == 200
    assert env.client.get("/api/roles/eu-controller", headers=headers).status_code == 200


# --- your own work is yours, whatever the lens says (pilot incident 2026-08-27) --------
#
# A TA Specialist created a Gurugram requisition from the careers bridge and was then
# locked out of it: "Couldn't auto-draft (This requisition belongs to a region you don't
# have access to.)", and the requisition never appeared on their dashboard. The cause
# was not the requisition — job_location was IN and correct — but region access: a
# signed-in user with a valid app role and NO grant rows resolves to an EMPTY set, so
# every requisition is denied.
#
# Being able to strand yourself by creating something is indefensible: you cannot ask
# for access to a thing you can no longer see. These pin the exemption AND its limit.


def _own_headers(email="taspecialist@example.com"):
    """The creator of both fixture requisitions, as an own-reqs-scoped specialist."""
    return {"X-MS-CLIENT-PRINCIPAL": principal(email, ["TASpecialist"])}


def test_a_user_with_no_grants_still_reaches_the_req_they_created(env):
    """The exact reported failure: role assigned, zero regions, own requisition."""
    env.seed()  # framework seeded, but NO grant for this user at all

    res = env.client.get("/api/roles/india-analyst", headers=_own_headers())
    assert res.status_code == 200, res.text
    assert res.json()["req_id"] == "india-analyst"


def test_a_user_with_no_grants_sees_their_own_reqs_on_the_dashboard(env):
    """An empty dashboard was indistinguishable from a locked one — and hid the very
    requisition they had just created."""
    env.seed()
    roles = env.client.get("/api/roles", headers=_own_headers()).json()["roles"]
    assert {r["req_id"] for r in roles} == {"eu-controller", "india-analyst"}


def test_the_exemption_does_not_widen_access_to_other_peoples_reqs(env):
    """The limit that keeps this an exemption rather than a hole: somebody else's
    requisition in a region you lack stays 403, and stays off your dashboard."""
    env.seed()
    env.grant("emea.rec@example.com", "TASpecialist", "EMEA")
    headers = _headers("emea.rec@example.com")  # EMEA grant, created neither req

    out = env.client.get("/api/roles/india-analyst", headers=headers)
    assert out.status_code == 403
    ids = {r["req_id"] for r in env.client.get("/api/roles", headers=headers).json()["roles"]}
    assert ids == {"eu-controller"}


def test_the_assigned_hiring_manager_gets_the_same_exemption(env):
    """Same reasoning as the creator: work assigned to you cannot be invisible to you.

    Asserted on region_allows directly, NOT through a route: a HiringManager's only
    capability is `hm_take`, which the dark-launched FEATURE_HM_PORTAL strips when off,
    so an HTTP probe here would be refused by the capability gate and would prove
    nothing about the region lens."""
    from resume_screener.api import service
    from resume_screener.models import Requisition

    env.seed()
    jd = Path("jds") / "india-analyst.md"
    save_req_meta(jd, hm={"email": "hm@example.com", "name": "Ravi", "status": None})
    req = Requisition(req_id="india-analyst", role_title="India Analyst",
                      jd_ref=str(jd), job_location="IN")

    assert service.region_allows(req, "hm@example.com") is True
    assert service.region_allows(req, "taspecialist@example.com") is True   # creator
    # ...and someone SCOPED to another region is still refused. It has to be a scoped
    # user: an account with no grants is unscoped-and-unrestricted (see
    # test_an_unconfigured_account_is_unscoped_not_locked_out), so an ungranted
    # stranger would pass here for a reason that has nothing to do with the exemption.
    env.grant("emea.only@example.com", "TASpecialist", "EMEA")
    assert service.region_allows(req, "emea.only@example.com") is False


def test_the_403_says_what_to_do_about_it(env):
    """A message naming a region the reader has never heard of is not actionable."""
    env.seed()
    env.grant("emea.rec@example.com", "TASpecialist", "EMEA")
    out = env.client.get("/api/roles/india-analyst",
                         headers=_headers("emea.rec@example.com"))
    assert out.status_code == 403
    assert "Ask IT" in out.json()["detail"]


def test_drafting_a_checklist_on_your_own_req_is_not_region_blocked(env, monkeypatch):
    """The reported symptom, at the route that produced it. The draft itself needs
    Azure, so this asserts it gets PAST the region guard rather than that it drafts."""
    env.seed()
    res = env.client.post("/api/roles/india-analyst/checklist/draft", headers=_own_headers())
    # Anything but the region 403 means the guard let it through.
    assert res.status_code != 403 or "region" not in res.text.lower(), res.text


def test_an_unconfigured_account_is_unscoped_not_locked_out(env):
    """No grant rows means NOT CONFIGURED, which is not the same as "allowed nothing"
    — and nothing else can mean it: a grant always carries either a region or NULL, so
    "deliberately allowed zero regions" is not expressible.

    This defaulted to deny-everything and there is no API, CLI or admin page that
    grants a region (only db seed, for the dev user), so every real SSO user landed
    here: an HR Head could not open anyone's requisition to approve a checklist, and a
    specialist saw an empty dashboard. A gate nobody can administer is a trap."""
    env.seed()  # framework seeded; NO grant for this user

    ctx = service.region_context("brand.new@example.com")
    assert ctx["all"] is True and ctx["ungranted"] is True
    assert service.accessible_regions("brand.new@example.com") is None

    # ...so an HR Head can reach requisitions they did not create, which is what
    # approvals require.
    headers = _headers("brand.new@example.com", "HRHead")
    assert env.client.get("/api/roles/india-analyst", headers=headers).status_code == 200
    ids = {r["req_id"] for r in env.client.get("/api/roles", headers=headers).json()["roles"]}
    assert ids == {"eu-controller", "india-analyst"}


def test_a_scoped_account_is_still_enforced(env):
    """The moment someone IS scoped, the lens bites again — that is the whole point of
    keeping enforcement rather than deleting it."""
    env.seed()
    env.grant("emea.rec@example.com", "TASpecialist", "EMEA")

    ctx = service.region_context("emea.rec@example.com")
    assert ctx["all"] is False and ctx["ungranted"] is False
    assert service.accessible_regions("emea.rec@example.com") == {"EMEA"}
