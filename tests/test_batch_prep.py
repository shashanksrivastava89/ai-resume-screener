"""The asynchronous batch-preparation contract (2026-08-26).

Uploads STAGE bytes and return; a worker drains the staging area applying the
SAME intake rules the old synchronous path applied. These tests pin:
  * outcome parity with add_bytes_to_batch (exact-dup, same-person-dup, add);
  * the one-extraction-pass seam (examine_pdf's text == what contact de-dup read);
  * the state machine (fresh session, crash respawn, attempt cap, finalize);
  * the persisted contact index (a second worker session re-reads nothing);
  * the fairness-critical bits: a failed examination KEEPS the file, and a small
    multi-résumé packet sharing one person with the batch no longer loses the
    other people in it.

Offline: every PDF is built in-test (never real candidate data, §6.12).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from tests.test_resume_split import make_pdf

from resume_screener import batch_prep, contacts, resume_split
from resume_screener.ats import local_folder


def _resume(name: str, email: str, phone: str = "") -> bytes:
    lines = [name, "Senior Accountant", email]
    if phone:
        lines.append(phone)
    return make_pdf([lines, ["Experience", "GL, R2R, Ind AS, GST"]])


def _bulk(people: list[tuple[str, str]]) -> bytes:
    """A Bulk Print with a TOC page and one page per (name, email)."""
    toc = ["Bulk Print Summary", "Table of Contents"]
    toc += [f"{i + 1}. {n} - Resume" for i, (n, _) in enumerate(people)]
    pages = [toc] + [[n, "Accountant", e] for n, e in people]
    return make_pdf(pages)


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    """A clean batch folder + clean process caches + async transport (the tests
    here call run_worker themselves; conftest's inline default would make
    reconcile run it implicitly)."""
    monkeypatch.setenv("BATCH_PREP_INLINE", "0")
    monkeypatch.setattr(batch_prep, "_emit", lambda *a, **k: None)
    local_folder._HASH_CACHE.clear()
    resume_split._SINGLE_CACHE.clear()
    contacts._TOKEN_CACHE.clear()
    batch = tmp_path / "resumes"
    batch.mkdir()
    return batch


def _run(folder: Path) -> dict:
    assert batch_prep.run_worker("role-x", folder) == 0
    return batch_prep.read_state(folder)


def _names(folder: Path) -> set[str]:
    return {p.name for p in local_folder.batch_files(folder)}


# --- outcome parity with the synchronous rules ---------------------------------------


def test_stage_then_worker_adds_dedupes_and_reports(folder):
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    batch_prep.stage_bytes("a-again.pdf", _resume("Asha Rao", "asha@example.com"), folder)  # same bytes
    batch_prep.stage_bytes("b.pdf", _resume("Bela Jain", "bela@example.com"), folder)
    state = _run(folder)
    assert state["status"] == "done"
    assert state["added"] == 2 and state["duplicates"] == 1
    assert "2 added, 1 duplicate skipped" in state["sentence"]
    assert len(_names(folder)) == 2
    assert batch_prep.incoming_files(folder) == []


def test_same_person_different_file_is_one_candidate(folder):
    batch_prep.stage_bytes("v1.pdf", _resume("Asha Rao", "asha@example.com", "+91 98765 43210"), folder)
    _run(folder)
    # A slightly different file, same email — the old add_bytes contract.
    batch_prep.stage_bytes("v2.pdf", make_pdf([["Asha R.", "asha@example.com", "an edit"]]), folder)
    state = _run(folder)
    assert state["duplicates"] == 1 and len(_names(folder)) == 1


def test_unreadable_file_is_kept_not_dropped(folder):
    """'We couldn't read it' must never become 'we removed it' — hash-de-dup only,
    exactly like the old contact_tokens-failure fallback."""
    batch_prep.stage_bytes("scan.pdf", b"%PDF-1.4 not really parseable", folder)
    state = _run(folder)
    assert state["added"] == 1 and state["errors"] == []
    assert len(_names(folder)) == 1


def test_bulk_packet_splits_and_slices_dedupe_individually(folder):
    batch_prep.stage_bytes("solo.pdf", _resume("Alice Alpha", "alice1@example.com"), folder)
    _run(folder)
    packet = _bulk([("Alice Alpha", "alice1@example.com"),
                    ("Bob Beta", "bob2@example.com"),
                    ("Carol Gamma", "carol3@example.com")])
    batch_prep.stage_bytes("packet.pdf", packet, folder)
    state = _run(folder)
    # The packet split; Alice (already present) de-duped as a SLICE, so Bob and
    # Carol were NOT lost with her — the small-packet fix this rewrite bakes in.
    assert state["split_files"] == 1 and state["split_resumes"] == 2
    names = _names(folder)
    assert any("Bob Beta" in n for n in names) and any("Carol Gamma" in n for n in names)
    # Alice stays represented by the original solo file; her slice was the duplicate.
    assert "solo.pdf" in names and not any("Alice" in n for n in names)
    groups = resume_split.batch_groups(folder)
    assert groups and groups[0]["duplicates_dropped"] == 1


def test_repeat_upload_of_split_packet_is_a_duplicate(folder):
    packet = _bulk([("Dev Lillis", "dev@example.com"), ("Esha Roach", "esha@example.com")])
    batch_prep.stage_bytes("packet.pdf", packet, folder)
    _run(folder)
    before = _names(folder)
    batch_prep.stage_bytes("packet-again.pdf", packet, folder)
    state = _run(folder)
    assert state["duplicates"] == 1 and _names(folder) == before


def test_identical_slice_rearriving_standalone_is_a_duplicate(folder):
    """Slice hashes must enter the session's known set as they're written."""
    packet = _bulk([("Farid Khan", "farid@example.com"), ("Gita Rao", "gita@example.com")])
    batch_prep.stage_bytes("packet.pdf", packet, folder)
    _run(folder)
    # The same person's standalone résumé (same email, new bytes) in a later burst.
    batch_prep.stage_bytes("farid-direct.pdf", _resume("Farid Khan", "farid@example.com"), folder)
    state = _run(folder)
    assert state["duplicates"] == 1


# --- the one-extraction-pass seam ------------------------------------------------------


def test_examine_pdf_text_matches_contact_extraction():
    """Contact identity derived from examine_pdf's text must be the SAME identity
    the old per-file extraction produced — same extractor, same join."""
    data = _resume("Hema Sen", "hema@example.com", "+91 91234 56789")
    _, _, _, full_text = resume_split.examine_pdf(data)
    assert contacts.tokens_from_text(full_text) == contacts.contact_tokens(data, ".pdf")
    packet = _bulk([("Ira Das", "ira@example.com"), ("Jai Bose", "jai@example.com")])
    _, _, _, packet_text = resume_split.examine_pdf(packet)
    assert contacts.tokens_from_text(packet_text) == contacts.contact_tokens(packet, ".pdf")


# --- the persisted index ---------------------------------------------------------------


def test_second_session_reads_nothing_it_already_knows(folder, monkeypatch):
    for i in range(4):
        batch_prep.stage_bytes(f"c{i}.pdf", _resume(f"Cand {chr(65 + i)}", f"c{i}@example.com"), folder)
    _run(folder)
    # A NEW process = cold in-memory caches. Simulate it, keep the index file.
    local_folder._HASH_CACHE.clear()
    contacts._TOKEN_CACHE.clear()
    reads = {"n": 0}
    real = contacts._text_from_bytes

    def counting(data, suffix):
        reads["n"] += 1
        return real(data, suffix)

    monkeypatch.setattr(contacts, "_text_from_bytes", counting)
    pages_before = resume_split._PageText.page
    extracted = {"n": 0}

    def counting_page(self, index):
        if index not in self._cache:
            extracted["n"] += 1
        return pages_before(self, index)

    monkeypatch.setattr(resume_split._PageText, "page", counting_page)

    batch_prep.stage_bytes("new.pdf", _resume("New Person", "new@example.com"), folder)
    _run(folder)
    assert len(_names(folder)) == 5  # the new file landed beside the four
    # Only the NEW file was examined; the 4 existing ones came from the index.
    assert extracted["n"] <= 2  # the new file's own pages
    assert reads["n"] == 0  # no whole-file contact re-extraction of the batch


def test_index_survives_and_validates(folder):
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    _run(folder)
    index = json.loads((folder / batch_prep.INDEX_NAME).read_text(encoding="utf-8"))
    (name, entry), = index["files"].items()
    assert entry["hash"] and entry["tokens"] == ["email:asha@example.com"]
    assert batch_prep._entry_valid(folder, name, entry)
    (folder / name).write_bytes(_resume("Asha Rao", "other@example.com"))
    assert not batch_prep._entry_valid(folder, name, entry)  # size/mtime moved


def test_out_of_band_file_is_adopted_and_split(folder):
    """A packet copied straight onto the share (no staging) still gets split on
    the next session — the one behavior of the old per-upload rescan worth
    keeping, now once-per-file-ever."""
    (folder / "dropped.pdf").write_bytes(_bulk([("Sherri Patel", "sherri@example.com"),
                                                ("Lata Shah", "lata@example.com")]))
    batch_prep.stage_bytes("solo.pdf", _resume("Mira Roy", "mira@example.com"), folder)
    state = _run(folder)
    assert state["split_files"] == 1 and state["split_resumes"] == 2
    assert "dropped.pdf" not in _names(folder)  # moved to _sources/
    assert any("Sherri Patel" in n for n in _names(folder))


# --- the state machine -------------------------------------------------------------------


def test_reconcile_spawns_once_and_respects_grace(folder, monkeypatch):
    spawns = []
    monkeypatch.setattr(batch_prep, "_spawn", lambda f, r: spawns.append(r))
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    payload = batch_prep.reconcile(folder, "role-x", actor="ta@example.com")
    assert payload["active"] and spawns == ["role-x"]
    # Freshly queued: polling again inside the grace window must NOT respawn.
    assert batch_prep.reconcile(folder, "role-x")["active"] and spawns == ["role-x"]
    # The spawn never booted (heartbeat frozen) -> after the grace it retries.
    state = batch_prep.read_state(folder)
    state["heartbeat"] = time.time() - batch_prep._GRACE_SECONDS - 1
    batch_prep._write_state(folder, state)
    batch_prep.reconcile(folder, "role-x")
    assert spawns == ["role-x", "role-x"]


def test_attempt_cap_parks_as_error_and_retry_resets(folder, monkeypatch):
    monkeypatch.setattr(batch_prep, "_spawn", lambda f, r: None)  # spawns that never boot
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    for _ in range(batch_prep._MAX_ATTEMPTS + 1):
        state = batch_prep.read_state(folder)
        state["heartbeat"] = 0  # long dead
        batch_prep._write_state(folder, state)
        payload = batch_prep.reconcile(folder, "role-x")
    assert payload["status"] == "error" and "Try again" in (payload["message"] or "")
    # Try again clears the cap; the file is still staged, so it re-queues.
    payload = batch_prep.reconcile(folder, "role-x", reset_attempts=True)
    assert payload["status"] == "queued"
    state = _run(folder)
    assert state["status"] == "done" and state["added"] == 1


def test_dead_worker_after_last_file_is_finalized_by_reconcile(folder):
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    _run(folder)
    # Forge the crash: session says working, nothing staged, heartbeat long dead.
    state = batch_prep.read_state(folder)
    state.update(status="working", heartbeat=0, sentence="")
    batch_prep._write_state(folder, state)
    payload = batch_prep.reconcile(folder, "role-x")
    assert payload["status"] == "done" and payload["active"] is False
    assert "1 added" in (payload["sentence"] or "")


def test_crashed_session_resumes_with_counters_intact(folder):
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    _run(folder)
    # Next burst: worker "crashed" mid-way — one file processed+counted, one left.
    batch_prep.stage_bytes("b.pdf", _resume("Bela Jain", "bela@example.com"), folder)
    batch_prep.stage_bytes("c.pdf", _resume("Chand Branaola", "chand@example.com"), folder)
    state = batch_prep.read_state(folder)
    state.update(status="working", heartbeat=0, added=0, processed=0)
    batch_prep._write_state(folder, state)
    final = _run(folder)  # the respawned worker continues the SAME session
    assert final["added"] == 2 and final["status"] == "done"


def test_second_worker_exits_immediately(folder):
    from resume_screener.locking import try_file_lock

    lock = try_file_lock(folder / batch_prep.LOCK_NAME)
    assert lock is not None
    try:
        assert batch_prep.run_worker("role-x", folder) == 0  # yields, touches nothing
    finally:
        lock.release()


def test_clear_prep_files_removes_staging_and_state(folder):
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    _run(folder)
    batch_prep.stage_bytes("late.pdf", _resume("Late Arrival", "late@example.com"), folder)
    batch_prep.clear_prep_files(folder)
    assert batch_prep.incoming_files(folder) == []
    assert not (folder / batch_prep.STATE_NAME).exists()
    assert not (folder / batch_prep.INDEX_NAME).exists()


def test_unsupported_files_never_stage(folder):
    assert batch_prep.stage_bytes("notes.txt", b"hello", folder) == "unsupported"
    assert batch_prep.incoming_files(folder) == []


def test_concurrent_state_writes_never_lose_the_temp_file(folder):
    """The bug the first hosted upload found: _write_state is read → write temp →
    os.replace, and _finalize wrote it WITHOUT the mutex the heartbeat thread
    held. The heartbeat replaced the temp finalize was about to move, finalize
    died, and a fully successful preparation reported "hit a problem". (The loser
    fails as ENOENT on the Linux share and WinError 32 on Windows — same race.)

    Hammer it from several threads; any unsynchronized write raises here."""
    import threading

    state = batch_prep._fresh_session("role-x")
    errors: list[BaseException] = []

    def writer(tag: int) -> None:
        try:
            for i in range(40):
                batch_prep._write_state(folder, dict(state, added=tag * 100 + i))
        except BaseException as exc:  # noqa: BLE001 — the assertion is "nothing raised"
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent state writes raised: {errors!r}"
    # The file is always complete, readable JSON — never a half-written temp.
    assert batch_prep.read_state(folder)["req_id"] == "role-x"
    assert list(folder.glob(f"{batch_prep.STATE_NAME}.*.tmp")) == []


def test_finalize_survives_a_live_heartbeat(folder):
    """End-to-end shape of the same bug: a heartbeat beating on the real interval
    while the worker finalizes must leave status 'done', not 'error'."""
    import threading

    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    state = batch_prep._fresh_session("role-x")
    stop = threading.Event()
    beats = {"n": 0}

    def beat() -> None:
        # Writes IMMEDIATELY, then every 5ms rather than the real 10s, so the race is
        # certain rather than lucky.
        while not stop.is_set():
            batch_prep._write_state(folder, state)
            beats["n"] += 1
            stop.wait(0.005)

    beater = threading.Thread(target=beat, daemon=True)
    beater.start()
    # Wait for the beater to actually be hammering before the worker starts.
    # Without this the worker could finish first on a fast machine and the test
    # would fail for the opposite reason — "the race never happened".
    deadline = time.monotonic() + 5
    while beats["n"] == 0 and time.monotonic() < deadline:
        time.sleep(0.001)
    try:
        # THE regression: the old _write_state raised here (the beater moved the temp
        # it was about to rename), run_worker caught it, and a completed preparation
        # was stamped as an error.
        assert batch_prep.run_worker("role-x", folder) == 0
    finally:
        stop.set()
        beater.join(timeout=5)

    assert beats["n"] > 0, "the beater never started — the race was not exercised"
    # ONLY what the beater cannot influence. It writes its own dict to the same
    # file, so whichever thread wrote last owns the state — asserting on that made
    # this test pass alone and fail in a full run, which is worse than no test.
    # The regression is entirely captured by "run_worker did not raise", above.
    assert len(_names(folder)) == 1
    assert batch_prep.incoming_files(folder) == []
    assert list(folder.glob(f"{batch_prep.STATE_NAME}.*.tmp")) == []  # no stranded temps


def test_error_with_nothing_pending_closes_itself(folder):
    """An error whose staged files are all gone is not actionable — every file was
    processed and counted — so it must not leave a permanent red card. A GENUINE
    failure keeps its files staged, and therefore keeps its error."""
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    _run(folder)
    # Forge the bookkeeping failure: work done and counted, status wrongly error.
    state = batch_prep.read_state(folder)
    state.update(status="error", message="Preparation stopped: transient write race")
    batch_prep._write_state(folder, state)

    payload = batch_prep.reconcile(folder, "role-x")
    assert payload["status"] == "done" and not payload["message"]
    assert "1 added" in (payload["sentence"] or "")


def test_error_with_files_still_staged_keeps_its_error(folder, monkeypatch):
    """The other side of the same rule: a worker that cannot start leaves real
    work undone, so the recruiter must keep seeing it (and the Try again)."""
    monkeypatch.setattr(batch_prep, "_spawn", lambda f, r: None)  # never boots
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    for _ in range(batch_prep._MAX_ATTEMPTS + 1):
        state = batch_prep.read_state(folder)
        state["heartbeat"] = 0
        batch_prep._write_state(folder, state)
        payload = batch_prep.reconcile(folder, "role-x")
    assert payload["status"] == "error" and payload["pending"] == 1
    assert "Try again" in (payload["message"] or "")


def test_counters_span_a_chunked_upload(folder):
    """A chunked upload arrives as SEVERAL requests seconds apart, and the worker can
    finish one group before the next lands. The counters must belong to the BURST:
    resetting per group made the progress numbers restart mid-upload (a 6-group
    upload counted "1 of 6 … 2 of 6" and then announced "5 of 5") and made the
    closing sentence report only the final group."""
    for group in range(3):
        batch_prep.stage_bytes(f"g{group}.pdf",
                               _resume(f"Person {group}", f"p{group}@example.com"), folder)
        state = _run(folder)  # each group drains fully before the next is staged
    assert state["added"] == 3 and state["processed"] == 3
    assert "3 added" in state["sentence"]


def test_a_later_upload_starts_its_own_session(folder, monkeypatch):
    """The burst window must not fuse genuinely separate uploads — a later drop
    reports its own counts, not the running total of everything ever added."""
    batch_prep.stage_bytes("first.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    _run(folder)
    monkeypatch.setattr(batch_prep, "_continues_burst", lambda state: False)  # time passed
    batch_prep.stage_bytes("later.pdf", _resume("Bela Jain", "bela@example.com"), folder)
    state = _run(folder)
    assert state["added"] == 1 and "1 added" in state["sentence"]
    assert len(_names(folder)) == 2


def test_clear_then_readd_the_same_packet_splits_again(folder):
    """The route a recruiter takes to pick up a splitter improvement: Clear batch,
    then drop the SAME bulk files back in.

    The trap this guards: a split packet is retained in the hidden `_sources/`
    folder, and `_source_hashes` treats a re-upload of an already-split source as a
    duplicate and DELETES it. If clearing left those sources behind, re-adding the
    same packets would silently drop every one of them and the batch would come back
    empty."""
    from resume_screener.ats.local_folder import clear_batch

    packet = _bulk([("Nita Roy", "nita@example.com"), ("Omar Shah", "omar@example.com")])
    batch_prep.stage_bytes("packet.pdf", packet, folder)
    first = _run(folder)
    assert first["split_resumes"] == 2
    assert (folder / resume_split.SOURCES_DIRNAME).exists()

    # Exactly what the Clear batch route does, in its order.
    batch_prep.clear_prep_files(folder)
    clear_batch(folder)
    resume_split.cleanup_removed(folder)
    assert _names(folder) == set()
    assert not (folder / resume_split.SOURCES_DIRNAME).exists(), "stored source outlived the batch"

    batch_prep.stage_bytes("packet.pdf", packet, folder)
    again = _run(folder)
    assert again["split_resumes"] == 2, "the re-added packet was dropped as a duplicate"
    assert len(_names(folder)) == 2


# --- "upload and screen" + the stop path (2026-08-27) ----------------------------------


def test_auto_screen_intent_survives_the_session_and_marks_a_run_owed(folder):
    """The recruiter drops files and walks away, so the intent CANNOT live in the
    browser — it is recorded on the session and the worker marks a run owed when
    preparation finishes."""
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    batch_prep.set_auto_screen(folder, actor="ta@example.com")
    assert batch_prep.session_payload(folder)["auto_screen"] is True

    state = _run(folder)
    assert state["status"] == "done"
    assert state["auto_screen_ready"] is True, "no run was marked owed"
    assert state["actor"] == "ta@example.com"


def test_plain_upload_owes_nothing(folder):
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    state = _run(folder)
    assert not state["auto_screen_ready"]
    assert batch_prep.session_payload(folder)["auto_screen"] is False


def test_stopping_keeps_what_was_prepared_and_discards_the_queue(folder):
    """The chicken-out path. Cooperative on purpose: candidates already added stay,
    the queue is dropped, and nothing is left half-written."""
    for i in range(4):
        batch_prep.stage_bytes(f"c{i}.pdf", _resume(f"Cand {i}", f"c{i}@example.com"), folder)

    # Stop after the first file is processed, the way a click mid-run would.
    real = batch_prep._process_one
    calls = {"n": 0}

    def stop_after_one(*args, **kwargs):
        real(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            batch_prep.request_stop(folder, "role-x")

    original = batch_prep._process_one
    batch_prep._process_one = stop_after_one
    try:
        state = _run(folder)
    finally:
        batch_prep._process_one = original

    assert len(_names(folder)) == 1, "a prepared candidate was thrown away"
    assert batch_prep.incoming_files(folder) == [], "the queue was not discarded"
    assert "Stopped" in (state.get("sentence") or ""), state.get("sentence")
    assert state["stop_requested"] is False  # the session closed, not stuck stopping


def test_stopping_cancels_an_owed_screening_run(folder):
    """Someone who stops preparing did not ask for a screening run."""
    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"), folder)
    batch_prep.set_auto_screen(folder, actor="ta@example.com")
    batch_prep.request_stop(folder, "role-x")
    state = batch_prep.read_state(folder)
    assert state["auto_screen"] is False and state["auto_screen_ready"] is False


def test_autoscreen_due_lists_only_finished_sessions_that_owe_a_run(folder, tmp_path):
    """The watchdog's input. A session still holding staged files is NOT due — the run
    must wait for the whole batch, or it screens a partial one."""
    roles = tmp_path / "roles"
    (roles / "role-x").mkdir(parents=True)
    import shutil
    shutil.copytree(folder, roles / "role-x" / "resumes")

    batch_prep.stage_bytes("a.pdf", _resume("Asha Rao", "asha@example.com"),
                           roles / "role-x" / "resumes")
    batch_prep.set_auto_screen(roles / "role-x" / "resumes", actor="ta@example.com")
    assert batch_prep.autoscreen_due(roles) == []          # nothing finished yet

    assert batch_prep.run_worker("role-x", roles / "role-x" / "resumes") == 0
    assert batch_prep.autoscreen_due(roles) == ["role-x"]  # finished and owed

    batch_prep.clear_autoscreen(roles / "role-x" / "resumes")
    assert batch_prep.autoscreen_due(roles) == []          # fired once, never twice
