"""Find expired records together with the files and records still in use."""

import hashlib
import json
import os
import re
import stat
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


_FOLDERS = ("analysis", "previews", "exports", "covers", "recordings/imports", "recordings/live", "logs")
_JOB_NAME = re.compile(r"job_[0-9a-f]{32}")
_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".ts", ".m4v", ".webm", ".mpeg", ".mpg"}
_SESSION_TABLES = {
    "live_segment_product_clips": "end",
    "live_merged_product_clips": "end",
    "live_segments": "ended",
    "live_product_events": "received_at",
    "live_product_periods": "ended",
    "live_session_current_product": "received_at",
    "live_session_product_catalog_state": "updated",
    "live_product_listener_state": "updated",
    "live_product_listener_retries": "requested",
}
_MONITOR_TABLES = (
    "live_monitor_products", "live_monitor_product_catalog", "live_monitor_product_misses",
    "live_monitor_liveness", "live_monitor_controls", "live_room_platforms",
    "live_platform_product_cache",
)


@dataclass
class RecordGroup:
    rows: list = field(default_factory=list)
    files: set[Path] = field(default_factory=set)
    originals: dict[Path, tuple[int, int] | str] = field(default_factory=dict)
    depends_on: set[tuple[str, str]] = field(default_factory=set)
    retained: bool = False

    def add(self, table, row, timestamp, cutoff):
        self.rows.append((table, row))
        if timestamp is not None and timestamp > cutoff:
            self.retained = True

    def link(self, kind, identifier):
        if isinstance(identifier, str) and identifier:
            self.depends_on.add((kind, identifier))


def _rows(conn, table, where=""):
    return conn.execute(f"SELECT rowid, * FROM {table} {where} ORDER BY rowid")


def _path(value):
    if not isinstance(value, str):
        return None
    return Path(os.path.abspath(value)) if Path(value).is_absolute() else None


def _under(path, roots):
    return any(path.is_relative_to(root) for root in roots)


def _linked(path):
    return path.is_symlink() or path.is_junction()


def _safe_stat(path):
    try:
        if any(_linked(part) for part in (*reversed(path.parents), path)):
            return None
        return path.lstat()
    except FileNotFoundError:
        return None


def _managed_paths(body, keys, roots):
    for key in keys:
        path = _path(body.get(key))
        if path and _under(path, roots):
            yield path


def _register_original(group, path, file_id, sha256=None):
    if not path or path.suffix.lower() not in _VIDEO_SUFFIXES:
        return
    identity = tuple(file_id) if file_id else sha256
    if not identity:
        return
    info = _safe_stat(path)
    if info and stat.S_ISREG(info.st_mode) and (isinstance(identity, str) or identity == (info.st_dev, info.st_ino)):
        group.files.add(path)
        group.originals[path] = identity


def _project_records(conn, root, cutoff, groups):
    roots = tuple(root / folder for folder in _FOLDERS)
    assets_by_hash = defaultdict(set)
    for row in _rows(conn, "objects", "WHERE kind<>'config'"):
        kind, identifier, body = row["kind"], row["id"], json.loads(row["body"])
        group = groups[kind, identifier]
        group.add("objects", row, row["created"], cutoff)
        group.link("asset", body.get("source_asset_id"))
        group.link("plan", body.get("plan_id"))
        group.link("product", body.get("product_id"))
        if kind == "asset":
            assets_by_hash[body.get("sha256")].add(identifier)
            source = _path(body.get("path"))
            if source and _under(source, roots):
                group.files.add(source)
            original = _path(body.get("original_path")) or source
            if original and (body.get("original_path") or not _under(original, roots)):
                _register_original(group, original, body.get("original_file_id"), body.get("sha256"))
                if original not in group.originals and _under(original, roots):
                    protected = groups["protected_path", str(original)]
                    protected.retained = True
                    protected.files.add(original)
            group.files.add(root / "covers" / identifier)
            group.files.add(root / "previews" / "assets" / f"{identifier}.mp4")
            group.link("live_monitors", body.get("source_monitor_id"))
        elif kind == "project":
            group.link("asset", identifier)
        elif kind == "transcript":
            group.link("asset", body.get("segment", {}).get("source_asset_id"))
        elif kind == "plan":
            for clip in body.get("clips", []):
                group.link("asset", clip.get("source_asset_id"))
                group.link("candidate", clip.get("candidate_id"))
        elif kind == "export":
            group.files.update(_managed_paths(body, ("path", "video_path"), roots))

    for row in _rows(conn, "jobs"):
        identifier = row["id"]
        group = groups["jobs", identifier]
        group.add("jobs", row, row["updated"], cutoff)
        if row["state"] in ("queued", "running", "paused", "pause_requested", "cancel_requested"):
            group.retained = True
        payload = json.loads(row["payload"])
        result = json.loads(row["result"] or "{}")
        group.link("asset", payload.get("asset_id"))
        group.link("plan", payload.get("plan_id"))
        group.link("asset", result.get("asset_id"))
        group.link("live_sessions", payload.get("session_id"))
        group.files.update(_managed_paths(payload, ("path", "cover_path", "reference_path"), roots))
        group.files.update(_managed_paths(result, ("path", "video_path", "original_path"), roots))
        if row["kind"] == "import":
            _register_original(group, _path(payload.get("original_path")), payload.get("original_file_id"))
        group.files.add(root / "analysis" / identifier)
        for folder in ("previews", "exports"):
            group.files.add(root / folder / f"export_{identifier}")

    cache_groups = {}
    for table, stamp in (("attempts", "ended"), ("cloud_operations", "updated")):
        for row in _rows(conn, table):
            key = ("jobs", row["job_id"])
            groups[key].add(table, row, row[stamp], cutoff)
            if table == "cloud_operations" and row["cache_key"]:
                cache_groups[row["cache_key"]] = key

    for row in _rows(conn, "cache"):
        cache_key = row["key"]
        prefix, _, owner = cache_key.partition(":")
        group_key = cache_groups.get(cache_key)
        if prefix in ("job-result", "roughcut-timestamps-v1", "roughcut-transcript"):
            group_key = ("jobs", owner)
        if prefix == "qwen-audio-v1":
            asset_hash = owner.partition(":")[0]
            group_key = ("cache", cache_key)
            for identifier in assets_by_hash[asset_hash]:
                groups["asset", identifier].depends_on.add(group_key)
        if group_key:
            groups[group_key].add("cache", row, None, cutoff)


