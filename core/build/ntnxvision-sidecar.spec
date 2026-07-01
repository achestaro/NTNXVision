# PyInstaller spec — freezes the Python core into a sidecar binary for packaging.
#
#   cd core && pyinstaller build/ntnxvision-sidecar.spec
#
# Produces dist/ntnxvision-sidecar/ which electron-builder bundles as resources/sidecar.

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("ntnxvision_core")
    + collect_submodules("openpyxl")
    + ["pydantic", "pydantic_core"]
)

a = Analysis(
    ["entry.py"],
    pathex=[".."],
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
