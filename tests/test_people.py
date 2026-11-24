"""The people directory + the mail seam (2026-09-06).

What these pin:
  * a sign-in name is REMEMBERED and turns the UPN into a person on every surface
    that resolves names (names.display_name), without a second write per request;
  * a typed HM name never overwrites a signed-in one; Graph overwrites both;
  * search finds people by name fragments and by address, local first, and the
    Graph seam degrades to local with a stated reason (never a 500);
  * an @-mention of someone outside the app is EMAILED — queued when mail is not
    configured, with the intent on the event spine, and never with the résumé
    attached unless the flag says so.
Offline: tmp_path + the synthetic SSO principals from test_sso_identity.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from resume_screener import mailer, names, people
from tests.test_sso_identity import _as, principal

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    import shutil

    for var in ("DEV_USER", "DEV_ROLE", "DATA_ROOT", "PEOPLE_DIRECTORY", "MAIL_SENDER"):
        monkeypatch.delenv(var, raising=False)
    shutil.copyfile(REPO_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "policies").mkdir()
    for pack in sorted((REPO_ROOT / "policies").glob("*.yaml")):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    shutil.copyfile(REPO_ROOT / "signals" / "features.yaml",
                    tmp_path / "signals" / "features.yaml")
    monkeypatch.chdir(tmp_path)
    people._GRAPH_STATE.update(ok=None, reason="")
    people._graph_singleton = None
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


# --- the store ---------------------------------------------------------------------


def test_remember_writes_once_and_names_resolve(workspace):
    log = workspace / "signals" / "people.jsonl"
    rec = people.remember("U120729@example.com", name="Shashank Srivastava")
    assert rec["email"] == "u120729@example.com"  # keyed lower-case
    assert log.read_text(encoding="utf-8").count("\n") == 1
    # the same fact again costs no write
    people.remember("u120729@example.com", name="Shashank Srivastava")
    assert log.read_text(encoding="utf-8").count("\n") == 1
    # every surface that resolves names now says the person, not the payroll id
    assert names.display_name("u120729@example.com") == "Shashank Srivastava"
    assert names.first_name("u120729@example.com") == "Shashank"
    # and nobody the directory has never met still humanises as before
    assert names.display_name("arjun.test@example.com") == "Arjun Test"


def test_typed_name_never_overwrites_a_signed_in_one(workspace):
    people.remember("bhavika@example.com", name="Bhavika S", source="sso")
    people.remember("bhavika@example.com", name="Bhavika (HM)", source="typed")
    assert people.display_name("bhavika@example.com") == "Bhavika S"
    # ...but Graph, the directory of record, does — and adds the title
    people.remember("bhavika@example.com", name="Bhavika Sharma", job_title="Finance Director",
                    source="graph")
    rec = people.lookup("bhavika@example.com")
    assert (rec["name"], rec["job_title"], rec["source"]) == ("Bhavika Sharma", "Finance Director", "graph")


def test_an_address_alone_teaches_nothing(workspace):
    assert people.remember("nobody@example.com") is None
    assert not (workspace / "signals" / "people.jsonl").exists()


def test_initials_and_hue_are_stable():
    assert people.initials("Shashank Srivastava") == "SS"
    assert people.initials("", "u120729@example.com") == "U"
    assert people.initials("Cher") == "CH"
    assert people.avatar_hue("a@b.com") == people.avatar_hue("A@B.COM")
    assert people.avatar_hue("a@b.com") in people.AVATAR_HUES
    assert people.name_shaped("arjun.test@example.com")
    assert not people.name_shaped("u120729@example.com")


# --- search -------------------------------------------------------------------------


def test_local_search_matches_name_fragments_and_addresses(workspace):
    people.remember("shashank_srivastava@example.com", name="Shashank Srivastava", source="sso")
    people.remember("bhavika.lillis@example.com", name="Bhavika Lillis", source="typed")
    hits = people.search("sriv")["people"]
    assert [h["email"] for h in hits] == ["shashank_srivastava@example.com"]
    assert hits[0]["initials"] == "SS" and hits[0]["hue"] in people.AVATAR_HUES
    assert [h["name"] for h in people.search("bhavika lillis")["people"]] == ["Bhavika Lillis"]
    assert [h["name"] for h in people.search("bhavika.lillis@")["people"]] == ["Bhavika Lillis"]
    assert people.search("")["people"] == []
    assert people.search("zzz")["source"] == "local"


def test_graph_seam_degrades_to_local_with_a_reason(workspace, monkeypatch):
    monkeypatch.setenv("PEOPLE_DIRECTORY", "graph")
    people.remember("local.person@example.com", name="Local Person", source="sso")

    def refused(url, headers):
        raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs=None, fp=None)

    monkeypatch.setattr(people, "_graph_singleton",
                        people.GraphDirectory(token_provider=lambda: "t", fetch=refused))
    result = people.search("local")
    assert [p["email"] for p in result["people"]] == ["local.person@example.com"]
    assert result["source"] == "local"
    assert "User.Read.All" in result["note"]
    assert people.status()["graph"] == {"enabled": True, "ok": False, "reason": result["note"]}


def test_graph_hits_are_merged_and_remembered(workspace, monkeypatch):
    monkeypatch.setenv("PEOPLE_DIRECTORY", "graph")
    seen: list[str] = []

    def fake_fetch(url, headers):
        seen.append(url)
        assert headers["Authorization"] == "Bearer tok"
        assert headers.get("ConsistencyLevel") == "eventual"
        return json.dumps({"value": [
            {"displayName": "Gerard Branaola", "jobTitle": "Sales Director",
             "mail": "Gerard_Branaola@example.com", "userPrincipalName": "u1@example.com"},
        ]}).encode()

    monkeypatch.setattr(people, "_graph_singleton",
                        people.GraphDirectory(token_provider=lambda: "tok", fetch=fake_fetch))
    result = people.search("rahul")
    assert result["source"] == "graph"
    [hit] = result["people"]
    assert (hit["email"], hit["name"], hit["job_title"]) == ("gerard_branaola@example.com", "Gerard Branaola", "Sales Director")
    # remembered: the next lookup is local and needs no Graph call
    monkeypatch.delenv("PEOPLE_DIRECTORY")
    assert people.display_name("gerard_branaola@example.com") == "Gerard Branaola"
    assert "$search=" in seen[0] and "$select=displayName,jobTitle" in seen[0]


# --- the API -----------------------------------------------------------------------


def test_me_remembers_the_sign_in_name_and_serves_the_avatar(client):
    hdr = {"X-MS-CLIENT-PRINCIPAL": principal("u120729@example.com", ["TASpecialist"],
                                              name="Shashank Srivastava")}
    me = client.get("/api/me", headers=hdr).json()
    assert me["name"] == "Shashank Srivastava"
    assert me["initials"] == "SS"
    assert me["avatar_hue"] in people.AVATAR_HUES
    assert me["job_title"] == ""  # nothing to fill it from until Graph is consented
    assert me["photo"] is None
    # the directory now knows this person — every surface resolves them
    assert people.display_name("u120729@example.com") == "Shashank Srivastava"
    rows = client.get("/api/people/directory", headers=hdr).json()
    assert rows["source"] == "local"
    assert [r["name"] for r in rows["people"]] == ["Shashank Srivastava"]
    hits = client.get("/api/people?q=shash", headers=hdr).json()["people"]
    assert hits[0]["email"] == "u120729@example.com"


def test_people_lookup_is_gated(client):
    # a principal with no known role gets the app's standard 403, never data
    hdr = {"X-MS-CLIENT-PRINCIPAL": principal("nobody@example.com", [])}
    assert client.get("/api/people?q=a", headers=hdr).status_code == 403


def test_typed_hm_name_is_remembered_at_creation(client):
    r = client.post("/api/roles", headers=_as("TASpecialist"), json={
        "title": "Finance Analyst", "jd_text": "Owns treasury accounting.",
        "job_location": "IN", "icims_req_id": "IN-901",
        "hm_name": "Bhavika Lillis", "hm_email": "bhavika_lillis.test@example.com",
    })
    assert r.status_code == 200, r.text
    rec = people.lookup("bhavika_lillis.test@example.com")
    assert (rec["name"], rec["source"]) == ("Bhavika Lillis", "typed")


def test_locations_carry_the_pack_duties_in_plain_words(client):
    feed = client.get("/api/locations", headers=_as("TASpecialist")).json()
    [india] = [loc for loc in feed["packs"] if loc["code"] == "IN"]
    assert any("DPDP" in o for o in india["obligations"])
    assert "caste" in india["notes"].lower()
    assert india["keep_at_least_days"] == 1095


def test_dashboard_metrics_carry_my_own_screened_total(client):
    payload = client.get("/api/roles", headers=_as("TASpecialist")).json()
    assert payload["metrics"]["my_candidates_screened"] == 0
    assert "candidates_screened" in payload["metrics"]  # the all-reqs total stays


# --- the mail seam -------------------------------------------------------------------


def test_unconfigured_mail_is_queued_and_on_the_spine(workspace):
    from resume_screener.events import load_events

    out = mailer.send("outside@example.com", "Subject", "Body", actor="me@example.com",
                      req_id="r1", kind="comment_mention_email",
                      attachments=[{"name": "cv.pdf", "bytes": b"%PDF", "content_type": "application/pdf"}])
    assert out == {"delivered": False, "queued": True,
                   "reason": "email delivery is not configured (MAIL_SENDER unset)"}
    [q] = mailer.queued()
    assert q["to"] == "outside@example.com" and q["attachments"] == ["cv.pdf"]
    assert "%PDF" not in (workspace / "signals" / "outbox.jsonl").read_text(encoding="utf-8")
    [ev] = load_events(kinds=["notify"])
    assert ev["data"]["channel"] == "email" and ev["data"]["delivered"] is False
    assert ev["data"]["to"] == "outside@example.com"


def test_configured_mail_sends_through_graph(workspace, monkeypatch):
    monkeypatch.setenv("MAIL_SENDER", "resume-screener@example.com")
    sent: list[dict] = []

    def fake_post(url, headers, body):
        assert url.endswith("/users/resume-screener@example.com/sendMail")
        sent.append(json.loads(body))
        return 202

    out = mailer.send("to@example.com", "Hi", "Text", actor="a@b.com",
                      transport=lambda msg: mailer.graph_send(msg, token_provider=lambda: "tok", post=fake_post))
    assert out["delivered"] is True and out["queued"] is False
    assert sent[0]["message"]["toRecipients"] == [{"emailAddress": {"address": "to@example.com"}}]
    assert mailer.queued() == []


def test_a_refused_send_falls_back_to_the_queue(workspace, monkeypatch):
    monkeypatch.setenv("MAIL_SENDER", "resume-screener@example.com")

    def refused(url, headers, body):
        raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs=None, fp=None)

    out = mailer.send("to@example.com", "Hi", "Text", actor="a@b.com",
                      transport=lambda msg: mailer.graph_send(msg, token_provider=lambda: "tok", post=refused))
    assert out["delivered"] is False and out["queued"] is True
    assert "Mail.Send" in out["reason"]


# --- @-mentions reach people outside the app -----------------------------------------


@pytest.fixture()
def hm_client(workspace, monkeypatch):
    """The HM-share workspace (a screened req with attached résumés) under the
    people-directory fixture's clean env."""
    import shutil

    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    monkeypatch.setenv("FEATURE_HM_PORTAL", "1")
    shutil.copyfile(REPO_ROOT / "signals" / "scoring_model.v1.yaml",
                    workspace / "signals" / "scoring_model.v1.yaml")
    return TestClient(app)