def _live_records(conn, root, cutoff, groups):
    binding_groups = {}
    for row in _rows(conn, "live_monitors"):
        group = groups["live_monitors", row["id"]]
        group.add("live_monitors", row, row["created"], cutoff)
    for table in _MONITOR_TABLES:
        for row in _rows(conn, table):
            key = ("live_monitors", row["monitor_id"])
            groups[key].add(table, row, row["updated"] if table == "live_monitor_controls" else None, cutoff)
            if table == "live_room_platforms":
                binding_groups[row["binding_id"]] = key
    for row in _rows(conn, "daduoduo_catalogs"):
        key = binding_groups.get(row["binding_id"], ("daduoduo_catalogs", row["binding_id"]))
        groups[key].add("daduoduo_catalogs", row, row["synced_at"], cutoff)
    for row in _rows(conn, "live_sessions"):
        group = groups["live_sessions", row["id"]]
        group.add("live_sessions", row, row["ended"] or row["started"], cutoff)
        group.link("live_monitors", row["monitor_id"])
        group.files.add(root / "recordings" / "live" / row["monitor_id"] / row["id"])
        if row["status"] == "recording":
            group.retained = True
    for table, stamp in _SESSION_TABLES.items():
        for row in _rows(conn, table):
            key = ("live_sessions", row["session_id"])
            group = groups[key]
            group.add(table, row, row[stamp], cutoff)
            if "path" in row.keys():
                group.files.update(_managed_paths(dict(row), ("path",), (root / "recordings" / "live",)))
                if row["asset_id"]:
                    groups["asset", row["asset_id"]].depends_on.add(key)
                if row["import_job_id"]:
                    groups["jobs", row["import_job_id"]].depends_on.add(key)


def _expired_files(folder, cutoff, protected, retained_jobs, entries):
    """Collect old files independently and only remove directories left empty."""
    info = _safe_stat(folder)
    if folder in protected or info is None or not stat.S_ISDIR(info.st_mode):
        return False
    if (folder / "douyincut_draft.json").is_file():
        return False
    all_removed = True
    for path in folder.iterdir():
        if path in protected or any(job in retained_jobs for job in _JOB_NAME.findall(path.name)):
            all_removed = False
            continue
        child = path.lstat()
        if _linked(path):
            all_removed = False
        elif stat.S_ISDIR(child.st_mode):
            if _expired_files(path, cutoff, protected, retained_jobs, entries) and child.st_mtime <= cutoff:
                entries[path] = child
            else:
                all_removed = False
        elif stat.S_ISREG(child.st_mode) and child.st_mtime <= cutoff:
            entries[path] = child
        else:
            all_removed = False
    return all_removed


def _original_matches(path, info, identity):
    if not isinstance(identity, str):
        return identity == (info.st_dev, info.st_ino)
    with path.open("rb") as source:
        current = os.fstat(source.fileno())
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
            return False
        return hashlib.file_digest(source, "sha256").hexdigest() == identity


def expired_business_data(conn, root, cutoff):
    groups = defaultdict(RecordGroup)
    _project_records(conn, root, cutoff, groups)
    _live_records(conn, root, cutoff, groups)

    for group in groups.values():
        for path in group.files:
            info = _safe_stat(path)
            if info and stat.S_ISREG(info.st_mode) and info.st_mtime > cutoff:
                group.retained = True
                break

    retained = {key for key, group in groups.items() if group.retained}
    pending = list(retained)
    while pending:
        key = pending.pop()
        for dependency in groups[key].depends_on - retained:
            if dependency in groups:
                retained.add(dependency)
                pending.append(dependency)
    protected = {path for key in retained for path in groups[key].files}
    retained_jobs = {identifier for kind, identifier in retained if kind == "jobs"}
    entries = {}
    for folder in _FOLDERS:
        _expired_files(root / folder, cutoff, protected, retained_jobs, entries)

    records = defaultdict(list)
    originals = set()
    for key, group in groups.items():
        if key in retained:
            continue
        for path, identity in group.originals.items():
            if any(parent in protected for parent in (path, *path.parents)):
                continue
            info = _safe_stat(path)
            if (info and stat.S_ISREG(info.st_mode) and info.st_mtime <= cutoff
                    and _original_matches(path, info, identity)):
                entries[path] = info
                originals.add(str(path))
        for table, row in group.rows:
            records[table].append(row)
    return entries, {table: sorted(rows, key=lambda row: row[0]) for table, rows in sorted(records.items())}, sorted(originals)
