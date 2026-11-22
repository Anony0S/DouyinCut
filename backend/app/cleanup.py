"""Preview and remove expired application-generated files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path

from .core.logs import ACTIVE_JOB_STATES
from .core.storage import Store

DEFAULT_RETENTION_DAYS = 7
SECONDS_PER_DAY = 24 * 60 * 60
_JOB_ID = re.compile(r"job_[0-9a-f]{32}")
_COVER_TEMP = re.compile(r"^(job_[0-9a-f]{32})\.(?:source\.(?:png|jpg|webp)|tmp\.jpg)$")
_STAGING = re.compile(r"^\.(export_(job_[0-9a-f]{32}))-attempt_[0-9a-f]{32}\.partial$")


class CleanupPreviewChanged(Exception):
    """The eligible files changed after the user saw the preview."""


def cleanup_unused_files(store: Store, data_dir: Path, *, now: float | None = None,
                         retention_days: int = DEFAULT_RETENTION_DAYS,
                         include_referenced: bool = False, preview: bool = False,
                         preview_id: str | None = None) -> dict:
    cutoff = (time.time() if now is None else now) - retention_days * SECONDS_PER_DAY
    root = Path(data_dir).resolve()
    with store.connection() as conn:
        # Keep job state stable during deletion. A preview needs only a read snapshot.
        conn.execute("BEGIN" if preview else "BEGIN IMMEDIATE")
        entries = _cleanup_entries(conn, root, cutoff, include_referenced)
        files = {path: info for path, info in entries.items() if stat.S_ISREG(info.st_mode)}
        directories = [path for path, info in entries.items() if stat.S_ISDIR(info.st_mode)]
        if preview or preview_id is not None:
            digest = hashlib.sha256(json.dumps([retention_days, include_referenced]).encode())
            for path, info in sorted(entries.items()):
                identity = (str(path.relative_to(root)), *_identity(info))
                digest.update(json.dumps(identity).encode())
            current_id = digest.hexdigest()
            if preview:
                return {"preview_id": current_id, "file_count": len(files),
                        "directory_count": len(directories),
                        "estimated_bytes": sum(info.st_size for info in files.values())}
            if preview_id != current_id:
                raise CleanupPreviewChanged

        result = {"deleted_files": 0, "deleted_directories": 0, "retained_files": 0,
                  "bytes_reclaimed": 0, "cutoff_timestamp": cutoff}
        for path, info in files.items():
            try:
                if not _same_entry(path, info, root):
                    result["retained_files"] += 1
                    continue
                path.unlink()
            except OSError:
                result["retained_files"] += 1
            else:
                result["deleted_files"] += 1
                result["bytes_reclaimed"] += info.st_size
        for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            try:
                if _same_entry(path, entries[path], root, directory=True):
                    path.rmdir()
                    result["deleted_directories"] += 1
            except OSError:
                pass  # Busy directories and newly added files stay in place.
    return result


def _cleanup_entries(conn, root: Path, cutoff: float, include_referenced: bool) -> dict[Path, os.stat_result]:
    job_rows = conn.execute("SELECT id, state, payload, result FROM jobs").fetchall()
    active_jobs = {row["id"] for row in job_rows if row["state"] in ACTIVE_JOB_STATES}
    assets = conn.execute("SELECT id, body FROM objects WHERE kind='asset'").fetchall()
    asset_ids = {row["id"] for row in assets}
    protected = set()
    referenced = set()
    active_assets = set()
    for row in assets:
        protected.update(_record_paths(json.loads(row["body"]), ("path",)))
    if not include_referenced:
        for row in conn.execute("SELECT body FROM objects WHERE kind='export'"):
            referenced.update(_record_paths(json.loads(row[0]), ("path", "video_path")))
    for row in job_rows:
        if row["id"] in active_jobs:
            payload = json.loads(row["payload"])
            if isinstance(payload.get("asset_id"), str):
                active_assets.add(payload["asset_id"])
            protected.update(_record_paths(payload, ("path", "cover_path", "reference_path")))
        if row["result"] is not None:
            paths = _record_paths(json.loads(row["result"]), ("path", "draft_path", "video_path"))
            if row["id"] in active_jobs:
                protected.update(paths)
            elif not include_referenced:
                referenced.update(paths)

    entries = {}
    for name in ("analysis", "previews", "exports", "covers"):
        folder = root / name
        if not folder.is_dir() or _is_link(folder):
            continue
        for child in folder.iterdir():
            if _is_link(child) or not child.is_dir():
                continue
            jobs = _JOB_ID.findall(child.name)
            if any(job in active_jobs for job in jobs) or _overlaps(child, protected):
                continue
            if name == "analysis" and not _JOB_ID.fullmatch(child.name):
                continue
            if name == "covers" and (child.name in active_assets or
                                     (not include_referenced and child.name in asset_ids)):
                continue
            # Known staging and old analysis workspaces are disposable even for terminal jobs.
            if name != "analysis" and not _STAGING.fullmatch(child.name) and _overlaps(child, referenced):
                continue
            entries.update(_old_tree(child, cutoff))
        if name == "covers":
            for directory, subdirs, files in os.walk(folder, followlinks=False):
                parent = Path(directory)
                subdirs[:] = [name for name in subdirs if not _is_link(parent / name)]
                for filename in files:
                    path = parent / filename
                    match = _COVER_TEMP.fullmatch(filename)
                    if (path in entries or not match or match.group(1) in active_jobs
                            or _overlaps(path, protected)):
                        continue
                    info = _old_entry(path, cutoff)
                    if info is not None and stat.S_ISREG(info.st_mode):
                        entries[path] = info
    return entries


def _record_paths(body: object, keys: tuple[str, ...]) -> set[Path]:
    if not isinstance(body, dict):
        return set()
    return {Path(body[key]).resolve() for key in keys if isinstance(body.get(key), str) and body[key]}


def _overlaps(path: Path, references: set[Path]) -> bool:
    return any(path.is_relative_to(ref) or ref.is_relative_to(path) for ref in references)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _old_entry(path: Path, cutoff: float) -> os.stat_result | None:
    try:
        info = path.lstat()
        if _is_link(path) or info.st_mtime > cutoff:
            return None
        return info
    except OSError:
        return None


def _old_tree(path: Path, cutoff: float) -> dict[Path, os.stat_result]:
    info = _old_entry(path, cutoff)
    if info is None or not stat.S_ISDIR(info.st_mode):
        return {}
    entries = {path: info}
    try:
        for child in path.iterdir():
            info = _old_entry(child, cutoff)
            if info is None:
                return {}
            if stat.S_ISDIR(info.st_mode):
                subtree = _old_tree(child, cutoff)
                if not subtree:
                    return {}
                entries.update(subtree)
            elif stat.S_ISREG(info.st_mode):
                entries[child] = info
            else:
                return {}
    except OSError:
        return {}
    return entries


def _identity(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _same_entry(path: Path, expected: os.stat_result, root: Path, *, directory: bool = False) -> bool:
    # Reject replaced entries and any path now reached through a link or junction.
    if any(_is_link(item) for item in (path, *path.parents) if item != root and item.is_relative_to(root)):
        return False
    current = path.stat()
    if directory:
        # Removing children updates the parent timestamps.
        return _identity(current)[:3] == _identity(expected)[:3]
    return _identity(current) == _identity(expected)
