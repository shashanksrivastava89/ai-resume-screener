"""A cache key that names a file's location without asking the filesystem (2026-10-14).

Several process-wide caches key on a file's ABSOLUTE path, because a relative key served
one workspace's answer to another (the tests chdir per test; a seeded data root carries
its source's size and mtime). They used `Path.resolve()` for that — which on Linux
`lstat()`s path components, every request, on the hosted share. That is the
difference between Windows (where the fs-op budgets passed) and the Linux CI runner
(where four of them failed): resolve() is free to count on one and not the other.

`os.path.abspath` is string work against the working directory, which
dataroot.bootstrap() fixes for the life of the process. It does not follow symlinks:
two links to one file make two cache entries — a miss, never a wrong answer. A
writer's invalidator must build its key with this same function.
"""

from __future__ import annotations

import os


def canon(path: str | os.PathLike) -> str:
    return os.path.abspath(os.fspath(path))
