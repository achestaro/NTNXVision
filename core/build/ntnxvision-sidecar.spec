# PyInstaller spec — freezes the Python core into a sidecar binary for packaging.
#
#   cd core && pyinstaller --noconfirm build/ntnxvision-sidecar.spec
#
# Produces a ONE-FOLDER build at dist/ntnxvision-sidecar/ (ntnxvision-sidecar on macOS,
# ntnxvision-sidecar.exe on Windows) which electron-builder bundles as resources/sidecar.
# One-folder rather than one-file: no per-launch unpack to a temp dir (faster start, no
# AV false positives on Windows, and every Mach-O can be signed in place for notarization).
#
# Builds for the architecture of the Python interpreter running PyInstaller — build on an
# arm64 Mac for arm64, on an x64 Mac (or Rosetta Python) for x64.

# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

# Anchor to the spec file so the build works from any cwd. The package is installed
# editable (PEP 660 finder) which PyInstaller can't trace — putting the real source
# tree on pathex lets Analysis resolve ntnxvision_core directly.
CORE_DIR = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821
REPO_DIR = os.path.dirname(CORE_DIR)

hiddenimports = (
    collect_submodules("ntnxvision_core")
    + collect_submodules("openpyxl")
    + collect_submodules("docx")
    + collect_submodules("fpdf")
    + collect_submodules("pydantic")
    + ["pydantic_core", "pydantic_core._pydantic_core", "annotated_types", "lxml.etree"]
)

datas = (
    # python-docx: default.docx + header/footer/styles XML templates used by Document().
    collect_data_files("docx")
    # fpdf2: bundled core-font metrics and data tables.
    + collect_data_files("fpdf")
    # openpyxl ships a few non-.py resources (e.g. VBA/theme XML).
    + collect_data_files("openpyxl")
    # Licensing: every runtime dependency's *.dist-info (METADATA + its LICENSE/NOTICE
    # files), plus the project's LICENSE / NOTICE / THIRD_PARTY_NOTICES.md under legal/.
    + copy_metadata("ntnxvision-core", recursive=True)
    + [
        (os.path.join(REPO_DIR, name), "legal")
        for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")
    ]
)

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],  # noqa: F821
    pathex=[CORE_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "mypy", "ruff", "IPython"],
    noarchive=False,
    # fpdf2 is LGPL-3.0: keep it as plain .py files in the bundle folder (not inside the
    # PYZ archive) so a user can swap in a modified fpdf2 without rebuilding the sidecar.
    # See docs/LICENSING.md.
    module_collection_mode={"fpdf": "py"},
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ntnxvision-sidecar",
    console=True,  # stdio JSON-RPC needs a console subsystem binary (Electron hides it)
    # Never UPX: compressed binaries break macOS code signing and trip Windows AV.
    upx=False,
    # Signing is done by electron-builder over the whole .app (identity from CSC_LINK).
    codesign_identity=None,
    entitlements_file=None,
    target_arch=None,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="ntnxvision-sidecar",
)
