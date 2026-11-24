"""The people directory (2026-09-06): who is behind an email or UPN.

WHY. The engine stores exactly one identity per action — the sign-in address —
and on the hosted tenant that is a payroll UPN (``u120729@example.com``).
Every surface that showed "who did this" therefore showed an id nobody could put
a face to, while the very same person's real name had crossed the wire in the
``name`` claim of their sign-in and been discarded. This module keeps what the
app LEARNS about people, so the next surface can say "Shashank Srivastava" and
put the UPN on hover.

WHAT IT STORES. ``signals/people.jsonl`` — one JSON line per learning, latest
per email wins on read (the append-only pattern of every other log here):
``{email (lower), name, job_title, photo (data URI or null), source, ts}``.

WHERE IT LEARNS FROM, in order of trust:
  graph  — Microsoft Graph (the directory of record: display name, job title,
           photo). Reached ONLY when ``PEOPLE_DIRECTORY=graph`` and the process
           can obtain a Graph token through the same ``DefaultAzureCredential``
           path as Key Vault. Today that needs the managed identity granted
           ``User.Read.All`` by a Cloud Admin (the ISM-274462 class of consent),
           so the provider is a seam that degrades to local, with the reason
           kept for ``status()``. Nothing here fails because Graph is absent.
  sso    — the ``name`` claim on each sign-in (``/api/me`` calls ``remember``).
           Free, already flowing, and it covers everyone who has ever opened the
           app — which is exactly the set of people whose UPNs appear in it.
  typed  — a name a recruiter typed next to an email (the hiring manager on a
           requisition). Lowest trust: it never overwrites a signed-in name.

WHAT IT IS NOT. Not an identity source (``rbac.py`` alone decides who someone is
and what they may do) and not PII the app did not already hold: every email here
was already on a decision, a comment or a sidecar. Photos are stored only when
Graph supplies them, as small data URIs, and only for people who use the app.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from resume_screener import reqcache
from resume_screener.locking import file_lock

log = logging.getLogger(__name__)

PEOPLE_LOG = Path("signals/people.jsonl")

# The eight semantic hues are the palette (tokens.css); avatars use the six that
# carry no fixed meaning — rose is destructive-only and slate reads as "not a fit".
AVATAR_HUES = ("green", "violet", "blue", "teal", "amber", "orange")

# How much each source is trusted for the NAME. A typed name never overwrites a
# signed-in one; Graph overwrites both (it is the directory of record).
_SOURCE_RANK = {"typed": 0, "sso": 1, "graph": 2}

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(email: str | None) -> str:
    return str(email or "").strip().lower()


def initials(name: str, email: str = "") -> str:
    """Two letters for an avatar: the first letters of the first two words of the
    name, else the first letter of the address. Never empty."""
    parts = [p for p in str(name or "").replace("-", " ").replace("_", " ").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if parts:
        return parts[0][:2].upper()
    local = _key(email).split("@", 1)[0]
    return (local[:1] or "?").upper()


def avatar_hue(email: str) -> str:
    """A stable colour per person, from a hash of the address — the same person is
    the same colour on every surface and in every session."""
    # Picks a colour, nothing more — and changing the hash would recolour everyone.
    # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
    digest = hashlib.sha1(_key(email).encode("utf-8"), usedforsecurity=False).digest()
    return AVATAR_HUES[digest[0] % len(AVATAR_HUES)]


def name_shaped(email: str) -> bool:
    """Whether the address's local part looks like a name ("arjun.test") rather
    than a payroll id ("u120729"). Decides whether humanising the address is an
    improvement or an insult."""
    local = _key(email).split("@", 1)[0]
    return any(sep in local for sep in "._-+")


# --- the store ---------------------------------------------------------------------

# path -> (size, mtime_ns, {email: record}); append-only, so a stale hit is impossible.
_DIR_CACHE: dict[str, tuple[int, int, dict[str, dict]]] = {}


def _parsed(path: Path) -> dict[str, dict]:
    try:
        st = path.stat()
    except OSError:
        return {}
    key = str(Path(path).resolve())
    hit = _DIR_CACHE.get(key)
    if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return hit[2]
    people: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue  # a half-written line never takes the directory down
        if isinstance(rec, dict) and _key(rec.get("email")):
            rec["email"] = _key(rec["email"])
            people[rec["email"]] = rec  # latest line per email wins
    _DIR_CACHE[key] = (st.st_size, st.st_mtime_ns, people)
    return people


@reqcache.memo(key=lambda log_path=PEOPLE_LOG: str(log_path))
def directory(log_path: str | Path = PEOPLE_LOG) -> dict[str, dict]:
    """{email: record} for everyone the app knows. Read once per request."""
    return _parsed(Path(log_path))


def lookup(email: str, log_path: str | Path = PEOPLE_LOG) -> dict | None:
    return directory(log_path).get(_key(email))


def display_name(email: str, log_path: str | Path = PEOPLE_LOG) -> str:
    """The directory's name for an address, or "" when it has none."""
    rec = lookup(email, log_path)
    return str((rec or {}).get("name") or "").strip()


