"""LocalFolderATS (P2, reshaped in P18): JDs from jds/, resumes from PER-ROLE batches.

Addendum 05 Part B: every role owns its candidate batch at
    data/roles/<role-slug>/resumes/
managed from the UI (upload, folder import with content-hash dedupe, remove).
fetch_candidates(req) reads THAT requisition's batch — exactly the shape of the
future iCIMS/Greenhouse adapter fetching candidates per requisition (§12).

Back-compat: if a role's batch is empty but the legacy shared folder
(data/resumes/) has files, the adapter falls back to it with a log note — the
UI offers a one-time copy-migration and never moves files silently.

Real adapters will implement the same five methods against REST APIs and map
real ids; nothing outside the ats/ package should ever care which adapter is
active. Batch storage helpers live here too: they are the local provider's
notion of "attaching a candidate to a requisition".
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from resume_screener.ats.base import ATSPort
from resume_screener import reqcache
from resume_screener.locking import file_lock
from resume_screener.models import CandidateRef, DocumentRef, Requisition, WorkLocation, candidate_id_for

log = logging.getLogger(__name__)

RESUME_SUFFIXES = {".pdf", ".docx"}
JD_SUFFIXES = {".md", ".txt"}  # .docx JDs get converted to .md first (see README)

# THE requisition-metadata sidecar next to the JD (P29, Addendum 11 Part D1).
# Schema v1: {schema, icims_req_id, job_location, country, us_state,
# status: "active"|"closed",
# outcome: {kind, hired_count?}|null, closed_at, closed_by, snooze_until,
# created_by, created_at, jd_language, hm: null (P41 fills it),
# work_location: {place, radius_km, max_km}|null (2026-08-20 — the physical
#   workplace + the recruiter's search radius; NON-SCORING context, see
#   models.WorkLocation. Additive and optional: an older sidecar without it loads
#   unchanged and the requisition simply has no location context)}.
# Lifecycle/status/hm never enter the ATS Pydantic contract — closing a req here
# is "this workspace only", never an ATS state.
REQ_META_SUFFIX = ".req.json"
REQ_META_SCHEMA = 1


def req_meta_path(jd_path: str | Path) -> Path:
    """The sidecar holding a role's requisition metadata (jds/<stem>.req.json)."""
    jd_path = Path(jd_path)
    return jd_path.parent / f"{jd_path.stem}{REQ_META_SUFFIX}"


def _work_location(meta: dict) -> "WorkLocation | None":
    """The sidecar's work_location as a contract object, or None.

    Tolerant on purpose: this data is hand-editable and arrives from an intake form,
    so a malformed radius must degrade to "not set" rather than break the whole
    requisition list. A place-less entry is None — a radius with nothing to measure
    from is meaningless, and rendering "within 25 km of" nothing would be worse than
    showing no location at all."""
    raw = meta.get("work_location")
    if not isinstance(raw, dict):
        return None
    place = str(raw.get("place") or "").strip()
    if not place:
        return None

    def _int(key: str, default: int | None) -> int | None:
        try:
            value = raw.get(key)
            return default if value is None else max(0, int(value))
        except (TypeError, ValueError):
            return default

    return WorkLocation(place=place, radius_km=_int("radius_km", None), max_km=_int("max_km", 100) or 100)


# Requisition fields that live in the .req.json SIDECAR in BOTH backends, so a
# DB-sourced row never carries them. Each entry reads one field out of the sidecar
# dict. Keyed by field name so hydrate_sidecar_only_fields cannot forget one, and
# tested against what the requisitions repo actually populates.
SIDECAR_ONLY_FIELDS = {
    "work_location": lambda meta: _work_location(meta),
    "icims_req_id": lambda meta: (str(meta.get("icims_req_id") or "").strip() or None),
    # The country/state the requisition was created for (2026-09-12). Same class as
    # the two above — the requisitions table carries job_location (the pack) and
    # nothing finer — so they belong here or a DB-backed deployment reads them as
    # None forever, which is exactly how work_location and icims_req_id were lost.
    "country": lambda meta: (str(meta.get("country") or "").strip().upper() or None),
    "us_state": lambda meta: (str(meta.get("us_state") or "").strip().upper() or None),
}


