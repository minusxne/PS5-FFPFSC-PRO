# PyInstaller spec — build with:  pyinstaller ps5ffpfsc.spec
# Produces a single windowed executable. The same binary also runs the job
# worker (``--worker``) and mkpfs's multiprocessing children, so no separate
# Python installation is needed.
import sys

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("mkpfs")
    + collect_submodules("ps5ffpfsc")
    + collect_submodules("py7zr")
    + ["rarfile", "zlib_ng", "zlib_ng.zlib_ng"]
)

a = Analysis(
    ["run.py"],
    pathex=["backend"],
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PS5-FFPFSC-PRO",
    console=False,
    upx=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

if sys.platform == "darwin":
    app = BUNDLE(exe, name="PS5-FFPFSC-PRO.app", bundle_identifier="io.github.ps5ffpfscpro")
