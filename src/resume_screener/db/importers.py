"""One-time backfill importers: flat-file logs → Postgres.

Run once (via `screener db import`) when a domain is switched to the DB backend, to
carry the existing history across. Everything imported here is APPEND-ONLY history,
so each importer is guarded: if the target table already holds rows it SKIPS (and
says so) rather than appending duplicates — making the command safe to re-run.

These read the same on-disk files the file backends write, so a DB backend starts
with the full trail intact:
- decisions : output/feedback/decisions.jsonl                → decision
- audit     : signals/audit_log.jsonl                        → audit_entry
- review    : signals/review_status.json (latest sign-off)   → review_signoff
- runs      : output/*/run_meta.json                         → run (per-run_id guard)

The audit log already contains every historical `review_signoff` MARKER line (the
file `record_review` wrote one each time), so the review importer inserts only the
structured `review_signoff` ROW and never re-writes that marker — avoiding a double
count that would corrupt the pending-banner ordering.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resume_screener.db.models.runtime import AuditEntry, Decision as DecisionRow, ReviewSignoff
from resume_screener.db.repositories import decisions as decisions_repo
from resume_screener.db.repositories import governance as gov_repo
from resume_screener.db.repositories import policy as policy_repo
from resume_screener.db.repositories import regions as regions_repo
from resume_screener.db.repositories import registry as registry_repo
from resume_screener.db.repositories import requisitions as requisitions_repo
from resume_screener.db.repositories import rubric as rubric_repo
from resume_screener.db.repositories import runs as runs_repo
from resume_screener.models import Decision


def _count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def import_decisions(
    session: Session, path: str | Path = "output/feedback/decisions.jsonl"
) -> dict:
    """Backfill the feedback log into `decision`. Skips if the table isn't empty."""
    path = Path(path)
    if not path.exists():
        return {"domain": "decisions", "added": 0, "skipped": True, "reason": "no file"}
    if _count(session, DecisionRow):
        return {"domain": "decisions", "added": 0, "skipped": True,
                "reason": "table already has rows"}
    added = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            decision = Decision.model_validate(json.loads(line))
        except Exception:
            continue  # skip a corrupt line, like the file loader does
        decisions_repo.add_decision(session, decision)
        added += 1
    return {"domain": "decisions", "added": added, "skipped": False, "reason": ""}


def import_audit(session: Session, path: str | Path = "signals/audit_log.jsonl") -> dict:
    """Backfill the audit trail into `audit_entry`, preserving each line's own ts and
    ORDER (rows land in file order → the append-only id keeps the banner ordering).
    Skips if the table isn't empty."""
    path = Path(path)
    if not path.exists():
        return {"domain": "audit", "added": 0, "skipped": True, "reason": "no file"}
    if _count(session, AuditEntry):
        return {"domain": "audit", "added": 0, "skipped": True,
                "reason": "table already has rows"}
    added = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        # region_id stays NULL (global): pre-Phase-7 history predates the region model.
        gov_repo.append_audit(session, entry, region_id=None)
        added += 1
    return {"domain": "audit", "added": added, "skipped": False, "reason": ""}


def import_review(session: Session, path: str | Path = "signals/review_status.json") -> dict:
    """Backfill the latest review sign-off into `review_signoff` (row only — the audit
    MARKER is already carried by import_audit). Skips if the table isn't empty."""
    path = Path(path)
    if not path.exists():
        return {"domain": "review", "added": 0, "skipped": True, "reason": "no file"}
    if _count(session, ReviewSignoff):
        return {"domain": "review", "added": 0, "skipped": True,
                "reason": "table already has rows"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"domain": "review", "added": 0, "skipped": True, "reason": "unreadable"}
    reviewers = str(data.get("reviewers", "")).strip()
    review_date = str(data.get("date", "")).strip()
    if not reviewers or not review_date:
        return {"domain": "review", "added": 0, "skipped": True, "reason": "incomplete"}
    session.add(ReviewSignoff(
        region_id=None, reviewers=reviewers, review_date=review_date,
        recorded_by=data.get("by"), ts=data.get("ts"),
    ))
    session.flush()
    return {"domain": "review", "added": 1, "skipped": False, "reason": ""}


