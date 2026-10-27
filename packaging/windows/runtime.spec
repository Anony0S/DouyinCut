# PyInstaller onedir runtime. Only application/package resources are collected;
# the work directory, environment files and development environment are excluded.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPEC).resolve().parents[2]
frontend = root / 'desktop' / 'dist'
if not (frontend / 'index.html').is_file():
    raise SystemExit('Build the production frontend before freezing the runtime.')
sys.path.insert(0, str(root / 'backend'))
from app.bundled_media import bundled_settings
media = root / '.runtime' / 'windows-packaging' / 'media'
bundled_settings(root, media.parent)

a = Analysis(
    [str(root / 'backend' / 'desktop_launcher.py')],
    pathex=[str(root / 'backend')],
    binaries=[],
    datas=[(str(frontend), 'frontend'), (str(media), 'media')]
        + collect_data_files('app') + collect_data_files('streamget'),
    hiddenimports=collect_submodules('app') + collect_submodules('streamget') + [
        'uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.lifespan.on', 'keyring.backends.Windows',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='DouyinCutRuntime',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DouyinCutRuntime')
