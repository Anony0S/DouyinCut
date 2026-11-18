"""Conservative cleanup for old, unreferenced application-generated files."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from .core.logs import ACTIVE_JOB_STATES
from .core.storage import Store

RETENTION_SECONDS = 7 * 24 * 60 * 60
_JOB_ID = re.compile(r"job_[0-9a-f]{32}")
_COVER_TEMP = re.compile(r"^(job_[0-9a-f]{32})\.(?:source\.(?:png|jpg|webp)|tmp\.jpg)$")
_STAGING = re.compile(r"^\.(export_(job_[0-9a-f]{32}))-attempt_[0-9a-f]{32}\.partial$")


def cleanup_unused_files(store: Store, data_dir: Path, *, now: float | None = None) -> dict:
    """Delete only old orphaned outputs and known abandoned staging files.

    User media, recordings, logs, the database, and outputs still referenced by
    application records are outside the cleanup scope.
    """
    now = time.time() if now is None else now
    cutoff = now - RETENTION_SECONDS
    root = Path(data_dir).resolve()
    result = {
        "deleted_files": 0,
        "deleted_directories": 0,
        "retained_files": 0,
        "bytes_reclaimed": 0,
        "cutoff_timestamp": cutoff,
    }

    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job_rows = conn.execute("SELECT id,state FROM jobs").fetchall()
        active_jobs = {row["id"] for row in job_rows if row["state"] in ACTIVE_JOB_STATES}
        asset_ids = {row[0] for row in conn.execute("SELECT DISTINCT id FROM objects WHERE kind='asset'")}
        referenced_paths: set[Path] = set()
        for row in conn.execute("SELECT body FROM objects WHERE kind IN ('asset','export')"):
            body = json.loads(row[0])
            for key in ("path", "video_path"):
                value = body.get(key)
                if isinstance(value, str):
                    referenced_paths.add(Path(value).resolve())
        for row in conn.execute("SELECT result FROM jobs WHERE result IS NOT NULL"):
            try:
                result_body = json.loads(row[0])
            except (TypeError, json.JSONDecodeError):
                continue
            for key in ("path", "draft_path", "video_path"):
                value = result_body.get(key) if isinstance(result_body, dict) else None
                if isinstance(value, str):
                    referenced_paths.add(Path(value).resolve())

        targets: list[tuple[Path, str]] = []

        # Published outputs are eligible only if their recorded path has become orphaned.
        for folder_name in ("exports", "previews"):
            folder = root / folder_name
            if not folder.is_dir() or folder.is_symlink():
                continue
            for child in folder.iterdir():
                if child.is_symlink() or not child.is_dir() or not _tree_old_enough(child, cutoff):
                    continue
                resolved = child.resolve()
                if not resolved.is_relative_to(folder.resolve()):
                    continue
                if any(_contains(path, resolved) or _contains(resolved, path) for path in referenced_paths):
                    continue
                job_match = _JOB_ID.search(child.name)
                if job_match and job_match.group(0) in active_jobs:
                    continue
                targets.append((child, "directory"))

        # Analysis folders are temporary job workspaces; keep them only while a job can still use them.
        analysis = root / "analysis"
        if analysis.is_dir() and not analysis.is_symlink():
            for child in analysis.iterdir():
                if child.is_symlink() or not child.is_dir() or not _tree_old_enough(child, cutoff):
                    continue
                if _JOB_ID.fullmatch(child.name) and child.name not in active_jobs:
                    targets.append((child, "directory"))

        # Cover folders are retained for every asset still present in the project database.
        covers = root / "covers"
        if covers.is_dir() and not covers.is_symlink():
            for child in covers.iterdir():
                if child.is_symlink() or not child.is_dir() or not _tree_old_enough(child, cutoff):
                    continue
                if child.name not in asset_ids and not any(
                    job_id in active_jobs and _job_uses_asset(conn, job_id, child.name)
                    for job_id in active_jobs
                ):
                    targets.append((child, "directory"))
            # Crash leftovers from image conversion are safe to remove once their job is terminal.
            for child in covers.rglob("*"):
                if child.is_symlink() or not child.is_file() or not _old_enough(child, cutoff):
                    continue
                match = _COVER_TEMP.fullmatch(child.name)
                if match and match.group(1) not in active_jobs:
                    targets.append((child, "file"))

        # Interrupted FFmpeg publication leaves only hidden staging directories.
        for folder_name in ("exports", "previews"):
            folder = root / folder_name
            if not folder.is_dir() or folder.is_symlink():
                continue
            for child in folder.iterdir():
                match = _STAGING.fullmatch(child.name)
                if (match and not child.is_symlink() and child.is_dir() and _tree_old_enough(child, cutoff)
                        and match.group(2) not in active_jobs):
                    targets.append((child, "directory"))

        seen: set[Path] = set()
        for path, kind in targets:
            resolved = path.resolve()
            if resolved in seen or path.is_symlink() or not resolved.is_relative_to(root):
                continue
            seen.add(resolved)
            if not (_tree_old_enough(path, cutoff) if kind == "directory" else _old_enough(path, cutoff)):
                continue
            file_sizes: dict[Path, int] = {}
            directory_paths: list[Path] = []
            try:
                if kind == "directory":
                    entries = list(path.rglob("*"))
                    if any(item.is_symlink() for item in entries):
                        result["retained_files"] += sum(item.is_file() or item.is_symlink() for item in entries)
                        continue
                    files = [item for item in entries if item.is_file()]
                    directory_paths = [path, *(item for item in entries if item.is_dir())]
                    file_sizes = {item: item.stat().st_size for item in files}
                    shutil.rmtree(path)
                    result["bytes_reclaimed"] += sum(file_sizes.values())
                    result["deleted_files"] += len(files)
                    result["deleted_directories"] += len(directory_paths)
                else:
                    file_size = path.stat().st_size
                    path.unlink()
                    result["bytes_reclaimed"] += file_size
                    result["deleted_files"] += 1
            except OSError:
                if kind == "directory":
                    removed = [item for item in file_sizes if not item.exists()]
                    result["deleted_files"] += len(removed)
                    result["bytes_reclaimed"] += sum(file_sizes[item] for item in removed)
                    result["retained_files"] += sum(item.exists() for item in file_sizes)
                    result["deleted_directories"] += sum(not item.exists() for item in directory_paths)
                elif path.exists():
                    result["retained_files"] += 1
                else:
                    result["deleted_files"] += 1
                    result["bytes_reclaimed"] += file_size

    return result


def _old_enough(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime <= cutoff
    except OSError:
        return False


def _tree_old_enough(path: Path, cutoff: float) -> bool:
    if not _old_enough(path, cutoff):
        return False
    try:
        return all(not item.is_symlink() and _old_enough(item, cutoff) for item in path.rglob("*"))
    except OSError:
        return False


def _contains(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _job_uses_asset(conn, job_id: str, asset_id: str) -> bool:
    row = conn.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return False
    try:
        return json.loads(row[0]).get("asset_id") == asset_id
    except (TypeError, json.JSONDecodeError):
        return False
