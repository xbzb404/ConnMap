# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['E:/AI工程/connmap/app.py'],
    pathex=[],
    binaries=[],
    datas=[('E:/AI工程/connmap/icon.ico', '.'), ('E:/AI工程/connmap/icon.png', '.'), ('C:/Program Files/WindowsApps/PythonSoftwareFoundation.Python.3.12_3.12.2800.0_x64__qbz5n2kfra8p0/tcl', 'tcl')],
    hiddenimports=['tkinter', 'tkinter.font', 'tkinter.ttk', '_tkinter', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageTk'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'scipy', 'pandas', 'matplotlib', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx', 'IPython', 'jupyter', 'notebook', 'pytest', 'setuptools', 'pip', 'sqlite3', 'unittest', 'pydoc', 'doctest', 'test', 'distutils'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ConnMap',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='E:/AI工程/connmap/version_info.txt',
    icon=['E:/AI工程/connmap/icon.ico'],
)
