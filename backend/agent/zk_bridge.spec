# PyInstaller spec for zk_bridge.py -> a single-file Windows executable.
# Build (on Windows, from backend/agent/):
#   pip install -r requirements-agent.txt
#   pyinstaller --clean zk_bridge.spec
# Output: dist/TaxFlowZkBridge.exe
#
# See README.md in this directory for the unsigned-binary / SmartScreen
# caveat and the --install-startup flag this exe still supports.

import pathlib

block_cipher = None
_script = str(pathlib.Path(__file__).parent.parent / "zk_bridge.py")

a = Analysis(
    [_script],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["zk", "zk.const"],
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
    name="TaxFlowZkBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep a visible window — this is a long-running poller,
                    # hiding it would make "is it working?" unanswerable
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,  # unsigned — see README.md
    entitlements_file=None,
)