def hydrate_sidecar_only_fields(rows: list["Requisition"]) -> list["Requisition"]:
    """Overlay the requisition fields that live in the SIDECAR onto DB-sourced rows.

    WHY THIS EXISTS (bug found on UAT, 2026-08-25). `fetch_requisitions` short-circuits
    to the Postgres repo when REQUISITIONS_BACKEND=db, and `work_location` has no column
    there — it belongs to the same class as lifecycle, hm and the iCIMS id: sidecar in
    BOTH backends, no column, no migration. Without this overlay the field came back None
    forever on any DB-backed deployment: the write landed in .req.json and the read came
    from a table that does not carry it, so the whole feature silently did nothing. It
    passed locally only because the file backend is the default, which is why it took a
    real click on UAT to surface.

    IT HAPPENED AGAIN (2026-08-26). This overlaid `work_location` only, so
    `icims_req_id` — named in the paragraph above as the same class of field — came
    back None on UAT. Every requisition showed a blank Req ID even though creation
    REQUIRES one, the dashboard's em-dash placeholder looked like a stray hyphen, and
    the mapping anchor back to the real iCIMS requisition was invisible.

    So the overlay is now driven by SIDECAR_ONLY_FIELDS rather than by remembering to
    add a line: a field that the Requisition contract carries and the requisitions
    table does not belongs in that tuple, and the test asserts the tuple covers
    everything the DB repo leaves unset.
    """
    out = []
    for row in rows:
        if not row.jd_ref:
            out.append(row)
            continue
        meta = load_req_meta(row.jd_ref)
        # Only ADD what the sidecar actually holds. Overlaying a None would destroy a
        # value the row did carry — harmless while the repo populates none of these,
        # and a silent data loss the moment it populates one.
        updates = {name: value for name, read in SIDECAR_ONLY_FIELDS.items()
                   if (value := read(meta)) is not None}
        out.append(row.model_copy(update=updates) if updates else row)
    return out


