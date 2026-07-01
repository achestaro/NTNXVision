"""Exporters — render an Environment to Excel / PDF / Word.

`export_environment` is the single dispatch point used by the CLI and sidecar: given a
format name it picks the right exporter, derives the output filename, and returns the
written path. PDF (weasyprint) and the Word Architecture Design Document (python-docx)
are added in Phase 3.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ntnxvision_core.exporters.excel import ExcelExporter

if TYPE_CHECKING:
    from ntnxvision_core.models import Environment

_EXTENSIONS = {"excel": "xlsx", "xlsx": "xlsx", "pdf": "pdf", "word": "docx", "docx": "docx"}


def export_environment(
    env: Environment, fmt: str, directory: str | Path, name: str
) -> Path:
    """Export `env` in the given format into `directory`, returning the output path."""
    fmt = fmt.lower()
    ext = _EXTENSIONS.get(fmt)
    if ext is None:
        raise ValueError(f"Unknown export format: {fmt!r}")
    out = Path(directory) / f"{name}.{ext}"

    if fmt in ("excel", "xlsx"):
        return ExcelExporter().export(env, out)
    if fmt == "pdf":
        from ntnxvision_core.exporters.pdf import PdfExporter

        return PdfExporter().export(env, out)
    if fmt in ("word", "docx"):
        from ntnxvision_core.exporters.word import WordExporter

        return WordExporter().export(env, out)
    raise ValueError(f"Unhandled format: {fmt!r}")  # pragma: no cover


__all__ = ["ExcelExporter", "export_environment"]
