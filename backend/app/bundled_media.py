"""Validate the pinned, self-contained desktop media resource directory."""

import hashlib
import json
from pathlib import Path

from .core.config import Settings


def bundled_settings(work_dir: Path, resources: Path) -> Settings:
    media = resources / 'media'
    try:
        manifest = json.loads((media / 'manifest.json').read_text(encoding='utf-8'))
        for name in ('ffmpeg.exe', 'ffprobe.exe', 'LICENSE', 'README.txt'):
            expected = manifest['files'][name]
            with (media / name).open('rb') as source:
                actual = hashlib.file_digest(source, 'sha256').hexdigest()
            if actual != expected:
                raise ValueError(f'{name} SHA-256 不匹配')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f'随包媒体工具缺失或损坏，请重新安装：{media}（{exc}）') from exc
    return Settings(work_dir.absolute(), ffmpeg=str(media / 'ffmpeg.exe'),
                    ffprobe=str(media / 'ffprobe.exe'))
