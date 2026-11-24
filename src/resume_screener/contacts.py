"""Local, offline contact extraction for candidate de-duplication (2026-07-23).

A candidate's IDENTITY for de-dup is their EMAIL / PHONE — not their name. Two
DIFFERENT people can share a name and both be valid on one requisition, so name
must never de-dup them. Conversely one person can submit two slightly different
résumés (different file bytes, same person) — those SHOULD de-dup, which a
content-hash comparison alone misses.

We read the email/phone from the résumé bytes locally (pypdf for PDF,
python-docx for DOCX — no cloud call, Document Intelligence stays reserved for
scoring-time extraction) and compare NORMALIZED tokens. Extraction is
best-effort: a scan or an unreadable file yields no tokens, and the caller then
falls back to exact content-hash de-dup only (better to keep two than wrongly
drop one). This only ever reads contact fields for the de-dup decision; the raw
résumé text never leaves this module.
"""

from __future__ import annotations

import io
import re

# Same email shape the MCP contact tool uses; kept here so de-dup has no import
# dependency on the API layer.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# A loose phone candidate: a run of digits with common separators. Validated by
# digit count after stripping, so incidental short numbers (years, counts) drop.
PHONE_RE = re.compile(r"[+(]?\d[\d\s().\-]{7,18}\d")


def _text_from_bytes(data: bytes, suffix: str) -> str:
    """Best-effort plain text from résumé bytes; '' on any failure (never raises)."""
    suffix = suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        if suffix == ".docx":
            from resume_screener.ingest import _docx_fallback_text

            return _docx_fallback_text(data)
    except Exception:  # noqa: BLE001 — extraction is advisory; a bad file just yields no tokens
        return ""
    return ""


def _phone_keys(raw: str) -> list[str]:
    """The comparison keys for one phone match — normally two, sometimes three.

    The same person writes their number several ways, and de-dup only works if
    every form collapses to a shared key. The original rule kept the last TEN
    digits, which is exactly right for India's uniform 10-digit mobile plan and
    wrong nearly everywhere else (2026-09-12): a Hungarian mobile written
    "+36 30 123 4567" and "06 30 123 4567" agree on their last nine digits and not
    their last ten, and a Spanish or Czech national mobile is nine digits long, so
    it yielded no key at all and that candidate de-duped on email alone.

    So: the full digit string, the last ten when there are ten (the original key,
    so an index written by an older version still intersects), and the last NINE —
    which is what makes the two Hungarian spellings meet.

    Known limit, stated rather than hidden: a national number with fewer than nine
    significant digits still yields nothing, and two different numbers sharing
    their last nine digits would collide. Both are rarer than the miss they
    replace, and a wrong merge still needs the same person's name on the page."""
    digits = re.sub(r"\D", "", raw)
    if not (9 <= len(digits) <= 15):
        return []
    keys = [digits]
    if len(digits) >= 10:
        keys.append(digits[-10:])
    keys.append(digits[-9:])
    return keys


def tokens_from_text(text: str) -> set[str]:
    """Normalized identity tokens (emails + phones) from already-extracted TEXT.

    Split out from contact_tokens so a caller that ALREADY holds the text does not
    pay to parse a PDF again. That is not hypothetical: the Bulk Print splitter had
    the parent document's per-page text in hand and then re-parsed every one of the
    ~230 slices it had just written, which was half the cost of a split (26s of a
    54s packet) and a large part of a 504 on upload."""
    if not text:
        return set()
    tokens: set[str] = set()
    for email in EMAIL_RE.findall(text):
        tokens.add("email:" + email.strip().lower())
    for raw in PHONE_RE.findall(text):
        for key in _phone_keys(raw):
            tokens.add("phone:" + key)
    return tokens


def contact_tokens(data: bytes, suffix: str) -> set[str]:
    """Normalized identity tokens (emails + phones) from one résumé's bytes.

    Returns a set like {"email:jane@x.com", "phone:9876543210"}. Empty when
    nothing readable — the caller then de-dups by content hash only. Two résumés
    are the SAME candidate when their token sets intersect."""
    return tokens_from_text(_text_from_bytes(data, suffix))


# A single résumé carries one or two addresses (plus the odd referee). Beyond this
# the file is a MULTI-candidate packet — e.g. an iCIMS Bulk Print, which holds 40+
# people. Matches ingest._MAX_DISTINCT_EMAILS, which is the threshold the scoring
# guard uses to refuse such a file as "one candidate".
MAX_SINGLE_RESUME_EMAILS = 6


def holds_many_candidates(tokens: set[str]) -> bool:
    """True when a token set clearly spans SEVERAL people, so it must not be used
    for candidate identity.

    Why this exists: contact de-dup asks "is this the same PERSON?", which is only
    meaningful for a one-candidate file. Applied to a Bulk Print the question is
    nonsense — the packet carries 40+ people's addresses, so a single candidate
    shared with an earlier packet would reject the WHOLE file (~40 résumés lost,
    and which files died depended on upload order). Multi-candidate files are
    de-duped by content hash only; their people are identified individually after
    the splitter turns them into one file per candidate.
    """
    return sum(1 for t in tokens if t.startswith("email:")) > MAX_SINGLE_RESUME_EMAILS


# --- per-file token cache -------------------------------------------------------------
#
# Extraction is expensive (a PDF text pass per file). Batch de-dup needs the tokens
# of every file ALREADY in the folder on every add, which for a 300-file batch meant
# re-extracting all 300 PDFs per upload — minutes of blocking work. Memoize on
# (path, size, mtime_ns) so a batch is read once and later adds are ~free; any edit
# to a file changes size/mtime and re-reads it.
_TOKEN_CACHE: dict[str, tuple[int, int, frozenset[str]]] = {}


def remember_tokens(path, tokens: set[str]) -> None:
    """Record tokens already extracted for a file just written, so the next batch
    scan doesn't re-extract what the caller has in hand. Without this, every upload
    paid to re-read the files the PREVIOUS upload added."""
    from pathlib import Path

    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return
    _TOKEN_CACHE[str(path.resolve())] = (stat.st_size, stat.st_mtime_ns, frozenset(tokens))


def preload_tokens(path, size: int, mtime_ns: int, tokens: set[str]) -> None:
    """Warm the cache from a PERSISTED index entry (batch_prep's contact index),
    so a fresh worker process doesn't re-extract text from files an earlier
    session already read. tokens_for_path still stat-validates (size, mtime_ns)
    on every lookup, so a stale entry costs one re-read, never a wrong identity."""
    from pathlib import Path

    _TOKEN_CACHE[str(Path(path).resolve())] = (size, mtime_ns, frozenset(tokens))


def tokens_for_path(path) -> set[str]:
    """Cached contact tokens for a file on disk; empty set when unreadable."""
    from pathlib import Path

    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return set()
    key = str(path.resolve())
    hit = _TOKEN_CACHE.get(key)
    if hit is not None and hit[0] == stat.st_size and hit[1] == stat.st_mtime_ns:
        return set(hit[2])
    try:
        tokens = contact_tokens(path.read_bytes(), path.suffix)
    except OSError:
        return set()
    _TOKEN_CACHE[key] = (stat.st_size, stat.st_mtime_ns, frozenset(tokens))
    return tokens
