"""Security headers, the API's own description, and the server's name (2026-10-14).

Every response tells the browser not to sniff types, where it may be framed, what to
send as a referrer, and to keep to HTTPS; HTML pages carry the Content-Security-Policy.
The OpenAPI description and its explorer answer only in APP_MODE=dev, and the server
no longer names its software. Offline.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from starlette.responses import HTMLResponse, Response

from resume_screener.api import security_headers

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("DEV_USER", raising=False)
    monkeypatch.delenv("DEV_ROLE", raising=False)
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.delenv("CSP_REPORT_ONLY", raising=False)
    for name in ("config.yaml", "rbac.yaml"):
        shutil.copyfile(REPO_ROOT / name, tmp_path / name)
    (tmp_path / "policies").mkdir()
    for pack in (REPO_ROOT / "policies").glob("*.yaml"):
        shutil.copyfile(pack, tmp_path / "policies" / pack.name)
    (tmp_path / "jds").mkdir()
    (tmp_path / "signals").mkdir()
    for y in (REPO_ROOT / "signals").glob("*.yaml"):
        shutil.copyfile(y, tmp_path / "signals" / y.name)
    monkeypatch.chdir(tmp_path)

    from resume_screener.api.app import app

    return TestClient(app)


def _assert_baseline(headers) -> None:
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"  # not DENY: the résumé preview
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in headers["Permissions-Policy"]
    assert headers["Strict-Transport-Security"].startswith("max-age=")


def test_every_response_carries_the_baseline(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    _assert_baseline(r.headers)
    assert "Content-Security-Policy" not in r.headers  # JSON: a policy protects nothing


def test_a_revalidated_poll_keeps_them(client):
    first = client.get("/api/notifications")
    again = client.get("/api/notifications", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304
    _assert_baseline(again.headers)


def test_the_sign_in_page_carries_the_policy_and_no_inline_style(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "prod")
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["Content-Security-Policy"] == security_headers.CSP
    assert "<style" not in r.text and 'class="edge-doc"' in r.text
    _assert_baseline(r.headers)


def test_report_only_switches_the_header_not_the_policy(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "prod")
    monkeypatch.setenv("CSP_REPORT_ONLY", "1")
    r = client.get("/")
    assert r.headers["Content-Security-Policy-Report-Only"] == security_headers.CSP
    assert "Content-Security-Policy" not in r.headers


def test_the_policy_lets_the_app_do_what_it_does_and_nothing_else():
    rules = dict(part.split(" ", 1) for part in security_headers.CSP.split("; "))
    assert rules["script-src"] == "'self'" and rules["style-src"] == "'self'"
    assert rules["img-src"] == "'self' data:"          # avatars + the select chevron
    assert rules["object-src"] == "'self'"            # the résumé preview <object>
    assert rules["frame-ancestors"] == "'self'"
    assert "unsafe" not in security_headers.CSP


def test_a_pdf_or_json_response_gets_no_policy_and_a_route_policy_wins():
    pdf = Response(b"%PDF-1.4", media_type="application/pdf")
    security_headers.apply(pdf)
    assert "Content-Security-Policy" not in pdf.headers
    assert pdf.headers["X-Frame-Options"] == "SAMEORIGIN"
    own = HTMLResponse("<p>x</p>", headers={"Content-Security-Policy": "default-src 'none'"})
    security_headers.apply(own)
    assert own.headers["Content-Security-Policy"] == "default-src 'none'"


def test_the_api_description_answers_only_in_dev(client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "dev")
    assert client.get("/openapi.json").status_code == 200
    docs = client.get("/docs")
    assert docs.status_code == 200 and "cdn.jsdelivr.net" in docs.headers["Content-Security-Policy"]
    for mode in ("uat", "prod"):
        monkeypatch.setenv("APP_MODE", mode)
        assert client.get("/openapi.json").status_code == 404
        assert client.get("/docs").status_code == 404
        assert "redoc" not in client.get("/redoc").text.lower()  # no second explorer anywhere


def test_the_server_does_not_name_its_software(monkeypatch):
    import uvicorn

    from resume_screener import serve

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setattr(serve, "_is_root", lambda: False)  # not root: nothing to drop
    serve.main()
    assert seen["app"] == "resume_screener.api.app:app"
    assert seen["server_header"] is False and seen["port"] == 8000


@pytest.mark.parametrize("writable", [True, False])
def test_the_container_drops_root_only_when_it_can_still_write(monkeypatch, writable, caplog):
    """As root on Linux: drop to uid 10001 when the data root is writable for it;
    otherwise keep serving as root and say so — never fail to start. (Simulated: the
    real check forks a child as uid 10001; the first UAT roll's log line confirms it.)"""
    import logging

    import uvicorn

    from resume_screener import serve

    dropped = []
    monkeypatch.setattr(serve, "_is_root", lambda: True)
    monkeypatch.setattr(serve, "_writable_as_app", lambda root: writable)
    monkeypatch.setattr(serve, "_drop_privileges", lambda: dropped.append(True))
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    with caplog.at_level(logging.INFO, logger="resume_screener.serve"):
        serve.main()
    assert dropped == ([True] if writable else [])
    assert ("Serving as uid 10001" in caplog.text) == writable
    assert ("serving as ROOT" in caplog.text) == (not writable)
