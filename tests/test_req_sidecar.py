"""P29: the consolidated jds/<stem>.req.json requisition sidecar (Addendum 11 D1).

save_req_meta is read-merge-write: unknown keys survive (later prompts add
fields), the schema stamp is enforced, and the write is atomic. fetch_requisitions
reads job_location + icims_req_id from it; sidecar-less roles get defaults.
"""

from __future__ import annotations

import json

from resume_screener.ats.local_folder import (
    REQ_META_SCHEMA,
    LocalFolderATS,
    load_req_meta,
    req_meta_path,
    save_req_meta,
)


def _jd(tmp_path, stem="finance-analyst", title="Finance Analyst"):
    jd = tmp_path / f"{stem}.md"
    jd.write_text(f"# {title}\n\nbody", encoding="utf-8")
    return jd


# --- load: missing / corrupt / wrong shape -> {} -------------------------------------


def test_missing_sidecar_loads_as_empty(tmp_path):
    assert load_req_meta(_jd(tmp_path)) == {}


def test_corrupt_sidecar_loads_as_empty(tmp_path):
    jd = _jd(tmp_path)
    req_meta_path(jd).write_text("{not json", encoding="utf-8")
    assert load_req_meta(jd) == {}


def test_non_dict_sidecar_loads_as_empty(tmp_path):
    jd = _jd(tmp_path)
    req_meta_path(jd).write_text('["a list"]', encoding="utf-8")
    assert load_req_meta(jd) == {}


# --- save: merge semantics + unknown-key preservation + schema stamp -----------------


def test_save_merges_over_existing_values(tmp_path):
    jd = _jd(tmp_path)
    save_req_meta(jd, job_location="IN", status="active")
    save_req_meta(jd, status="closed", closed_by="head@example.com")
    meta = load_req_meta(jd)
    assert meta["job_location"] == "IN"  # untouched key survives the second write
    assert meta["status"] == "closed" and meta["closed_by"] == "head@example.com"
    assert meta["schema"] == REQ_META_SCHEMA


def test_save_preserves_unknown_keys(tmp_path):
    # A later prompt's field (P41 writes hm) must survive a P29-era writer.
    jd = _jd(tmp_path)
    req_meta_path(jd).write_text(
        json.dumps({"schema": 1, "job_location": "IN",
                    "hm": {"email": "hm@example.com", "name": "Rahul", "status": "invited"},
                    "future_field": 42}),
        encoding="utf-8")
    save_req_meta(jd, status="closed")
    meta = load_req_meta(jd)
    assert meta["hm"] == {"email": "hm@example.com", "name": "Rahul", "status": "invited"}
    assert meta["future_field"] == 42
    assert meta["status"] == "closed"


def test_save_over_corrupt_sidecar_starts_fresh(tmp_path):
    jd = _jd(tmp_path)
    req_meta_path(jd).write_text("{broken", encoding="utf-8")
    meta = save_req_meta(jd, job_location="EU")
    assert meta == {"job_location": "EU", "schema": REQ_META_SCHEMA}
    assert load_req_meta(jd)["job_location"] == "EU"


def test_atomic_write_leaves_no_temp_file(tmp_path):
    jd = _jd(tmp_path)
    save_req_meta(jd, job_location="IN")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert req_meta_path(jd).name == "finance-analyst.req.json"


# --- fetch_requisitions reads the sidecar --------------------------------------------


def _ats(tmp_path):
    return LocalFolderATS(jd_dir=tmp_path, resumes_dir=tmp_path / "r",
                          default_job_location="IN", roles_dir=tmp_path / "roles")


def test_icims_round_trip_through_fetch_requisitions(tmp_path):
    jd = _jd(tmp_path)
    save_req_meta(jd, icims_req_id="IN-2026-1042", job_location="US-CA")
    req = _ats(tmp_path).fetch_requisitions()[0]
    assert req.icims_req_id == "IN-2026-1042"
    assert req.job_location == "US-CA"  # location comes from the .req.json too


def test_sidecar_less_role_gets_defaults(tmp_path):
    _jd(tmp_path)
    req = _ats(tmp_path).fetch_requisitions()[0]
    assert req.icims_req_id is None
    assert req.job_location == "IN"  # configured default


def test_req_json_sidecar_is_never_mistaken_for_a_jd(tmp_path):
    jd = _jd(tmp_path)
    save_req_meta(jd, job_location="IN")
    reqs = _ats(tmp_path).fetch_requisitions()
    assert [r.req_id for r in reqs] == ["finance-analyst"]
