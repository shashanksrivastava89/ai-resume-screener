"""Active content out of the PDFs we store and serve (2026-10-16, pen-test RS-04).

A PDF can carry code: JavaScript run when the file opens (/OpenAction), when a page
is shown or a field is touched (/AA), from a document-level name tree, from a link,
a bookmark or a form field (/A); a /Launch that starts a program; a /SubmitForm
that posts what is typed to somewhere else; embedded files. The pen test uploaded a
résumé whose JavaScript ran in the reviewer's browser viewer. A résumé needs none of
it, so it is removed — at intake, before the content hash is taken (so the candidate
id is the clean file's), and again when a file is served, for anything stored before
this existed or placed in a batch by hand.

What is removed: /OpenAction and every /Next link in an action chain when it is one
of DANGEROUS (or carries /JS); /AA wherever it appears (catalog, pages, fields,
annotations — every /AA value is an action and none belongs on a résumé); the
document JavaScript and embedded-file name trees; the XFA form; file-attachment,
rich-media, 3D, sound, movie and screen annotations. A plain /URI link or a "go to
page" destination is kept: a résumé's links are part of it.

Rules that matter more than they look:
- A clean file comes back as THE SAME bytes object, never a rewrite: rewriting
  changes the candidate id, and decisions, comments and shares are keyed by it.
- The rewrite is deterministic (pypdf mints no /ID and stamps no time), so the same
  upload twice still de-duplicates.
- strip() never raises. A file pypdf cannot parse passes through unchanged; the
  serving side then refuses to render it inline if its raw bytes carry a JavaScript
  or Launch marker (`unreadable_but_marked` — a weak backstop: object streams hide
  markers). An encrypted file that opens with an empty password is cleaned and comes
  out unencrypted; one that needs a password passes through.
"""

from __future__ import annotations

import io
import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DANGEROUS = frozenset({
    "/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR", "/GoToE",
    "/Rendition", "/RichMediaExecute",
})
_DROP_ANNOTS = frozenset({"/FileAttachment", "/RichMedia", "/3D", "/Sound", "/Movie",
                          "/Screen"})
_MARKERS = (b"/JavaScript", b"/JS", b"/Launch")
MAX_SERVE_BYTES = 25 * 1024 * 1024  # above this a file is never rendered inline


def _resolved(value):
    return value.get_object() if hasattr(value, "get_object") else value


def _get(d, key: str):
    from pypdf.generic import DictionaryObject

    if not isinstance(d, DictionaryObject) or key not in d:
        return None
    return _resolved(d[key])


def _identity(obj) -> object:
    ref = getattr(obj, "indirect_reference", None)
    return ("ref", ref.idnum, ref.generation) if ref is not None else ("id", id(obj))


def _dangerous(action) -> bool:
    from pypdf.generic import DictionaryObject

    action = _resolved(action)
    if not isinstance(action, DictionaryObject):
        return False
    return str(_get(action, "/S") or "") in DANGEROUS or "/JS" in action


class _Walk:
    def __init__(self) -> None:
        self.removed: list[str] = []
        self.seen: set[object] = set()

    def note(self, label: str) -> None:
        if label not in self.removed:
            self.removed.append(label)

    def action(self, owner, key: str, label: str) -> None:
        """owner[key] is an action: drop it when it is dangerous, else cut any
        dangerous link further down its /Next chain."""
        from pypdf.generic import DictionaryObject

        act = _get(owner, key)
        if not isinstance(act, DictionaryObject):
            return
        if _dangerous(act):
            del owner[key]
            self.note(label)
            return
        self._prune_next(act, label, set())

    def _prune_next(self, act, label: str, seen: set[object]) -> None:
        from pypdf.generic import ArrayObject

        if _identity(act) in seen:
            return
        seen.add(_identity(act))
        nxt = _get(act, "/Next")
        if nxt is None:
            return
        items = list(nxt) if isinstance(nxt, ArrayObject) else [nxt]
        if any(_dangerous(x) for x in items):
            del act["/Next"]
            self.note(label)
            return
        for x in items:
            self._prune_next(_resolved(x), label, seen)

    def drop_aa(self, owner, label: str) -> None:
        if _get(owner, "/AA") is not None:
            del owner["/AA"]
            self.note(label)

    def field(self, field) -> None:
        from pypdf.generic import DictionaryObject

        field = _resolved(field)
        if not isinstance(field, DictionaryObject) or _identity(field) in self.seen:
            return
        self.seen.add(_identity(field))
        self.drop_aa(field, "form field action")
        self.action(field, "/A", "form field action")
        for kid in _get(field, "/Kids") or []:
            self.field(kid)


