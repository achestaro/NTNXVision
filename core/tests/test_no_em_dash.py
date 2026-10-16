"""Guardrail: no em dash (U+2014) in user-visible Python strings.

Every string literal in ``ntnxvision_core`` can end up in the UI, a sidecar
error, or an exported report, so the whole package is scanned. Docstrings and
comments are never shown and are skipped (comments aren't in the AST at all), except
the docstrings of Typer commands, which ``--help`` prints.
"""

from __future__ import annotations

import ast
from pathlib import Path

import ntnxvision_core

EM_DASH = "\u2014"
PACKAGE_ROOT = Path(ntnxvision_core.__file__).resolve().parent

# (relative path, line) pairs that must keep an em dash, e.g. verbatim
# third-party license text. Keep this empty unless there is no alternative.
ALLOW_LIST: set[tuple[str, int]] = set()


def _is_cli_command(node: ast.AST) -> bool:
    """A Typer `@app.command(...)` / `@app.callback(...)`: its docstring is CLI help."""
    decorators = getattr(node, "decorator_list", [])
    return any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr in ("command", "callback")
        for d in decorators
    )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and not _is_cli_command(node):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def find_em_dashes(root: Path = PACKAGE_ROOT) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        skip = _docstring_nodes(tree)
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and EM_DASH in node.value
                and id(node) not in skip
                and (rel, node.lineno) not in ALLOW_LIST
            ):
                hits.append(f"{rel}:{node.lineno}: {node.value.strip()[:80]!r}")
    return hits


def test_no_em_dash_in_string_literals() -> None:
    hits = find_em_dashes()
    assert not hits, (
        "Em dashes found in user-visible strings; use a comma, colon, period, "
        "parentheses or the NA placeholder instead:\n" + "\n".join(hits)
    )
