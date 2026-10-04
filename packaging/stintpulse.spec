# Build on the target OS. onedir keeps startup fast and data outside binaries.
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / 'backend'))
from ac_agent import APP_NAME, __version__  # noqa: E402

numbers = tuple(int(n) for n in __version__.split('.')[:3]) + (0,)
version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
    kids=[
        StringFileInfo([StringTable('040704B0', [
            StringStruct('CompanyName', APP_NAME),
            StringStruct('FileDescription', f'{APP_NAME} - Telemetrie und Onboard-Video für Assetto Corsa'),
            StringStruct('FileVersion', __version__),
            StringStruct('InternalName', APP_NAME),
            StringStruct('OriginalFilename', f'{APP_NAME}.exe'),
            StringStruct('ProductName', APP_NAME),
            StringStruct('ProductVersion', __version__),
            StringStruct('LegalCopyright', 'MIT License, StintPulse contributors'),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0407, 1200])]),
    ],
)

a = Analysis([str(root / 'packaging' / 'entry.py')], pathex=[str(root / 'backend')],
    binaries=[], datas=[(str(root / 'frontend' / 'dist'), 'web'),
        (str(root / 'docs' / 'licenses'), 'licenses'), (str(root / 'LICENSE'), '.'),
        (str(root / 'THIRD_PARTY_NOTICES.md'), '.')],
    hiddenimports=collect_submodules('uvicorn') + ['websockets.legacy.server', 'ac_agent.windows'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['tkinter', 'pandas', 'matplotlib', 'scipy'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME,
    icon=str(root / 'packaging' / 'stintpulse.ico'), version=version,
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)