def remember(
    email: str,
    *,
    name: str = "",
    job_title: str = "",
    photo: str | None = None,
    country: str = "",
    source: str = "sso",
    log_path: str | Path = PEOPLE_LOG,
) -> dict | None:
    """Record what was just learned about a person. Writes ONLY when it adds
    something — a new person, a better-sourced or changed name, a title or photo
    where there was none — so the per-request call from ``/api/me`` is a dict
    lookup and no lock on every request after the first.

    Returns the record now on file (unchanged or new), None for an empty address."""
    key = _key(email)
    if not key:
        return None
    name = str(name or "").strip()
    job_title = str(job_title or "").strip()
    current = lookup(key, log_path) or {}
    cur_rank = _SOURCE_RANK.get(str(current.get("source") or ""), -1)
    new_rank = _SOURCE_RANK.get(source, 0)

    if current.get("name") and name and new_rank < cur_rank:
        merged_name, merged_source = current["name"], current["source"]
    elif name:
        merged_name, merged_source = name, source
    else:
        merged_name, merged_source = str(current.get("name") or ""), current.get("source") or source
    merged_title = job_title or str(current.get("job_title") or "")
    merged_photo = photo if photo is not None else current.get("photo")
    # The country the directory holds, used ONLY to open someone's region picker
    # somewhere sensible. Never served in public_row: a personal setting is not
    # the whole recruiting team's business.
    merged_country = (str(country or "").strip().upper()
                      or str(current.get("country") or ""))

    if current and (merged_name == current.get("name", "")
                    and merged_title == current.get("job_title", "")
                    and merged_photo == current.get("photo")
                    and merged_country == str(current.get("country") or "")):
        return current  # nothing new — no write
    if not current and not (merged_name or merged_title or merged_photo or merged_country):
        return None  # an address alone teaches nothing

    record = {"email": key, "name": merged_name, "job_title": merged_title,
              "photo": merged_photo, "country": merged_country,
              "source": merged_source, "ts": _now()}
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def public_row(rec: dict) -> dict:
    """The wire shape of one person — what the client needs to render a name with
    an avatar, and nothing else."""
    email = rec.get("email", "")
    return {
        "email": email,
        "name": rec.get("name", "") or "",
        "job_title": rec.get("job_title", "") or "",
        "photo": rec.get("photo"),
        "initials": initials(rec.get("name", ""), email),
        "hue": avatar_hue(email),
        "source": rec.get("source", ""),
    }


# --- search: local first, Graph when it is there -----------------------------------


SEARCH_MIN_CHARS = 2  # a one-letter query matches everyone: that is enumeration


def _local_matches(q: str, log_path: str | Path, limit: int) -> list[dict]:
    needle = q.strip().lower()
    if len(needle) < SEARCH_MIN_CHARS:
        return []
    tokens = [t for t in needle.replace("@", " ").replace(".", " ").split() if t]
    out: list[tuple[int, str, dict]] = []
    for rec in directory(log_path).values():
        name = str(rec.get("name") or "").lower()
        email = rec["email"]
        hay = f"{name} {email} {email.split('@', 1)[0].replace('.', ' ').replace('_', ' ')}"
        if all(t in hay for t in tokens):
            # names that START with the query rank first, then anything containing it
            rank = 0 if name.startswith(needle) or email.startswith(needle) else 1
            out.append((rank, name or email, rec))
    out.sort(key=lambda t: (t[0], t[1]))
    return [public_row(r) for _, _, r in out[:limit]]


