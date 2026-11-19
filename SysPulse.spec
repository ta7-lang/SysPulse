# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller --noconfirm --clean SysPulse.spec  →  dist/SysPulse.exe

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("assets/syspulse.ico", "assets")],
    hiddenimports=["pynvml"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SysPulse",
    console=False,
    icon="assets/syspulse.ico",
    upx=False,
    version="version_info.txt",
)
