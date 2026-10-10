# Build from this directory with the pinned Windows Python environment.
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['src/app.py'],
    pathex=['src'],
    binaries=[],
    datas=[('resources/default_devices.csv', 'resources')],
    hiddenimports=collect_submodules('winrt') + ['qa'],
    hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['TkAgg']}},
    excludes=['IPython', 'pytest', 'scipy', 'sklearn', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='WearableSweatSensor',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    version='version-info.txt',
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name='WearableSweatSensor-1.1.0')
