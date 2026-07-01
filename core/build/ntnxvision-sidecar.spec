# PyInstaller spec — freezes the Python core into a sidecar binary for packaging.
#
#   cd core && pyinstaller build/ntnxvision-sidecar.spec
#
# Produces dist/ntnxvision-sidecar/ which electron-builder bundles as resources/sidecar.

# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_submodules

# Anchor to the spec file so the build works from any cwd. The package is installed
# editable (PEP 660 finder) which PyInstaller can't trace — putting the real source
# tree on pathex lets Analysis resolve ntnxvision_core directly.
CORE_DIR = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821

hiddenimports = (
    collect_submodules("ntnxvision_core")
    + collect_submodules("openpyxl")
    + ["pydantic", "pydantic_core"]
)

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],  # noqa: F821
    pathex=[CORE_DIR],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ntnxvision-sidecar",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="ntnxvision-sidecar",
)
