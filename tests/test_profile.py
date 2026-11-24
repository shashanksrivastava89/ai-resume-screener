"""Profile-parse tests with a fake OpenAI client: assembly, cache behavior,
artifact freezing. Offline."""

import json
from types import SimpleNamespace

from resume_screener.cache import JsonCache
from resume_screener.ingest import ProfileDraft, RoleStintDraft, freeze_profile, parse_profile, role_slug

DRAFT = ProfileDraft(
    years_experience_total=6.0,
    skills=["US GAAP", "SOX", " Reconciliations "],
    roles=[
        RoleStintDraft(title="Senior Treasury Accountant", summary="Owns close",
                       start="2021-01", end=None, employment_type=None),
        RoleStintDraft(title="Accountant", summary="Journal entries",
                       start="2019-04", end="2020-12", employment_type="contract"),
    ],
    education=["M.Com Finance, University of Delhi"],
    certifications=["SAP FI Certified"],
    languages=[],
)


class FakeClient:
    def __init__(self, parsed):
        self.calls = 0

        def parse(**kwargs):
            self.calls += 1
            message = SimpleNamespace(refusal=None, parsed=parsed)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))


def test_parse_assembles_profile_and_caches(tmp_path):
    cache = JsonCache(tmp_path)
    client = FakeClient(DRAFT)

    profile, cached = parse_profile("REDACTED TEXT", "cid123", "src.pdf",
                                    client, "gpt-test", cache)
    assert not cached and client.calls == 1
    assert profile.candidate_id == "cid123"
    assert profile.skills == ["US GAAP", "SOX", "Reconciliations"]  # trimmed
    assert profile.roles[1].employment_type == "contract"
    assert profile.roles[0].end is None
    assert profile.redacted_text == "REDACTED TEXT"

    again, cached2 = parse_profile("REDACTED TEXT", "cid123", "src.pdf",
                                   client, "gpt-test", cache)
    assert cached2 and client.calls == 1  # second call served from cache
    assert again == profile

    _, cached3 = parse_profile("DIFFERENT TEXT", "cid123", "src.pdf",
                               client, "gpt-test", cache)
    assert not cached3 and client.calls == 2  # different text = different key


def test_freeze_writes_readable_artifact(tmp_path):
    from resume_screener.ingest import default_artifact_dir

    cache = JsonCache(tmp_path / "c")
    profile, _ = parse_profile("TXT", "cidabc", "src.pdf",
                               FakeClient(DRAFT), "gpt-test", cache)
    path = freeze_profile(profile, default_artifact_dir("Role 1: Treasury / Accounting", tmp_path / "out"))
    assert path.name == "cidabc.profile.json"
    assert "Role-1-Treasury-Accounting" in str(path.parent)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["candidate_id"] == "cidabc"
    assert data["roles"][0]["title"] == "Senior Treasury Accountant"


def test_role_slug():
    assert role_slug("role-1") == "role-1"
    assert role_slug("  ") == "role"
    assert role_slug("A/B ??") == "A-B"
