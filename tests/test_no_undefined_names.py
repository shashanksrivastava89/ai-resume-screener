"""An undefined name must be a failing TEST, not a production incident.

WHY THIS FILE EXISTS. The non-scoring location lens shipped with its import inside a
DIFFERENT command's function, so the run worker referenced `location_context` with
nothing in scope. Every candidate died with NameError at the "location" stage — 235 of
235 on the pilot batch — which means screening was completely broken from the moment
the lens landed, and the recruiter only saw "This file couldn't be screened".

Nothing caught it. The run worker needs Azure OpenAI and Document Intelligence, so no
unit test executes that code path, and a NameError is invisible to the compiler: the
module imports fine, the function parses fine, and the name is only resolved when the
line actually runs — on a real candidate, in production.

So the guard cannot be another unit test of that path. It has to be static: resolve
every name a function reads against the scopes that can actually see it. That is what
pyflakes does, but it isn't a dependency here and adding one to the critical path the
night before user testing is its own risk, so this is a deliberately CONSERVATIVE
stdlib implementation — it reports only what it is sure about, and the first test
below proves it still catches the exact shape of the real bug.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "resume_screener"
_BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}


def _bound_by(node: ast.AST) -> set[str]:
    """Every name this statement/expression BINDS in the scope that contains it.

    Deliberately generous: over-reporting a binding can only cause a MISS, never a
    false alarm, and a checker that cries wolf gets deleted."""
    names: set[str] = set()

    def target(node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for element in node.elts:
                target(element)
        elif isinstance(node, ast.Starred):
            target(node.value)

    def children(current: ast.AST):
        """Statements belonging to THIS scope — nested scopes contribute only their
        own NAME, never their contents.

        This has to be shallow for the same reason the read-collector does, and for a
        sharper reason: a deep walk hoisted an import made inside one function into
        the module's scope, which is precisely the bug being guarded against, so the
        detector reported nothing at all."""
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(child.name)  # the def binds its name here; its body does not
                continue
            if isinstance(child, ast.Lambda):
                continue
            yield child
            yield from children(child)

    for child in children(node):
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            for alias in child.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(child, ast.Assign):
            for t in child.targets:
                target(t)
        elif isinstance(child, (ast.AnnAssign, ast.AugAssign)):
            target(child.target)
        elif isinstance(child, (ast.For, ast.AsyncFor)):
            target(child.target)
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            for item in child.items:
                if item.optional_vars is not None:
                    target(item.optional_vars)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            names.add(child.name)
        elif isinstance(child, ast.NamedExpr):  # walrus
            target(child.target)
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            names.update(child.names)
        elif isinstance(child, (ast.comprehension,)):
            target(child.target)
    return names


def _lambda_params(fn: ast.Lambda) -> set[str]:
    return _params(fn)


def _params(fn) -> set[str]:
    args = fn.args
    names = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def _loads_in_scope(node: ast.AST) -> list[tuple[int, str]]:
    """Name READS that belong to THIS scope, not descending into nested ones.

    The distinction matters and getting it wrong is what a first attempt got wrong:
    `ast.walk` walks straight into nested functions, so every inner parameter looked
    undefined in the outer scope and the checker reported 40 false alarms in code that
    was perfectly fine."""
    out: list[tuple[int, str]] = []

    def walk(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                continue  # its own scope; visited separately with this one enclosing it
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                out.append((child.lineno, child.id))
            walk(child)

    walk(node)
    return out


def _nested_scopes(node: ast.AST) -> list[ast.AST]:
    """Immediate child scopes (functions, lambdas, classes) at any statement depth."""
    found: list[ast.AST] = []

    def walk(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                found.append(child)
            else:
                walk(child)

    walk(node)
    return found


def undefined_names(source: str, filename: str = "<src>") -> list[tuple[int, str]]:
    """[(line, name)] for names a scope READS that no visible scope binds.

    Conservative by construction: a module using `from x import *` is skipped
    entirely, and a name bound anywhere in a scope counts as bound everywhere in it
    (so use-before-assignment is not reported — that is not what this guards)."""
    tree = ast.parse(source, filename=filename)
    if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
           for n in ast.walk(tree)):
        return []

    found: list[tuple[int, str]] = []

    def enter(node: ast.AST, enclosing: set[str]) -> None:
        if isinstance(node, ast.Lambda):
            scope = enclosing | _lambda_params(node) | _bound_by(node)
        elif isinstance(node, ast.ClassDef):
            # A class body cannot be seen by its methods, so methods get `enclosing`.
            scope = enclosing | _bound_by(node)
        else:
            scope = enclosing | _params(node) | _bound_by(node)

        for line, name in _loads_in_scope(node):
            if name not in scope:
                found.append((line, name))

        inherited = enclosing if isinstance(node, ast.ClassDef) else scope
        for child in _nested_scopes(node):
            enter(child, inherited)

    module_scope = _bound_by(tree) | _BUILTINS
    for line, name in _loads_in_scope(tree):
        if name not in module_scope:
            found.append((line, name))
    for child in _nested_scopes(tree):
        enter(child, module_scope)
    return sorted(set(found))


# --- the detector must catch the real bug ---------------------------------------------


REAL_BUG_SHAPE = '''
def other_command():
    from resume_screener import location_context      # the import lives HERE
    return location_context.read_context("", "")


def run_command(text, place):
    stage = "location"
    return location_context.read_context(text, place)  # ...but it is used HERE
'''


def test_the_detector_catches_the_shape_of_the_real_bug():
    """A module imported in one function and used in another — exactly how screening
    broke. If this ever stops being reported, the guard below is worthless."""
    hits = undefined_names(REAL_BUG_SHAPE)
    assert any(name == "location_context" for _line, name in hits), hits
    # ...and it does NOT flag the function that legitimately imported it.
    lines = [line for line, name in hits if name == "location_context"]
    assert lines == [9], hits  # only the use in run_command


def test_the_detector_does_not_cry_wolf_on_ordinary_code():
    """Closures, comprehensions, walrus, except-as, class bodies, lambdas and
    late-bound module globals are all normal and must never be reported."""
    ok = '''
import os
from pathlib import Path

CONST = 1


def outer(items, *rest, flag=False, **kw):
    total = CONST + len(rest) + len(kw)
    squares = [i * i for i in items if i]
    pairs = {k: v for k, v in kw.items()}

    def inner():
        return total + len(squares) + len(pairs) + int(flag)

    if (n := len(items)) > 0:
        total += n
    try:
        Path(os.getcwd())
    except OSError as exc:
        return str(exc)
    with open("x") as fh:
        fh.read()
    for idx, value in enumerate(items):
        total += idx + int(bool(value))
    return inner() + helper()


def helper():
    return CONST


class Thing:
    LIMIT = 5

    def method(self):
        return self.LIMIT + CONST + sorted([1], key=lambda x: -x)[0]
'''
    assert undefined_names(ok) == []


# --- the codebase itself ---------------------------------------------------------------


def test_no_module_reads_a_name_nothing_defines():
    """The guard. Every .py under src/resume_screener is scanned."""
    problems: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for line, name in undefined_names(source, str(path)):
            problems.append(f"{path.relative_to(SRC.parent.parent)}:{line} — {name}")
    assert not problems, (
        "undefined name(s) — this is how screening broke silently:\n  "
        + "\n  ".join(problems))
