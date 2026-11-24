"""The Ask-Claude MCP server (Phase A: local stdio, read-only).

The guard pipeline is the point: feature flag → capabilities (the app's ONE
enforcement point) → row scope (region / own-req / HM assignment via
app._req_view_authorized) → redaction posture → audit. Tests call the plain
tool functions (SDK-version-proof); exactly one smoke test touches the SDK.
Identity is env-based (DEV_USER / DEV_ROLE), the same headerless path the
whole local CLI uses — set per test with monkeypatch.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest

from resume_screener.ats.local_folder import save_req_meta
from resume_screener.events import load_events
from resume_screener.models import candidate_id_for
from tests.test_sso_identity import _as

def _portal(on: bool) -> None:
    """Turn the hiring-manager portal on/off the way the app does (2026-09-07: it is
    an ordinary flag a SuperAdmin toggles, not FEATURE_HM_PORTAL any more)."""
    from resume_screener import feature_flags

    feature_flags.set_flag("hm_portal", None, on, author="sa@example.com")


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
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
    # AFTER the chdir, and after the features.yaml copy above: hm_portal became an
    # ordinary file-backed flag (2026-09-07) and set_flag resolves signals/ against
    # the CWD, so called any earlier this wrote the REPOSITORY'S features.yaml and
    # appended to its events.jsonl and audit_log.jsonl.
    _portal(True)
    return tmp_path


@pytest.fixture()
def client(workspace):
    from fastapi.testclient import TestClient

    from resume_screener.api.app import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _named_mode():
    """Every test starts in named mode; blind tests flip it themselves."""
    from resume_screener.api import mcp as mcp_mod

    mcp_mod._STATE["blind"] = False
    yield
    mcp_mod._STATE["blind"] = False


def _flag_on():
    from resume_screener import feature_flags

    feature_flags.set_flag("ask_claude_mcp", None, True, "test-setup")


def _flag_off():
    # The shipped default is mutable (the pilot may have toggled it on via the
    # UI), so flag-off tests set it explicitly rather than trusting the default.
    from resume_screener import feature_flags

    feature_flags.set_flag("ask_claude_mcp", None, False, "test-setup")


def _me(monkeypatch, role: str, user: str | None = None) -> str:
    email = user or f"{role.split(',')[0].lower()}@example.com"
    monkeypatch.setenv("DEV_USER", email)
    monkeypatch.setenv("DEV_ROLE", role)
    return email


CANDIDATE_FILE = "Anita Rao.pdf"


def _screened_role(client, stem="finance-analyst", hm=None,
                   created_by="taspecialist@example.com",
                   contact_docx=False) -> tuple[str, str]:
    """A role with a published checklist and one screened candidate whose id is
    the content hash of the attached résumé (same shape as test_handoff).
    contact_docx=True makes the résumé a real DOCX carrying an email + phone."""
    jd = Path("jds") / f"{stem}.md"
    jd.write_text("# Finance Analyst\n\nOwns treasury.", encoding="utf-8")
    meta = {"icims_req_id": f"REQ-{stem}", "job_location": "IN", "status": "active",
            "created_by": created_by}
    if hm:
        meta["hm"] = {"email": hm, "name": "Rahul", "status": "invited"}
    save_req_meta(jd, **meta)

    # SuperAdmin headers: setup must work for reqs owned by OTHER recruiters too.
    client.put(f"/api/roles/{stem}/rubric", headers=_as("SuperAdmin"), json={
        "requirements": [
            {"text": "Strong US GAAP experience", "kind": "must_have", "importance": 5},
            {"text": "CA qualification stated", "kind": "hard_gate", "importance": 3},
        ]})

    folder = Path(client.get(f"/api/roles/{stem}/batch",
                             headers=_as("SuperAdmin")).json()["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    if contact_docx:
        import docx

        doc = docx.Document()
        doc.add_paragraph("Anita Rao")
        doc.add_paragraph("Email: anita.rao@example.com · Phone: +91 98765 43210")
        doc.add_paragraph("Treasury accountant; led US GAAP reporting.")
        resume = folder / "Anita Rao.docx"
        doc.save(str(resume))
        data = resume.read_bytes()
    else:
        resume = folder / CANDIDATE_FILE
        data = b"Anita Rao resume bytes"
        resume.write_bytes(data)
    cid = candidate_id_for(data)

    out = Path("output") / stem
    (out / "private").mkdir(parents=True)
    (out / "private" / "candidate_map.json").write_text(
        json.dumps({cid: str(resume)}), encoding="utf-8")
    (out / "candidates").mkdir(parents=True)
    (out / "candidates" / f"{cid}.json").write_text(json.dumps({
        "score": {"candidate_id": cid, "role_title": "Finance Analyst", "jurisdiction": "IN",
                  "layer1": {"candidate_id": cid, "rubric_version": 1, "gate_flags": [],
                             "must_haves_missing": [], "coverage_score": 0.82, "base_score": 82.0,
                             "eligible": True, "reasons": []},
                  "layer2": None, "critic": None, "final_score": 82.0, "fit_band": "Strong",
                  "confidence": "high", "summary_rationale": "ok"},
        "findings": {"candidate_id": cid, "role_title": "Finance Analyst", "rubric_version": 1,
                     "findings": [
                         {"requirement_id": "r01", "requirement_text": "Strong US GAAP experience",
                          "match": "full", "evidence": "led US GAAP reporting for the India entity",
                          "note": ""},
                         {"requirement_id": "r02", "requirement_text": "CA qualification stated",
                          "match": "none", "evidence": None, "note": ""},
                     ]},
        "suspended_adjustments": [],
    }), encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps(
        {"run_id": "run-1", "jurisdiction": "IN", "counts": {"scored": 1}}), encoding="utf-8")
    return stem, cid


# --- 1. registry shape (the ONE SDK smoke test) --------------------------------------


def test_server_exposes_ten_tools_and_one_prompt(workspace):
    from resume_screener.api import mcp as mcp_mod

    server = mcp_mod.create_server()
    tools = asyncio.run(server.list_tools())
    assert sorted(t.name for t in tools) == sorted(n for _f, n in mcp_mod._TOOLS)
    assert len(tools) == 10
    prompts = asyncio.run(server.list_prompts())
    assert [p.name for p in prompts] == ["shortlist-review"]


# --- 2. feature-flag gate -------------------------------------------------------------


def test_every_tool_refuses_while_the_flag_is_off(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _me(monkeypatch, "SuperAdmin")
    _screened_role(client)
    _flag_off()
    for fn, name in mcp_mod._TOOLS:
        kwargs = {}
        if name in ("requisition_overview", "review_shortlist", "shortlist_chart",
                    "ask_claude_bundle", "screening_runs"):
            kwargs = {"req_id": "finance-analyst"}
        elif name in ("candidate_detail", "candidate_contact"):
            kwargs = {"req_id": "finance-analyst", "rank": 1}
        result = fn(**kwargs)
        assert result == {"error": mcp_mod.FLAG_OFF_MESSAGE}, name


def test_run_stdio_exits_when_the_flag_is_off(workspace):
    from resume_screener.api import mcp as mcp_mod

    _flag_off()
    with pytest.raises(SystemExit) as exc:
        mcp_mod.run_stdio()
    assert exc.value.code == 2


# --- 3. capability denial matrix -------------------------------------------------------


def test_ta_specialist_reaches_review_but_not_audit_or_full_model(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    _screened_role(client)
    _me(monkeypatch, "TASpecialist")
    assert "requisitions" in mcp_mod.list_requisitions()
    assert mcp_mod.review_shortlist("finance-analyst")["ready"] is True
    assert mcp_mod.how_scoring_works()["version"] >= 0                 # summary ok
    assert mcp_mod.how_scoring_works("full") == {"error": mcp_mod.RULES_DENIED_MESSAGE}
    assert mcp_mod.audit_timeline() == {"error": mcp_mod.AUDIT_DENIED_MESSAGE}


def test_legal_gets_the_model_and_audit_but_never_the_shortlist(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    _screened_role(client)
    _me(monkeypatch, "Legal")
    full = mcp_mod.how_scoring_works("full")
    assert "never_scored_topics" in full and "signals" in full
    assert "items" in mcp_mod.audit_timeline()
    assert mcp_mod.review_shortlist("finance-analyst") == {
        "error": mcp_mod.REQS_DENIED_MESSAGE}
    assert mcp_mod.candidate_contact("finance-analyst", 1) == {
        "error": mcp_mod.RECRUITER_ONLY_MESSAGE}


def test_hm_with_portal_off_and_unknown_role_get_the_setup_message(client, monkeypatch):
    from resume_screener.api import app as app_module
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    _screened_role(client)
    _portal(False)   # hm_take suppressed -> zero caps
    _me(monkeypatch, "HiringManager")
    assert mcp_mod.list_requisitions() == {"error": app_module._NO_ACCESS}
    monkeypatch.setenv("DEV_ROLE", "NobodyKnown")
    assert mcp_mod.list_requisitions() == {"error": app_module._NO_ACCESS}


# --- 4. own-req scoping (the contextvar shim works outside HTTP) -----------------------


def test_own_req_scoping_hides_and_refuses_another_recruiters_req(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    _screened_role(client, stem="mine", created_by="taspecialist@example.com")
    _screened_role(client, stem="theirs", created_by="other.ta@example.com")
    _me(monkeypatch, "TASpecialist")   # recruiter WITHOUT reqs_all

    listed = {r["req_id"] for r in mcp_mod.list_requisitions()["requisitions"]}
    assert "mine" in listed and "theirs" not in listed
    refusal = mcp_mod.requisition_overview("theirs")
    assert "created by another recruiter" in refusal["error"]
    # every req-scoped tool flows through the same guard
    assert "error" in mcp_mod.review_shortlist("theirs")
    assert "error" in mcp_mod.ask_claude_bundle("theirs")
    # the HR Head (reqs_all) still sees both
    _me(monkeypatch, "HRHead")
    listed = {r["req_id"] for r in mcp_mod.list_requisitions()["requisitions"]}
    assert {"mine", "theirs"} <= listed


# --- 5. HM scoping ---------------------------------------------------------------------


def test_hm_only_caller_reaches_only_their_assigned_req(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    hm_email = "hiringmanager@example.com"
    _screened_role(client, stem="assigned", hm=hm_email)
    _screened_role(client, stem="not-mine")
    _me(monkeypatch, "HiringManager", user=hm_email)

    listed = {r["req_id"] for r in mcp_mod.list_requisitions()["requisitions"]}
    assert listed == {"assigned"}
    assert "isn't assigned to you" in mcp_mod.requisition_overview("not-mine")["error"]
    # nothing has been SHARED with the HM yet, so ranks resolve to nothing —
    # the snapshot semantics hold through MCP too.
    detail = mcp_mod.candidate_detail("assigned", 1)
    assert "No candidate at rank 1" in detail["error"]


# --- 6. blind mode: the pinned no-PII contract ------------------------------------------


def test_blind_mode_outputs_carry_no_pii(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    stem, cid = _screened_role(client)
    _me(monkeypatch, "TASpecialist")
    mcp_mod._STATE["blind"] = True

    for payload in (mcp_mod.review_shortlist(stem),
                    mcp_mod.candidate_detail(stem, 1),
                    mcp_mod.ask_claude_bundle(stem)):
        text = json.dumps(payload, ensure_ascii=False)
        assert "error" not in payload
        assert "Anita Rao" not in text
        assert "@" not in text
        assert cid not in text
        assert not re.search(r"\b[0-9a-f]{16}\b", text)
        assert CANDIDATE_FILE not in text and ".pdf" not in text
    # rank labels: the list + bundle number candidates; the drawer says
    # "This candidate" (the app's own blind wording).
    assert "Candidate 1" in json.dumps(mcp_mod.review_shortlist(stem))
    assert "Candidate 1" in mcp_mod.ask_claude_bundle(stem)["text"]

    assert mcp_mod.candidate_contact(stem, 1) == {
        "error": mcp_mod.BLIND_CONTACT_MESSAGE}
    _me(monkeypatch, "HRHead")
    assert mcp_mod.audit_timeline() == {"error": mcp_mod.BLIND_AUDIT_MESSAGE}


# --- 7. named default + contact extraction ----------------------------------------------


def test_named_mode_shows_names_and_contact_returns_only_matches(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    stem, _cid = _screened_role(client, stem="contact-role", contact_docx=True)
    _me(monkeypatch, "TASpecialist")

    cards = mcp_mod.review_shortlist(stem)["candidates"]
    assert cards[0]["name"] == "Anita Rao"

    contact = mcp_mod.candidate_contact(stem, 1)
    assert contact["name"] == "Anita Rao"
    assert contact["emails"] == ["anita.rao@example.com"]
    assert any("98765" in p for p in contact["phones"])
    # ONLY the matches — never the résumé text
    text = json.dumps(contact, ensure_ascii=False)
    assert "Treasury accountant" not in text and "US GAAP" not in text


# --- 8. visual: deep links, score bars/badges, rendered chart --------------------------


def test_results_carry_deep_links_and_score_visuals(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    stem, _cid = _screened_role(client)
    _me(monkeypatch, "TASpecialist")

    reqs = mcp_mod.list_requisitions()
    assert reqs["requisitions"][0]["url"] == f"http://localhost:8000/reqs/{stem}"

    shortlist = mcp_mod.review_shortlist(stem)
    assert shortlist["url"] == f"http://localhost:8000/reqs/{stem}/review"
    card = shortlist["candidates"][0]
    assert "█" in card["score_bar"] and "82" in card["score_bar"]
    assert card["band_badge"].startswith("🟢")

    detail = mcp_mod.candidate_detail(stem, 1)
    assert "█" in detail["score_bar"]
    assert detail["url"].endswith(f"/reqs/{stem}/review")

    _me(monkeypatch, "HRHead")
    assert mcp_mod.how_scoring_works()["url"] == "http://localhost:8000/scoring"
    assert mcp_mod.audit_timeline(req_id=stem)["url"] == \
        f"http://localhost:8000/audit?req={stem}"


def test_app_base_url_is_overridable(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    stem, _cid = _screened_role(client)
    _me(monkeypatch, "TASpecialist")
    monkeypatch.setenv("APP_BASE_URL", "https://screener.internal/")   # trailing slash
    assert mcp_mod.list_requisitions()["requisitions"][0]["url"] == \
        f"https://screener.internal/reqs/{stem}"


def test_shortlist_chart_returns_a_png_image(client, monkeypatch):
    from mcp.server.fastmcp import Image

    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    stem, _cid = _screened_role(client)
    _me(monkeypatch, "TASpecialist")

    img = mcp_mod.shortlist_chart(stem)
    assert isinstance(img, Image)
    assert img.data.startswith(b"\x89PNG\r\n\x1a\n")   # PNG magic
    # blind mode still renders (labels come from the redacted cards)
    mcp_mod._STATE["blind"] = True
    assert isinstance(mcp_mod.shortlist_chart(stem), Image)
    # audited like every other call
    assert any(e["data"]["tool"] == "shortlist_chart"
               for e in load_events(req_id=stem, kinds=["mcp_tool_call"]))


# --- 9. every call lands on the audit spine ---------------------------------------------


def test_calls_are_audited_on_the_event_spine(client, monkeypatch):
    from resume_screener.api import mcp as mcp_mod

    _flag_on()
    stem, _cid = _screened_role(client, contact_docx=True)
    user = _me(monkeypatch, "TASpecialist")

    mcp_mod.review_shortlist(stem)
    calls = load_events(req_id=stem, kinds=["mcp_tool_call"])
    assert calls and calls[-1]["actor"] == user
    assert calls[-1]["data"]["tool"] == "review_shortlist"
    assert "logged automatically" in calls[-1]["summary"]

    mcp_mod.ask_claude_bundle(stem)
    assert load_events(req_id=stem, kinds=["handoff_copied"])

    mcp_mod.candidate_contact(stem, 1)
    contact_events = load_events(req_id=stem, kinds=["contact_viewed"])
    assert contact_events and "Anita Rao" in contact_events[-1]["summary"]

    _me(monkeypatch, "HRHead")
    mcp_mod.audit_timeline(req_id=stem)
    views = load_events(kinds=["audit_view"])
    assert views and "Ask Claude" in views[-1]["summary"]

    # a global (unscoped) tool lands unscoped
    mcp_mod.list_requisitions()
    unscoped = [e for e in load_events(kinds=["mcp_tool_call"]) if "req_id" not in e]
    assert any(e["data"]["tool"] == "list_requisitions" for e in unscoped)
