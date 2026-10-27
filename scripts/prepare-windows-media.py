"""Stage a specific pre-acquired FFmpeg distribution, never select from PATH."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path,
                        help='Extracted ffmpeg-8.1-full_build directory containing bin, LICENSE and README.txt')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'packaging/windows/media-manifest.json').read_text(encoding='utf-8'))
    source = args.source.resolve()
    paths = {name: source / ('bin' if name.endswith('.exe') else '') / name for name in manifest['files']}
    for name, path in paths.items():
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != manifest['files'][name]:
            raise RuntimeError(f'Pinned media hash mismatch: {path}')
    encoders = subprocess.run([str(paths['ffmpeg.exe']), '-hide_banner', '-encoders'],
                              capture_output=True, text=True, check=True, timeout=30).stdout
    available = {parts[1] for line in encoders.splitlines() if len(parts := line.split()) > 1}
    if missing := set(manifest['required_encoders']) - available:
        raise RuntimeError(f'Missing media encoders: {sorted(missing)}')
    target = root / '.runtime/windows-packaging/media'
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='media-stage-', dir=target.parent) as temporary:
        stage = Path(temporary)
        for name, path in paths.items():
            shutil.copy2(path, stage / name)
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(stage, target)
    print(json.dumps({'media': str(target), 'version': manifest['version'],
                      'encoders': manifest['required_encoders']}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Media staging failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