def import_runs(session: Session, output_root: str | Path = "output") -> dict:
    """Backfill the run index from every output/<req_id>/run_meta.json. Guarded per
    run_id (not by an empty-table check), so it's safe to re-run to pick up new runs
    without duplicating ones already indexed. req_code = the output-folder name; the
    governing region is resolved from the run's jurisdiction."""
    root = Path(output_root)
    if not root.exists():
        return {"domain": "runs", "added": 0, "skipped": True, "reason": "no runs on disk"}
    added = existing = 0
    for meta_path in sorted(root.glob("*/run_meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        run_id = str(meta.get("run_id", ""))
        if run_id and runs_repo.has_run(session, run_id):
            existing += 1
            continue
        region_id = regions_repo.region_id_for_jurisdiction(
            session, str(meta.get("jurisdiction", ""))
        )
        runs_repo.record_run(session, meta, req_code=meta_path.parent.name, region_id=region_id)
        added += 1
    if added == 0 and existing == 0:
        return {"domain": "runs", "added": 0, "skipped": True, "reason": "no runs on disk"}
    reason = f"{existing} already indexed" if existing else ""
    return {"domain": "runs", "added": added, "skipped": False, "reason": reason}


def import_registry(session: Session, path: str | Path = "signals") -> dict:
    """Backfill the signal-registry rows (signals + exclusions + version) from
    the governed scoring model (signals/scoring_model.vN.yaml — the registry's
    source of truth since the 2026-07-18 merge). Skips if signals already
    exist. Seeds is_base_locked from the model's floor topics — the rail that
    those need an explicit acknowledgment to remove."""
    from resume_screener.registry import RegistryError, load_registry
    from resume_screener.scoring_model import (
        ScoringModelError, latest_scoring_model_path, load_scoring_model,
    )

    p = Path(path)
    directory = p if p.is_dir() or not p.suffix else p.parent
    if latest_scoring_model_path(directory) is None:
        return {"domain": "registry", "added": 0, "skipped": True,
                "reason": "no governed scoring model here"}
    try:
        model = load_registry(directory)
        floor_ids = load_scoring_model(directory).floor_topic_ids()
    except (RegistryError, ScoringModelError):
        return {"domain": "registry", "added": 0, "skipped": True, "reason": "unreadable"}
    return registry_repo.import_registry(session, model, base_locked_ids=floor_ids)


def import_policies(session: Session, policies_dir: str | Path = "policies") -> dict:
    """Backfill the jurisdiction policy packs (base + overlays) from policies/*.yaml.
    Reads the FILES directly (not policy.load_pack, which would route to the DB when
    policy_backend=db). Skips if packs already exist, so `screener db import` is
    re-runnable."""
    from resume_screener.policy import PolicyError, _load_pack_file

    root = Path(policies_dir)
    if not root.exists():
        return {"domain": "policies", "added": 0, "skipped": True, "reason": "no dir"}
    packs = []
    for pack_path in sorted(root.glob("*.yaml")):
        try:
            packs.append(_load_pack_file(pack_path))
        except PolicyError:
            continue  # a malformed pack is skipped, like the file loader would
    if not packs:
        return {"domain": "policies", "added": 0, "skipped": True, "reason": "no packs"}
    return policy_repo.import_packs(session, packs)


def import_rubrics(session: Session, jd_dir: str | Path = "jds") -> dict:
    """Backfill every versioned rubric (jds/<stem>.rubric.vN.yaml) into rubric rows,
    keyed by the JD stem. Reads the FILES directly (not rubric.load_rubric, which
    would route to the DB when rubric_backend=db). Skips if any rubric rows exist, so
    `screener db import` is re-runnable."""
    from resume_screener.rubric import RubricError, _load_rubric_file, _rubric_glob

    root = Path(jd_dir)
    if not root.exists():
        return {"domain": "rubrics", "added": 0, "skipped": True, "reason": "no dir"}
    if rubric_repo.has_any(session):
        return {"domain": "rubrics", "added": 0, "skipped": True,
                "reason": "table already has rows"}
    added = 0
    for jd in sorted(root.iterdir()):
        if not jd.is_file() or jd.suffix.lower() not in {".md", ".txt"} or jd.name.startswith("."):
            continue
        for version_path in _rubric_glob(jd):  # sorted ascending by version
            try:
                rubric = _load_rubric_file(version_path)
            except RubricError:
                continue  # a malformed version file is skipped, like the loader would
            rubric_repo.import_rubric_version(session, jd.stem, rubric)
            added += 1
    if added == 0:
        return {"domain": "rubrics", "added": 0, "skipped": True, "reason": "no rubrics"}
    return {"domain": "rubrics", "added": added, "skipped": False, "reason": ""}


def import_requisitions(
    session: Session, jd_dir: str | Path | None = None, default_job_location: str | None = None
) -> dict:
    """Backfill JD documents + requisitions from jds/ (title from the JD heading,
    job_location from the .req.json sidecar), then link the soft req_code
    references on rubric/run/screening_job rows to the new requisition_id FKs.

    Reads JD FILES directly (role definitions, not résumés). Skips CREATING rows if
    requisitions already exist, but always runs the (idempotent) FK backfill so a
    re-run links any rows added since."""
    from resume_screener.ats.local_folder import JD_SUFFIXES, _jd_title, load_req_meta, role_slug

    if jd_dir is None or default_job_location is None:
        try:
            from resume_screener.config import load_app_config

            cfg = load_app_config()
            jd_dir = jd_dir or cfg.ats.jd_dir
            default_job_location = default_job_location or cfg.policy.default_jurisdiction
        except Exception:  # noqa: BLE001 — no config.yaml (e.g. tests): fall back to defaults
            jd_dir = jd_dir or "jds"
            default_job_location = default_job_location or "IN"

    root = Path(jd_dir)
    if not root.exists():
        return {"domain": "requisitions", "added": 0, "skipped": True, "reason": "no dir"}
    if requisitions_repo.has_any(session):
        linked = requisitions_repo.backfill_requisition_links(session)
        reason = "table already has rows"
        if linked:
            reason += f" ({linked} FK link(s) backfilled)"
        return {"domain": "requisitions", "added": 0, "skipped": True, "reason": reason}

    added = 0
    for jd in sorted(root.iterdir()):
        if not jd.is_file() or jd.suffix.lower() not in JD_SUFFIXES or jd.name.startswith("."):
            continue
        raw = jd.read_bytes()
        title = _jd_title(jd)
        doc = requisitions_repo.upsert_jd_document(
            session,
            content_hash=hashlib.sha256(raw).hexdigest(),
            title=title,
            source_ref=str(jd),
            media_type=jd.suffix.lower().lstrip("."),
            text=raw.decode("utf-8", errors="replace"),
        )
        requisitions_repo.upsert_requisition(
            session,
            req_code=jd.stem,
            role_title=title,
            job_location=load_req_meta(jd).get("job_location") or default_job_location,
            jd_document_id=doc.id,
            slug=role_slug(title),
        )
        added += 1
    if added == 0:
        return {"domain": "requisitions", "added": 0, "skipped": True, "reason": "no JDs"}
    linked = requisitions_repo.backfill_requisition_links(session)
    reason = f"{linked} FK link(s) backfilled" if linked else ""
    return {"domain": "requisitions", "added": added, "skipped": False, "reason": reason}


# --- v2 domains (P49) ---------------------------------------------------------------


def _fold_jsonl(path: Path, key: str) -> list[dict]:
    """Fold a latest-wins JSONL log (preferences/hm_takes) to its current records."""
    if not path.exists():
        return []
    latest: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get(key) is not None:
            latest[rec[key]] = rec
    return list(latest.values())


def import_preferences(
    session: Session, path: str | Path = "signals/preferences.jsonl"
) -> dict:
    """Backfill the folded HR preferences (latest per pref_id) into `hr_preference`.
    Reads the FILE directly (not preferences._fold, which routes to the DB when
    prefs_backend=db). Skips if the table already has rows."""
    from resume_screener.db.repositories import preferences as prefs_repo

    return prefs_repo.import_prefs(session, _fold_jsonl(Path(path), "pref_id"))


def import_events(session: Session, path: str | Path = "signals/events.jsonl") -> dict:
    """Backfill the app-event spine into `app_event`, preserving file ORDER (→ id
    order). Reads the FILE directly. Skips if the table already has rows."""
    from resume_screener.db.repositories import events as events_repo

    path = Path(path)
    entries: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return events_repo.import_events(session, entries)


def import_hm_takes(
    session: Session, path: str | Path = "output/feedback/hm_takes.jsonl"
) -> dict:
    """Backfill the folded hiring-manager takes (latest per run/candidate/hm) into
    `hm_take`. Reads the FILE directly. Skips if the table already has rows."""
    from resume_screener.db.repositories import hm_takes as hm_repo

    # Fold on the composite key the file store supersedes on.
    if not Path(path).exists():
        return hm_repo.import_takes(session, [])
    latest: dict = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("take_id"):
            latest[(rec.get("run_id"), rec.get("candidate_id"), rec.get("hm_email"))] = rec
    return hm_repo.import_takes(session, list(latest.values()))


def import_flags(session: Session) -> dict:
    """Backfill the editable feature flags (feature.default_enabled + region_feature
    overrides) from signals/features.yaml. Idempotent upserts (re-runnable);
    env-controlled flags are skipped (they carry no DB state)."""
    from resume_screener import feature_flags
    from resume_screener.db.repositories import flags as flags_repo

    return flags_repo.import_flags(session, feature_flags.file_catalog())


def import_requisition_contacts(session: Session, jd_dir: str | Path | None = None) -> dict:
    """Backfill requisition_contact rows from the .req.json sidecars: the assigned
    `hm` → a hiring_manager contact, `created_by` → a recruiter contact. Idempotent
    on (requisition, role, email); requires the requisitions to exist (runs after
    import_requisitions in import_all)."""
    from resume_screener.ats.local_folder import JD_SUFFIXES, load_req_meta

    if jd_dir is None:
        try:
            from resume_screener.config import load_app_config

            jd_dir = load_app_config().ats.jd_dir
        except Exception:  # noqa: BLE001
            jd_dir = "jds"
    root = Path(jd_dir)
    if not root.exists():
        return {"domain": "req_contacts", "added": 0, "skipped": True, "reason": "no dir"}
    added = 0
    for jd in sorted(root.iterdir()):
        if not jd.is_file() or jd.suffix.lower() not in JD_SUFFIXES or jd.name.startswith("."):
            continue
        meta = load_req_meta(jd)
        hm = meta.get("hm") if isinstance(meta.get("hm"), dict) else None
        if hm and hm.get("email"):
            added += requisitions_repo.upsert_contact(
                session, req_code=jd.stem, contact_role="hiring_manager",
                contact_email=hm.get("email"), contact_name=hm.get("name"))
        if meta.get("created_by"):
            added += requisitions_repo.upsert_contact(
                session, req_code=jd.stem, contact_role="recruiter",
                contact_email=meta.get("created_by"))
    return {"domain": "req_contacts", "added": added, "skipped": added == 0,
            "reason": "" if added else "no sidecar contacts"}


def import_all(session: Session) -> list[dict]:
    """Run every backfill importer once; returns a per-domain report. Requisitions
    run before their contacts (which need the rows) and the FK backfill sees the
    rubric/run rows the earlier importers made."""
    return [
        import_decisions(session),
        import_audit(session),
        import_review(session),
        import_runs(session),
        import_registry(session),
        import_policies(session),
        import_rubrics(session),
        import_requisitions(session),
        import_requisition_contacts(session),
        import_preferences(session),
        import_events(session),
        import_hm_takes(session),
        import_flags(session),
    ]
