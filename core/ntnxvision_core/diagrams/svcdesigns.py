"""Registry entries of the platform service designs: Kubernetes (NKP), Database
Service (NDB), Objects and Files (NUS). Kept in their own module so `diagrams.TYPES`
gains them with one line. Each applies only when the snapshot has that product's data."""

from __future__ import annotations

from . import DiagramType
from . import edit_services as _edit
from . import services as S
from .layout.services import layout as services_layout

TYPES: dict[str, DiagramType] = {
    "nkp": DiagramType(
        id="nkp", title="Kubernetes platform design", chapter="kubernetes",
        section="Kubernetes Platform Design", applies=S.nkp_applies,
        build_model=S.nkp_model, layout=services_layout, to_doc=S.to_doc,
        from_doc=S.from_doc, edit=_edit.NKP, slug="kubernetes-platform"),
    "ndb": DiagramType(
        id="ndb", title="Database service design", chapter="ndb",
        section="Database Service Design", applies=S.ndb_applies,
        build_model=S.ndb_model, layout=services_layout, to_doc=S.to_doc,
        from_doc=S.from_doc, edit=_edit.NDB, slug="database-service"),
    "objects": DiagramType(
        id="objects", title="Objects design", chapter="nus", section="Objects Design",
        applies=S.objects_applies, build_model=S.objects_model, layout=services_layout,
        to_doc=S.to_doc, from_doc=S.from_doc, edit=_edit.OBJECTS, slug="objects"),
    "files": DiagramType(
        id="files", title="Files design", chapter="nus", section="Files Design",
        applies=S.files_applies, build_model=S.files_model, layout=services_layout,
        to_doc=S.to_doc, from_doc=S.from_doc, edit=_edit.FILES, slug="files"),
}
