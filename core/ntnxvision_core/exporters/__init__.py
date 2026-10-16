"""Exporters — render an Environment to Excel / PDF / Word.

`export_environment` is the single dispatch point used by the CLI and sidecar: given a
format name it picks the right exporter, derives the output filename, and returns the
written path. `ExportOptions` carries the per-export choices the UI offers: which
sections the PDF and Word documents include, the Word template (built-in or a customer
``.dotx``/``.docx``), and the "prepared for" / author fields for the cover and document
properties. Excel always contains every sheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

from ntnxvision_core.exporters.excel import ExcelExporter
from ntnxvision_core.numfmt import fmt_count

if TYPE_CHECKING:
    from ntnxvision_core.models import Environment

_EXTENSIONS = {"excel": "xlsx", "xlsx": "xlsx", "pdf": "pdf", "word": "docx", "docx": "docx"}

#: The section ids the export dialog offers, in document order.
SECTIONS = (
    "executive_summary", "clusters_hosts", "workloads_storage", "data_protection",
    "security_networking", "platform_services", "licensing_lcm", "collection_notes",
)


@dataclass(frozen=True)
class ExportOptions:
    sections: frozenset[str] | None = None  # None = every section
    template: str = "standard"
    prepared_for: str = ""
    author: str = ""

    def includes(self, section: str) -> bool:
        return self.sections is None or section in self.sections

    @classmethod
    def coerce(cls, value: ExportOptions | dict[str, Any] | None) -> ExportOptions:
        if isinstance(value, ExportOptions):
            return value
        value = value or {}
        sections = value.get("sections")
        return cls(
            sections=frozenset(str(s) for s in sections) if isinstance(sections, list)
            else None,
            template=str(value.get("template") or "standard"),
            prepared_for=str(value.get("prepared_for") or ""),
            author=str(value.get("author") or ""),
        )


@overload
def export_environment(env: Environment, fmt: str, directory: str | Path, name: str,
                       options: ExportOptions | dict[str, Any] | None = ...,
                       with_detail: Literal[False] = ...) -> Path: ...


@overload
def export_environment(env: Environment, fmt: str, directory: str | Path, name: str,
                       options: ExportOptions | dict[str, Any] | None = ...,
                       *, with_detail: Literal[True]) -> tuple[Path, str | None]: ...


def export_environment(
    env: Environment, fmt: str, directory: str | Path, name: str,
    options: ExportOptions | dict[str, Any] | None = None, with_detail: bool = False,
) -> Path | tuple[Path, str | None]:
    """Export `env` in the given format into `directory`, returning the output path
    (and, with `with_detail`, a short description such as "34 sheets")."""
    fmt = fmt.lower()
    ext = _EXTENSIONS.get(fmt)
    if ext is None:
        raise ValueError(f"Unknown export format: {fmt!r}")
    out = Path(directory) / f"{name}.{ext}"
    opts = ExportOptions.coerce(options)

    detail: str | None
    if fmt in ("excel", "xlsx"):
        excel = ExcelExporter()
        path = excel.export(env, out)
        detail = f"{fmt_count(excel.sheet_count)} sheets"
    elif fmt == "pdf":
        from ntnxvision_core.exporters.pdf import PdfExporter

        pdf = PdfExporter()
        path = pdf.export(env, out, opts)
        detail = f"{fmt_count(pdf.page_count)} pages" if pdf.page_count else None
    elif fmt in ("word", "docx"):
        from ntnxvision_core.exporters.word import WordExporter

        word = WordExporter()
        path = word.export(env, out, opts)
        detail = (f"{fmt_count(word.section_count)} sections · "
                  f"{fmt_count(word.table_count)} tables")
    else:  # pragma: no cover
        raise ValueError(f"Unhandled format: {fmt!r}")
    return (path, detail) if with_detail else path


__all__ = ["SECTIONS", "ExcelExporter", "ExportOptions", "export_environment"]