class GraphUnavailable(Exception):
    """Graph could not be reached or refused us — the reason is for admins."""


class GraphDirectory:
    """A thin Microsoft Graph client for the two calls this app needs: search
    people and read one profile (name, job title, photo).

    ``token_provider`` returns a bearer token; the default asks
    DefaultAzureCredential for a Graph token — the managed identity hosted, ``az
    login`` locally — the same path the service keys take. ``fetch`` is the HTTP
    seam, injected by tests. Every failure raises GraphUnavailable with a plain
    reason; nothing here is ever fatal to the caller."""

    def __init__(self, token_provider: Callable[[], str] | None = None,
                 fetch: Callable[[str, dict], bytes] | None = None) -> None:
        self._token_provider = token_provider or _default_token
        self._fetch = fetch or _http_get

    def _get(self, url: str, accept: str = "application/json", **headers: str) -> bytes:
        try:
            token = self._token_provider()
        except Exception as exc:  # noqa: BLE001 — credential errors are a reason, not a crash
            raise GraphUnavailable(f"no Graph token: {type(exc).__name__}") from exc
        hdrs = {"Authorization": f"Bearer {token}", "Accept": accept, **headers}
        try:
            return self._fetch(url, hdrs)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise GraphUnavailable(
                    "Graph refused the request (the app's identity lacks User.Read.All "
                    "consent) — ask the Cloud Admin to grant it.") from exc
            if exc.code == 404:
                raise GraphUnavailable("not found") from exc
            raise GraphUnavailable(f"Graph returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # The exception text can name internal hosts and proxies; it goes to the
            # log, and the reason the client sees is fixed copy (2026-10-16).
            log.info("people: Graph unreachable — %s", exc)
            raise GraphUnavailable("The company directory couldn't be reached — type the "
                                   "address instead.") from exc

    @staticmethod
    def _row(u: dict) -> dict:
        email = _key(u.get("mail") or u.get("userPrincipalName"))
        # `usageLocation` is an ISO-3166 alpha-2 by definition; `country` is free
        # text, so it goes through the country table and yields a code or nothing.
        # Only the CODE is stored — a building name or a city is personal detail
        # this app has no use for.
        from resume_screener import countries

        found = (countries.lookup(u.get("usageLocation"))
                 or countries.lookup(u.get("country")))
        return {"email": email, "name": str(u.get("displayName") or "").strip(),
                "job_title": str(u.get("jobTitle") or "").strip(), "photo": None,
                "country": found.alpha2 if found else "",
                "source": "graph", "ts": _now()}

    def search(self, q: str, limit: int = 8) -> list[dict]:
        term = q.strip().replace('"', "")
        if len(term) < 2:
            return []
        quoted = urllib.parse.quote(f'"displayName:{term}" OR "mail:{term}" OR "userPrincipalName:{term}"')
        url = (f"{GRAPH_BASE}/users?$search={quoted}&$select=displayName,jobTitle,mail,"
               f"userPrincipalName,usageLocation,country&$top={int(limit)}")
        body = self._get(url, ConsistencyLevel="eventual")
        data = json.loads(body or b"{}")
        rows = [self._row(u) for u in data.get("value", []) if isinstance(u, dict)]
        return [r for r in rows if r["email"]]

    def profile(self, email: str) -> dict | None:
        key = _key(email)
        if not key:
            return None
        url = (f"{GRAPH_BASE}/users/{urllib.parse.quote(key)}"
               "?$select=displayName,jobTitle,mail,userPrincipalName,usageLocation,country")
        try:
            data = json.loads(self._get(url) or b"{}")
        except GraphUnavailable as exc:
            if str(exc) == "not found":
                return None
            raise
        return self._row(data) if isinstance(data, dict) and data else None

    def photo_data_uri(self, email: str, size: str = "48x48") -> str | None:
        key = _key(email)
        if not key:
            return None
        url = f"{GRAPH_BASE}/users/{urllib.parse.quote(key)}/photos/{size}/$value"
        try:
            raw = self._get(url, accept="image/jpeg")
        except GraphUnavailable as exc:
            if str(exc) == "not found":
                return None  # no photo set — the common case, not an error
            raise
        if not raw:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def _default_token() -> str:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token(_GRAPH_SCOPE).token


def _http_get(url: str, headers: dict) -> bytes:
    # Only ever the Graph API over https — enforced, not just assumed (bandit B310).
    if not url.startswith(GRAPH_BASE + "/"):
        raise urllib.error.URLError("directory lookups go to Microsoft Graph only")
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=6) as resp:  # nosec B310
        return resp.read()