def _clean(writer) -> list[str]:
    from pypdf.generic import ArrayObject, NameObject

    walk = _Walk()
    root = writer._root_object
    open_action = _get(root, "/OpenAction")
    if open_action is not None and not isinstance(open_action, ArrayObject):
        walk.action(root, "/OpenAction", "open action")
    walk.drop_aa(root, "document action")

    names = _get(root, "/Names")
    if names is not None:
        for key, label in (("/JavaScript", "document JavaScript"),
                           ("/EmbeddedFiles", "embedded file")):
            if key in names:
                del names[key]
                walk.note(label)
        if not names:
            del root["/Names"]

    form = _get(root, "/AcroForm")
    if form is not None:
        if "/XFA" in form:
            del form["/XFA"]
            walk.note("XFA form")
        for fld in _get(form, "/Fields") or []:
            walk.field(fld)

    outlines = _get(root, "/Outlines")
    stack = [_get(outlines, "/First")] if outlines is not None else []
    visited: set[object] = set()
    while stack:
        item = stack.pop()
        if item is None or _identity(item) in visited:
            continue
        visited.add(_identity(item))
        walk.action(item, "/A", "bookmark action")
        stack += [_get(item, "/First"), _get(item, "/Next")]

    for page in writer.pages:
        walk.drop_aa(page, "page action")
        annots = _get(page, "/Annots")
        if annots is None:
            continue
        kept = ArrayObject()
        for ref in annots:
            annot = _resolved(ref)
            if str(_get(annot, "/Subtype") or "") in _DROP_ANNOTS:
                walk.note("attachment or media annotation")
                continue
            walk.drop_aa(annot, "annotation action")
            walk.action(annot, "/A", "link action")
            kept.append(ref)
        if len(kept) != len(annots):
            page[NameObject("/Annots")] = kept
    return walk.removed


def _open(data: bytes):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        result = reader.decrypt("")
        if not result or getattr(result, "name", "") == "NOT_DECRYPTED":
            return None
    return reader


def _strip(data: bytes) -> tuple[bytes, list[str], bool]:
    """(bytes, what was removed, could it be read)."""
    if not data.startswith(b"%PDF") and b"%PDF" not in data[:1024]:
        return data, [], False
    try:
        from pypdf import PdfWriter

        reader = _open(data)
        if reader is None:
            return data, [], False
        writer = PdfWriter(clone_from=reader)
        removed = _clean(writer)
        if not removed:
            return data, [], True
        # Orphans: what the removed actions pointed to (a JavaScript stream, the next
        # action in a chain) is still in the object table until it is swept, and one
        # sweep only drops what NOTHING references — a chain needs a pass per link.
        count = -1
        for _ in range(6):
            writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
            now = sum(1 for o in writer._objects if o is not None)
            if now == count:
                break
            count = now
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue(), removed, True
    except Exception as exc:  # noqa: BLE001 — a malformed file must never sink intake
        log.info("pdfsafe: could not clean a PDF (%s)", type(exc).__name__)
        return data, [], False


def strip(data: bytes) -> tuple[bytes, list[str]]:
    """The PDF with its active content removed, and what was removed. Clean, not a
    PDF, or unreadable -> the SAME bytes object and []. Never raises."""
    out, removed, _readable = _strip(data)
    return out, removed


def inspect(data: bytes) -> list[str]:
    """What strip() would remove ([] when clean or unreadable)."""
    return _strip(data)[1]


def unreadable_but_marked(data: bytes) -> bool:
    """pypdf could not read it, yet its raw bytes name JavaScript or Launch. (Not
    /OpenAction: ordinary LaTeX output carries one.)"""
    _out, _removed, readable = _strip(data)
    return not readable and any(m in data for m in _MARKERS)


@dataclass(frozen=True)
class FileCheck:
    sanitized: bytes | None = None    # set when the file carried active content
    removed: tuple[str, ...] = ()
    attachment_only: bool = False     # couldn't be checked: never render it inline


_FILE_CHECKS: "OrderedDict[str, tuple[int, int, FileCheck]]" = OrderedDict()
_FILE_CHECKS_MAX = 256


def checked_file(path: str | Path) -> FileCheck:
    """The serving side's check for a stored PDF, memoised on the file's state."""
    path = Path(path)
    key = str(path.resolve())
    st = path.stat()
    hit = _FILE_CHECKS.get(key)
    if hit is not None and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        _FILE_CHECKS.move_to_end(key)
        return hit[2]
    if st.st_size > MAX_SERVE_BYTES:
        result = FileCheck(attachment_only=True)
    else:
        data = path.read_bytes()
        out, removed, readable = _strip(data)
        if removed:
            result = FileCheck(sanitized=out, removed=tuple(removed))
        else:
            result = FileCheck(attachment_only=not readable and any(m in data for m in _MARKERS))
    _FILE_CHECKS[key] = (st.st_size, st.st_mtime_ns, result)
    while len(_FILE_CHECKS) > _FILE_CHECKS_MAX:
        _FILE_CHECKS.popitem(last=False)
    return result