def test_mentioning_an_outsider_emails_them_and_leaves_the_resume_out(hm_client, workspace):
    from resume_screener.events import load_events
    from tests.test_hm_share import _mk_screened_role

    client = hm_client
    stem, ids = _mk_screened_role()
    # one person the app knows (signed in once), one it has never seen
    people.remember("known@example.com", name="Known Person", source="sso")
    r = client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments",
                    headers=_as("TASpecialist"),
                    json={"text": "@Known Person @Outside Person what do you think?",
                          "mentions": ["known@example.com", "outside@example.com"]})
    assert r.status_code == 200, r.text
    # the known person gets the bell only; the outsider gets an EMAIL (queued here)
    [q] = mailer.queued()
    assert q["to"] == "outside@example.com"
    assert "REQ-finance-analyst" in q["subject"] and "Finance Analyst" in q["subject"]
    assert "what do you think?" in q["body"] and f"/reqs/{stem}/review?focus=comment:" in q["body"]
    assert q["attachments"] == []  # the résumé travels only when the flag says so
    emailed = [e for e in load_events(kinds=["notify"]) if e["data"].get("channel") == "email"]
    assert [e["data"]["to"] for e in emailed] == ["outside@example.com"]
    bells = [e for e in load_events(kinds=["notify"]) if e["data"].get("kind") == "comment_mention"]
    assert sorted(e["data"]["to"] for e in bells) == ["known@example.com", "outside@example.com"]


def test_resume_attaches_only_when_the_flag_is_on(hm_client, workspace, monkeypatch):
    from resume_screener import feature_flags
    from tests.test_hm_share import _mk_screened_role

    client = hm_client
    stem, ids = _mk_screened_role()
    # The résumé is reached through the private id→file map the run writes (the
    # shared fixture skips it) — the same bridge the download route uses.
    private = Path("output") / stem / "private"
    private.mkdir(parents=True, exist_ok=True)
    (private / "candidate_map.json").write_text(json.dumps(
        {ids[0]: str((Path("data/roles") / stem / "resumes" / "resume-0.pdf").resolve())}),
        encoding="utf-8")
    monkeypatch.setattr(feature_flags, "flag_enabled",
                        lambda key, region=None: key == "mention_email_attach_resume")
    r = client.post(f"/api/roles/{stem}/candidates/{ids[0]}/comments",
                    headers=_as("TASpecialist"),
                    json={"text": "@Outside have a look", "mentions": ["outside@example.com"]})
    assert r.status_code == 200, r.text
    [q] = mailer.queued()
    assert q["attachments"] == ["resume-0.pdf"]