def graph_enabled() -> bool:
    """``PEOPLE_DIRECTORY=graph`` opts the deployment in; the default is local."""
    return os.environ.get("PEOPLE_DIRECTORY", "local").strip().lower() == "graph"


# The last Graph outcome, so admins can see WHY the directory is local without
# reading logs. Reset by every successful call.
_GRAPH_STATE: dict[str, Any] = {"ok": None, "reason": ""}

_graph_singleton: GraphDirectory | None = None


def _graph() -> GraphDirectory:
    global _graph_singleton
    if _graph_singleton is None:
        _graph_singleton = GraphDirectory()
    return _graph_singleton


def _graph_call(fn: Callable[[GraphDirectory], Any]) -> Any:
    try:
        result = fn(_graph())
    except GraphUnavailable as exc:
        _GRAPH_STATE.update(ok=False, reason=str(exc))
        log.info("people: Graph unavailable — %s", exc)
        return None
    _GRAPH_STATE.update(ok=True, reason="")
    return result


def status() -> dict:
    """Which directory is serving, and if Graph was asked for but is not, why."""
    return {
        "source": "graph" if graph_enabled() and _GRAPH_STATE.get("ok") is not False else "local",
        "graph": {"enabled": graph_enabled(), "ok": _GRAPH_STATE.get("ok"),
                  "reason": _GRAPH_STATE.get("reason", "")},
    }


def search(q: str, *, limit: int = 8, log_path: str | Path = PEOPLE_LOG) -> dict:
    """People matching ``q``: what the app already knows first, then Graph when
    it is enabled and answering. Graph hits are remembered, so a person picked
    once is known offline from then on."""
    local = _local_matches(q, log_path, limit)
    if not graph_enabled():
        return {"people": local, "source": "local", "note": None}
    remote = _graph_call(lambda g: g.search(q, limit=limit))
    if remote is None:
        return {"people": local, "source": "local",
                "note": _GRAPH_STATE.get("reason") or "Directory lookup is unavailable."}
    seen = {p["email"] for p in local}
    merged = list(local)
    for rec in remote:
        if rec["email"] in seen:
            continue
        stored = remember(rec["email"], name=rec["name"], job_title=rec["job_title"],
                          source="graph", log_path=log_path) or rec
        merged.append(public_row(stored))
        seen.add(rec["email"])
    return {"people": merged[:limit], "source": "graph", "note": None}


def enrich(email: str, *, log_path: str | Path = PEOPLE_LOG) -> dict | None:
    """Fill a person's title (and photo) from Graph if enabled — used for the
    signed-in user's own identity block. Cheap when nothing is enabled."""
    key = _key(email)
    if not key or not graph_enabled():
        return lookup(key, log_path)
    current = lookup(key, log_path) or {}
    if current.get("source") == "graph" and current.get("photo") is not None:
        return current  # already the directory of record's view
    prof = _graph_call(lambda g: g.profile(key))
    if not prof:
        return current or None
    photo = _graph_call(lambda g: g.photo_data_uri(key))
    return remember(key, name=prof["name"], job_title=prof["job_title"], photo=photo,
                    source="graph", log_path=log_path)
