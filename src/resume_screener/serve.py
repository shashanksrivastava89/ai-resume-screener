"""The container's entrypoint: serve the app as an unprivileged user (2026-10-14).

The image starts as root for one reason: to find out whether the app can run without
it. All mutable state lives under DATA_ROOT — on App Service, an Azure Files share the
PLATFORM mounts — and the platform documents no ownership or mode for that mount. So
this checks, in a throwaway child process already switched to the `app` user
(uid/gid 10001), that it can create a file in DATA_ROOT and append to the event log.
If it can, the server drops to that user for good before it starts; if not, it stays
root and says so in the log, rather than failing to start on a share it cannot write.
Workers the app spawns (batch preparation, screening runs) inherit whichever it is.

Python rather than a shell script: the image is built from a Windows checkout, where a
script would carry CRLF line endings into the container and fail to start.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

APP_UID = APP_GID = 10001
APP_HOME = "/home/app"

log = logging.getLogger("resume_screener.serve")


def _drop_privileges() -> None:
    os.setgroups([])
    os.setgid(APP_GID)
    os.setuid(APP_UID)
    os.environ["HOME"] = APP_HOME


def _writable_as_app(root: Path) -> bool:
    """Whether uid 10001 can create a file in `root` and append to its event log —
    tried in a child process, so the parent keeps the privileges it may still need."""
    pid = os.fork()
    if pid == 0:  # child
        try:
            _drop_privileges()
            root.mkdir(parents=True, exist_ok=True)
            probe = root / f".write-probe-{os.getpid()}"
            probe.write_bytes(b"")
            probe.unlink()
            events = root / "signals" / "events.jsonl"
            if events.exists():
                with open(events, "ab"):
                    pass
            os._exit(0)
        except BaseException:  # noqa: BLE001 — any failure means "not writable"
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status) == 0


def _is_root() -> bool:
    return os.name == "posix" and os.getuid() == 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    root = Path(os.environ.get("DATA_ROOT") or ".").resolve()
    if _is_root():
        if _writable_as_app(root):
            _drop_privileges()
            log.info("Serving as uid %d (unprivileged); %s is writable.", APP_UID, root)
        else:
            log.warning("%s is not writable by uid %d — serving as ROOT. Check the storage "
                        "mount's permissions (see docs/13-UAT-DEPLOYMENT.md).", root, APP_UID)
    import uvicorn

    # Port 8000, fixed: App Service routes to WEBSITES_PORT=8000 and may set PORT for
    # its own images, which must not move us.
    # Every interface, deliberately: this is the container's own network namespace,
    # and App Service (Easy Auth in front) is the only thing that reaches it.
    every_interface = "0.0.0.0"  # nosec B104
    uvicorn.run("resume_screener.api.app:app", host=every_interface, port=8000,
                server_header=False)


if __name__ == "__main__":
    main()