@reqcache.memo(key=lambda jd_path: str(jd_path))
def load_req_meta(jd_path: str | Path) -> dict:
    """A role's requisition metadata; {} when the sidecar is missing or corrupt
    (sidecar-less test roles get lifecycle defaults in the service layer).

    Read once per request: fifteen call sites read this sidecar, ~10 of them per
    requisition per dashboard load. The exists() probe is folded into the read —
    one round trip, not two."""
    path = req_meta_path(jd_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _touch_dashboard(jd_path: str | Path) -> None:
    """A sidecar write changes the card's title/lifecycle/HM/location."""
    from resume_screener import dashboard_index

    dashboard_index.invalidate(Path(jd_path).stem)


def save_req_meta(jd_path: str | Path, **updates: Any) -> dict:
    """Read-merge-write the requisition sidecar under a cross-process lock.

    Unknown keys are PRESERVED (later prompts add fields — hm in P41 — without
    touching this module); the write is atomic (temp + os.replace) so a crash
    never leaves a half-written sidecar. Returns the merged metadata."""
    path = req_meta_path(jd_path)
    with file_lock(path):
        # The DISK copy, never the request memo: this is read-merge-write under a
        # cross-process lock, and a memoised read taken earlier in the request could
        # predate another process's write — merging onto it would silently lose that
        # write. __wrapped__ is the un-memoised function.
        meta = load_req_meta.__wrapped__(jd_path)
        meta.update(updates)
        meta["schema"] = REQ_META_SCHEMA
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    invalidate_requisitions()  # the list caches the sidecar's identity fields
    _touch_dashboard(jd_path)  # ...and the dashboard card shows them
    return meta


# jd_dir -> (dir mtime_ns, cached_at monotonic, rows). See fetch_requisitions.
_REQ_LIST_CACHE: dict[str, tuple[int, float, list["Requisition"]]] = {}
# The TTL only guards an IN-PLACE edit of a JD by another process (nothing hosted
# does that — the CLI in dev is the one writer); creates, deletes and every sidecar
# write move the directory mtime and invalidate instantly, and in-process JD edits
# call invalidate_requisitions(). 2 s was too short: a cold first load can take
# longer than that, so the list was rebuilt on every 2-second poll.
_REQ_LIST_TTL_SECONDS = 15.0


def invalidate_requisitions() -> None:
    """Drop the cached requisition list. Called by every in-process writer of a JD
    or sidecar (save_req_meta, the JD edit route); a directory change (create,
    delete, replace) invalidates on its own through the mtime key."""
    _REQ_LIST_CACHE.clear()


def _use_requisitions_db() -> bool:
    """True when role identity is read from the database (requisitions_backend=db).
    Only fetch_requisitions is affected — résumé files/batches stay on disk."""
    from resume_screener.config import load_db_settings

    return load_db_settings().requisitions_backend.strip().lower() == "db"


class BatchError(RuntimeError):
    """Batch-management problem with a message safe to show in the UI."""


def role_slug(title: str) -> str:
    """Role title -> folder-safe slug ('Accountant — Treasury' -> 'accountant-treasury')."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "role"


def batch_dir(roles_dir: str | Path, handle: str) -> Path:
    """The per-role résumé batch. Callers pass the role's IMMUTABLE handle
    (req_id = the JD file stem) so title edits never orphan a batch; role_slug
    is idempotent on slugs, so app-created paths are byte-identical."""
    return Path(roles_dir) / role_slug(handle) / "resumes"


@reqcache.memo(key=lambda folder: str(folder))
def batch_files(folder: str | Path) -> list[Path]:
    """The resume files in a folder (sorted = deterministic candidate order).

    One directory read, not N+1: `iterdir()` + `p.is_file()` cost a stat per entry
    — 235 round trips to the share for the big requisition, on every request that
    touched it. os.scandir answers is_file() from the directory listing itself."""
    folder = Path(folder)
    try:
        with os.scandir(folder) as it:
            return sorted(
                folder / e.name for e in it
                if e.is_file()
                and os.path.splitext(e.name)[1].lower() in RESUME_SUFFIXES
                and not e.name.startswith((".", "~$"))  # hidden + Word owner/lock files (~$x.docx)
            )
    except FileNotFoundError:
        return []
    except NotADirectoryError:
        return []


@dataclass
class ImportReport:
    added: int = 0
    duplicates: int = 0
    unsupported: int = 0
    errors: list[str] = field(default_factory=list)

    def sentence(self) -> str:
        parts = [f"{self.added} added"]
        if self.duplicates:
            parts.append(f"{self.duplicates} duplicate{'s' if self.duplicates != 1 else ''} skipped")
        if self.unsupported:
            parts.append(f"{self.unsupported} unsupported skipped")
        if self.errors:
            parts.append(f"{len(self.errors)} failed")
        return ", ".join(parts)


# Content-hash cache keyed on a file's identity ON DISK: abs path -> (size,
# mtime_ns, digest).
#
# Every upload de-dups the incoming files against the hashes of the ENTIRE existing
# batch. Uncached, that re-read every résumé already in the batch on every single
# upload — so the cost of adding two files grew with how many were already there,
# and on a hosted deployment (where the batch lives on a network share) each of
# those reads is a round trip. Measured on the UAT box: the same 20-file upload took
# 26s into an empty batch and 215s into a batch of 200, against a hard 230s
# platform request timeout. New bytes always move size or mtime, so a stale digest
# cannot survive a change to the file.
_HASH_CACHE: dict[str, tuple[int, int, str]] = {}


def _remember_hash(path: Path, digest: str) -> None:
    """Record a digest we already hold for a file just written, so the batch never
    has to be read back to learn it."""
    try:
        stat = path.stat()
    except OSError:
        return
    _HASH_CACHE[str(path.resolve())] = (stat.st_size, stat.st_mtime_ns, digest)


def cached_candidate_id(path: Path) -> str:
    """content-hash id for a résumé file, memoized on its (size, mtime_ns)."""
    try:
        stat = path.stat()
    except OSError:
        return candidate_id_for(path.read_bytes())
    key = str(path.resolve())
    hit = _HASH_CACHE.get(key)
    if hit is not None and hit[0] == stat.st_size and hit[1] == stat.st_mtime_ns:
        return hit[2]
    digest = candidate_id_for(path.read_bytes())
    _HASH_CACHE[key] = (stat.st_size, stat.st_mtime_ns, digest)
    return digest


def _batch_hashes(folder: Path) -> set[str]:
    return {cached_candidate_id(p) for p in batch_files(folder)}


def batch_content_hashes(folder: Path) -> set[str]:
    """Public: the content-hash set of a batch, so callers (e.g. the upload
    route) can compute `known_hashes` once and amortize a bulk add."""
    return _batch_hashes(folder)


def _batch_contacts(folder: Path, exclude: set[str] | None = None) -> set[str]:
    """Every SINGLE-candidate résumé's contact tokens across the batch — the set a
    new add is checked against to catch the same person arriving under a different
    file. Cached per file (size+mtime), so a large batch is read once rather than
    re-extracted on every upload. Multi-candidate packets are excluded: their 40+
    addresses aren't one person's identity and would reject whole files.

    `exclude` skips files by name — the splitter passes the packet it is currently
    slicing, whose own text obviously contains every candidate inside it (without
    this, each slice would match its own source and be dropped)."""
    from resume_screener.contacts import holds_many_candidates, tokens_for_path

    tokens: set[str] = set()
    for p in batch_files(folder):
        if exclude and p.name in exclude:
            continue
        found = tokens_for_path(p)
        if found and not holds_many_candidates(found):
            tokens |= found
    return tokens


def batch_contact_tokens(folder: Path) -> set[str]:
    """Public: all contact-identity tokens in a batch, so callers can compute
    `known_contacts` once and amortize a bulk add (mirrors batch_content_hashes)."""
    return _batch_contacts(folder)


def _free_name(folder: Path, name: str) -> Path:
    """Collision-safe target path (same NAME but different CONTENT gets '-2', '-3', …)."""
    target = folder / Path(name).name
    stem, suffix = target.stem, target.suffix
    counter = 2
    while target.exists():
        target = folder / f"{stem}-{counter}{suffix}"
        counter += 1
    return target


def add_bytes_to_batch(file_name: str, data: bytes, folder: str | Path,
                       known_hashes: set[str] | None = None,
                       known_contacts: set[str] | None = None) -> str:
    """One file into a batch -> 'added' | 'duplicate' | 'unsupported'.

    De-dup identifies the same CANDIDATE two ways, in order:
      1. exact CONTENT hash — the identical file forwarded again;
      2. shared CONTACT (email/phone) read locally from the résumé — the same
         person arriving under a different file / slightly edited résumé.
    A shared NAME is deliberately NOT a duplicate: two different people can have
    the same name on one requisition, and both must be kept. A résumé with no
    readable email/phone falls back to content-hash de-dup only.

    A MULTI-candidate packet (an iCIMS Bulk Print: 40+ people in one PDF) is
    de-duped by content hash ONLY — see contacts.holds_many_candidates. Its
    people are identified individually once the splitter has turned it into one
    file per candidate.

    Pass known_hashes / known_contacts to amortize bulk imports (compute once,
    the sets are extended in place as files land)."""
    from resume_screener.contacts import contact_tokens, holds_many_candidates, remember_tokens

    suffix = Path(file_name).suffix.lower()
    if suffix not in RESUME_SUFFIXES:
        return "unsupported"
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    hashes = _batch_hashes(folder) if known_hashes is None else known_hashes
    digest = candidate_id_for(data)
    if digest in hashes:
        return "duplicate"
    tokens = contact_tokens(data, suffix)
    if tokens and not holds_many_candidates(tokens):
        contacts = _batch_contacts(folder) if known_contacts is None else known_contacts
        if tokens & contacts:
            return "duplicate"  # same person (shared email/phone), different file
        contacts.update(tokens)
    target = _free_name(folder, file_name)
    target.write_bytes(data)
    # We just hashed AND read these bytes — hand both results to the caches rather
    # than making the next upload read the file back off the share to rediscover
    # what this one already knows.
    _remember_hash(target, digest)
    remember_tokens(target, tokens)
    hashes.add(digest)
    return "added"


def import_folder_into_batch(source: str | Path, folder: str | Path) -> ImportReport:
    """COPY resume files from a local folder (recursive) into a batch.

    Copy, not reference (addendum 05): the project stays self-contained, an
    offline OneDrive path can't break screening, and retention has one story.
    """
    source = Path(str(source).strip().strip('"'))
    if not source.exists():
        raise BatchError(f"That folder doesn't exist: {source}")
    if not source.is_dir():
        raise BatchError(f"That's a file, not a folder: {source}")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if source.resolve() == folder.resolve():
        raise BatchError("That's already this role's batch folder.")

    report = ImportReport()
    known = _batch_hashes(folder)
    known_contacts = _batch_contacts(folder)
    try:
        entries = sorted(p for p in source.rglob("*") if p.is_file())
    except (OSError, PermissionError) as exc:
        raise BatchError(f"Couldn't read that folder (permissions?): {exc}") from exc
    for path in entries:
        if path.name.startswith((".", "~$")):
            continue
        if path.suffix.lower() not in RESUME_SUFFIXES:
            report.unsupported += 1
            continue
        try:
            outcome = add_bytes_to_batch(path.name, path.read_bytes(), folder, known, known_contacts)
        except (OSError, PermissionError) as exc:
            report.errors.append(f"{path.name}: {exc}")
            continue
        if outcome == "added":
            report.added += 1
        elif outcome == "duplicate":
            report.duplicates += 1
    return report


def clear_batch(folder: str | Path) -> int:
    """Remove every resume file in a batch; returns how many were removed."""
    removed = 0
    for path in batch_files(folder):
        path.unlink()
        removed += 1
    return removed


class LocalFolderATS(ATSPort):
    def __init__(self, jd_dir: str | Path, resumes_dir: str | Path, default_job_location: str,
                 roles_dir: str | Path = "data/roles", resumes_override: str | Path | None = None) -> None:
        self.jd_dir = Path(jd_dir)
        self.resumes_dir = Path(resumes_dir)  # LEGACY shared folder (fallback only)
        self.roles_dir = Path(roles_dir)
        # An explicit --resumes override wins over everything (power-user/CI path).
        self.resumes_override = Path(resumes_override) if resumes_override else None
        # Local folders carry no location metadata, so every requisition gets the
        # configured default (config.yaml policy.default_jurisdiction; IN for the pilot).
        self.default_job_location = default_job_location
        # candidate_id -> file path, filled by fetch_candidates. fetch_documents
        # needs it because ids are content hashes — you can't reverse a hash back
        # into a path.
        self._paths_by_candidate: dict[str, Path] = {}

    def candidates_folder(self, req: Requisition) -> Path:
        """Which folder this requisition's candidates come from (override > batch > legacy)."""
        if self.resumes_override is not None:
            return self.resumes_override
        batch = batch_dir(self.roles_dir, req.req_id)
        if not batch_files(batch) and batch_files(self.resumes_dir):
            log.info("[LocalFolderATS] batch %s is empty — falling back to the legacy shared "
                     "folder %s (migrate it from the Screen page)", batch, self.resumes_dir)
            return self.resumes_dir
        return batch

    def _own_jd(self, row: Requisition) -> Requisition:
        """Point a DB-sourced row at ITS OWN JD file rather than the shared document.

        `jd_document` is keyed by CONTENT HASH — "a re-uploaded identical JD is one
        document" — and its `source_ref` is overwritten by whichever requisition was
        written last. So two openings for the same role (identical JD text, an
        entirely ordinary thing to have) came back with the SAME `jd_ref`, and every
        per-requisition file derived from that path then collided: the rubric, and
        the `.req.json` sidecar carrying the iCIMS id, work location, lifecycle and
        created_at. Two requisitions read and wrote one another's metadata, which is
        the "state spilling into neighbours" the pilot reported.

        req_id IS the JD stem by construction (the file backend defines it that way),
        so a requisition's own JD is `<jd_dir>/<req_id>.<ext>`. Falls back to whatever
        the document said when no such file exists, so nothing regresses for rows
        whose JD really does live elsewhere."""
        for suffix in (".md", ".txt"):
            own = self.jd_dir / f"{row.req_id}{suffix}"
            if own.exists():
                return row if str(own) == row.jd_ref else row.model_copy(
                    update={"jd_ref": str(own)})
        return row

    def fetch_requisition(self, req_id: str) -> Requisition | None:
        """ONE requisition by its code, without building the whole list.

        Under the DB backend the list costs a sidecar read and a JD resolve per
        requisition in the deployment, and every per-requisition route — the polls
        most of all — was paying that just to find its own row (2026-09-10). The
        file backend already caches the list on the jds/ directory mtime, so it
        answers from there."""
        if not _use_requisitions_db():
            return next((r for r in self.fetch_requisitions() if r.req_id == req_id), None)

        from resume_screener.db.repositories import requisitions as req_repo
        from resume_screener.db.session import session_scope

        with session_scope() as session:
            row = req_repo.get_requisition(session, req_id)
        if row is None:
            # A role the DB does not know (created file-side, or mid-migration):
            # fall back to the full list rather than reporting it missing.
            return next((r for r in self.fetch_requisitions() if r.req_id == req_id), None)
        return hydrate_sidecar_only_fields([self._own_jd(row)])[0]

    def fetch_requisitions(self) -> list[Requisition]:
        # Role identity from the DB when requisitions_backend=db; candidates/documents
        # (below) still read the on-disk batches either way.
        if _use_requisitions_db():
            from resume_screener.db.repositories import requisitions as req_repo
            from resume_screener.db.session import session_scope

            # Cached exactly like the file branch below, and for the same reason: this
            # list is rebuilt on almost every API call, and building it costs a sidecar
            # read and a JD resolve PER REQUISITION IN THE DEPLOYMENT — 400 share round
            # trips for 200 roles, on every dashboard load (2026-09-10).
            try:
                dir_mtime = os.stat(self.jd_dir).st_mtime_ns
            except OSError:
                dir_mtime = None
            with session_scope() as session:
                stamp = req_repo.table_stamp(session)
            key = f"db:{self.jd_dir}"
            state = (dir_mtime, stamp)
            now = time.time()
            hit = _REQ_LIST_CACHE.get(key)
            if hit is not None and hit[0] == state and now - hit[1] < _REQ_LIST_TTL_SECONDS:
                return list(hit[2])
            with session_scope() as session:
                rows = req_repo.list_requisitions(session)
            out = hydrate_sidecar_only_fields([self._own_jd(r) for r in rows])
            _REQ_LIST_CACHE[key] = (state, now, out)
            return out
        # The list is rebuilt on almost every API call (authorisation resolves the
        # requisition first), and building it read every JD's markdown for its title
        # plus every sidecar — ~180 round trips to the share per request, including
        # the two-second polls. It is a pure function of the directory, so it is
        # cached on the directory's mtime (any create/delete/replace bumps it — the
        # sidecar writer's temp+replace included) with a short TTL as the net for an
        # in-place edit by another process; in-process writers invalidate explicitly.
        try:
            dir_mtime = os.stat(self.jd_dir).st_mtime_ns
        except OSError:
            return []
        key = str(self.jd_dir)
        now = time.monotonic()
        hit = _REQ_LIST_CACHE.get(key)
        if hit is not None and hit[0] == dir_mtime and now - hit[1] < _REQ_LIST_TTL_SECONDS:
            return list(hit[2])
        requisitions = []
        with os.scandir(self.jd_dir) as it:
            entries = sorted((e for e in it if e.is_file()), key=lambda e: e.name)
        for e in entries:
            p = self.jd_dir / e.name
            if not (p.suffix.lower() in JD_SUFFIXES and not p.name.startswith(".")):
                continue
            meta = load_req_meta(p)
            requisitions.append(Requisition(
                req_id=p.stem,
                role_title=_jd_title(p),
                jd_ref=str(p),
                # Per-role job location from the .req.json sidecar (P29); sidecar-less
                # test roles fall back to the configured default (the pilot's IN).
                job_location=meta.get("job_location") or self.default_job_location,
                country=(str(meta.get("country") or "").strip().upper() or None),
                us_state=(str(meta.get("us_state") or "").strip().upper() or None),
                icims_req_id=meta.get("icims_req_id") or None,
                work_location=_work_location(meta),
            ))
        _REQ_LIST_CACHE[key] = (dir_mtime, now, requisitions)
        return list(requisitions)

    def fetch_candidates(self, req: Requisition) -> list[CandidateRef]:
        candidates = []
        for path in batch_files(self.candidates_folder(req)):
            # id from file BYTES (not name): stable across renames, changes on re-export.
            cid = candidate_id_for(path.read_bytes())
            self._paths_by_candidate[cid] = path
            candidates.append(CandidateRef(candidate_id=cid, source="local", label=path.stem))
        return candidates

    def fetch_documents(self, candidate: CandidateRef) -> list[DocumentRef]:
        path = self._paths_by_candidate.get(candidate.candidate_id)
        if path is None:
            # Unknown id (e.g. fetch_candidates never ran in this process) —
            # empty list, not an exception: the caller treats it like "no docs".
            return []
        return [
            DocumentRef(
                doc_id=f"{candidate.candidate_id}-resume",
                candidate_id=candidate.candidate_id,
                file_name=path.name,
                media_type=path.suffix.lower().lstrip("."),
                uri=str(path),
            )
        ]

    # Write-back surfaces exist so the run pipeline can call them unconditionally;
    # locally they only log. The iCIMS adapter will post score/band/result-URL here.
    def write_back(self, req: Requisition, candidate_id: str, payload: dict[str, Any]) -> None:
        log.info("[LocalFolderATS] write_back(req=%s, candidate=%s) ignored — PoC no-op", req.req_id, candidate_id)

    def on_stage_change(self, req: Requisition, candidate_id: str, stage: str) -> None:
        log.info("[LocalFolderATS] on_stage_change(req=%s, candidate=%s, stage=%s) ignored — PoC no-op", req.req_id, candidate_id, stage)


def _jd_title(path: Path) -> str:
    """First markdown heading if present, else the file stem."""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("#"):
                return line.lstrip("# ").strip() or path.stem
    except OSError:
        pass  # unreadable JD -> fall back to the stem; the run will fail later with a clearer error
    return path.stem


__all__ = [
    "BatchError", "ImportReport", "LocalFolderATS", "add_bytes_to_batch", "batch_dir",
    "batch_files", "clear_batch", "import_folder_into_batch", "load_req_meta",
    "req_meta_path", "role_slug", "save_req_meta",
]
