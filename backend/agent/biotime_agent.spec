# PyInstaller spec for biotime_agent.py -> a single-file Windows executable.
# Build (on Windows, from backend/agent/):
#   pip install -r requirements-agent.txt
#   pyinstaller --clean biotime_agent.spec
# Output: dist/TaxFlowBioTimeAgent.exe
#
# See README.md in this directory for the unsigned-binary / SmartScreen
# caveat and the --install-startup flag this exe still supports.

import pathlib

block_cipher = None
_script = str(pathlib.Path(__file__).parent.parent / "biotime_agent.py")

a = Analysis(
    [_script],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TaxFlowBioTimeAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,  # unsigned — see README.md
    entitlements_file=None,
)
