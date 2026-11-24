"""P26: cross-process file locks around shared mutable files."""

from __future__ import annotations

import threading

from resume_screener.locking import file_lock


def test_lock_serializes_writers(tmp_path):
    target = tmp_path / "log.jsonl"
    order: list[str] = []
    release = threading.Event()

    def holder():
        with file_lock(target):
            order.append("holder-acquired")
            release.wait(timeout=5)
            order.append("holder-released")

    thread = threading.Thread(target=holder)
    thread.start()
    while "holder-acquired" not in order:  # wait until the lock is actually held
        pass
    release.set()
    with file_lock(target):  # blocks until the holder finishes
        order.append("second-acquired")
    thread.join(timeout=5)
    assert order == ["holder-acquired", "holder-released", "second-acquired"]


def test_lock_uses_a_sidecar_not_the_data_file(tmp_path):
    target = tmp_path / "registry.yaml"
    with file_lock(target):
        # the data file itself is untouched (atomic os.replace onto it stays safe)
        assert not target.exists()
        assert (tmp_path / "registry.yaml.lock").exists()


def test_appends_through_the_lock_land_intact(tmp_path):
    target = tmp_path / "decisions.jsonl"
    for i in range(3):
        with file_lock(target), target.open("a", encoding="utf-8") as handle:
            handle.write(f"line-{i}\n")
    assert target.read_text(encoding="utf-8").splitlines() == ["line-0", "line-1", "line-2"]
