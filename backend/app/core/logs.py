"""Runtime diagnostics: one line format for every process, and safe access to the log files."""

import codecs
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from .config import Settings
from .storage import Store

RUNTIME_LOG = "desktop-runtime.log"
PREVIOUS_RUNTIME_LOG = "desktop-runtime.previous.log"
READ_LIMIT = 512 * 1024
ACTIVE_JOB_STATES = ("queued", "running", "pause_requested", "paused", "cancel_requested")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.log")
_JOB_LOG = re.compile(r"(job_[0-9a-f]{32})-")
_CLIP_LOG = re.compile(r"product-clip-(product_clip_[0-9a-f]{32})-")
_configured = False


def configure_logging(role: str) -> None:
    """Give this process the shared line format on stderr.

    The native host appends stderr of every role to the runtime log. The development
    launcher has no such redirect and names a file to copy lines into instead.
    """
    global _configured
    if _configured:
        return
    _configured = True
    formatter = logging.Formatter(f"%(asctime)s %(levelname)-8s [{role}] %(name)s: %(message)s",
                                  "%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    copy = os.environ.get("DOUYINCUT_LOG_FILE")
    if copy:
        Path(copy).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(copy, encoding="utf-8"))
    root = logging.getLogger()
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    # Third-party loggers stay at WARNING: their INFO lines include request URLs.
    for name in ("app", "douyincut"):
        logging.getLogger(name).setLevel(logging.INFO)
    # uvicorn's own handler prints without a time; route its records through this format.
    uvicorn = logging.getLogger("uvicorn")
    uvicorn.handlers = []
    uvicorn.propagate = True


def _owner(name: str) -> tuple[str, str] | None:
    """The job or product clip whose FFmpeg output a task log holds."""
    if match := _JOB_LOG.match(name):
        return "job", match.group(1)
    if match := _CLIP_LOG.match(name):
        return "product_clip", match.group(1)
    return None


class LogLibrary:
    """The two places diagnostics are written.

    `runtime` is the application log beside the workspace locator; `task` is per-task
    FFmpeg output under the work directory. Only plain `.log` names inside those two
    directories are ever read, opened or removed.
    """

    def __init__(self, settings: Settings, store: Store):
        self.store = store
        self.task_dir = settings.data_dir / "logs"
        self.runtime_dir = settings.runtime_log.parent if settings.runtime_log else None

    def _directory(self, category: str) -> Path:
        if category == "task":
            return self.task_dir
        if category == "runtime" and self.runtime_dir is not None:
            return self.runtime_dir
        raise KeyError(category)

    def _path(self, category: str, name: str) -> Path:
        directory = self._directory(category)
        valid = name in (RUNTIME_LOG, PREVIOUS_RUNTIME_LOG) if category == "runtime" else _NAME.fullmatch(name)
        if not valid:
            raise ValueError("日志文件名无效")
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
            raise KeyError(name)
        return path

    def _task_entries(self):
        if not self.task_dir.is_dir():
            return
        with os.scandir(self.task_dir) as entries:
            for entry in entries:
                # Without a workspace locator the application log lives in this directory too.
                if (_NAME.fullmatch(entry.name) and entry.name not in (RUNTIME_LOG, PREVIOUS_RUNTIME_LOG)
                        and entry.is_file(follow_symlinks=False)):
                    yield entry

    def _rows(self, query: str, ids: list[str]):
        rows = []
        with self.store.connection() as conn:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                rows += conn.execute(query.format(",".join("?" * len(chunk))), chunk).fetchall()
        return rows

    def files(self) -> list[dict]:
        """Non-empty log files: runtime first, then task logs, newest first."""
        runtime, tasks = [], []
        if self.runtime_dir is not None:
            for name in (RUNTIME_LOG, PREVIOUS_RUNTIME_LOG):
                path = self.runtime_dir / name
                if path.is_file() and not path.is_symlink() and (stat := path.stat()).st_size:
                    runtime.append({"category": "runtime", "name": name, "size": stat.st_size,
                                    "modified": stat.st_mtime, "group": None, "group_type": None,
                                    "job_kind": None, "product_name": None})
        for entry in self._task_entries():
            stat = entry.stat()
            if not stat.st_size:
                continue
            owner = _owner(entry.name)
            tasks.append({"category": "task", "name": entry.name, "size": stat.st_size,
                          "modified": stat.st_mtime, "group": owner[1] if owner else None,
                          "group_type": owner[0] if owner else None, "job_kind": None, "product_name": None})
        kinds = {row["id"]: row["kind"] for row in self._rows(
            "SELECT id,kind FROM jobs WHERE id IN ({})",
            sorted({item["group"] for item in tasks if item["group_type"] == "job"}))}
        names = {row["id"]: row["product_name"] for row in self._rows(
            "SELECT id,product_name FROM live_segment_product_clips WHERE id IN ({})",
            sorted({item["group"] for item in tasks if item["group_type"] == "product_clip"}))}
        for item in tasks:
            item["job_kind"] = kinds.get(item["group"])
            item["product_name"] = names.get(item["group"])
        tasks.sort(key=lambda item: (-item["modified"], item["name"]))
        return runtime + tasks

    def read(self, category: str, name: str, after: int | None = None) -> dict:
        """The end of a file, or what was appended after byte offset `after`.

        `reset` reports that the file became shorter than `after` (it was cleared or
        replaced), in which case the end of the new content is returned instead.
        """
        path = self._path(category, name)
        with path.open("rb") as file:
            size = file.seek(0, os.SEEK_END)
            reset = after is not None and after > size
            from_end = after is None or reset
            start = max(0, size - READ_LIMIT) if from_end else after
            file.seek(start)
            data = file.read(READ_LIMIT)
        truncated = from_end and start > 0
        if truncated:
            # Begin on a line boundary rather than inside a line or a multi-byte character.
            skip = data.find(b"\n") + 1
            data, start = data[skip:], start + skip
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        text = decoder.decode(data)
        # Bytes of a character still being written stay for the next read.
        pending = len(decoder.getstate()[0])
        return {"text": text, "start": start, "next": start + len(data) - pending, "size": size,
                "truncated": truncated, "reset": reset}

    def open_location(self, category: str, name: str | None = None) -> dict:
        if os.name != "nt":
            raise ValueError("打开日志位置仅支持 Windows")
        if name:
            subprocess.Popen(["explorer", "/select,", str(self._path(category, name))])
        else:
            directory = self._directory(category)
            if not directory.is_dir():
                raise KeyError(category)
            os.startfile(str(directory))
        return {"opened": True}

    def clear(self) -> dict:
        """Remove diagnostics that no running work still needs."""
        result = {"runtime_cleared": False, "deleted_files": 0, "retained_files": 0, "bytes_reclaimed": 0}
        if self.runtime_dir is not None:
            current = self.runtime_dir / RUNTIME_LOG
            if current.is_file() and not current.is_symlink():
                size = current.stat().st_size
                try:
                    # Running processes hold this file open for appending: empty it in place.
                    with current.open("r+b") as file:
                        file.truncate(0)
                    result["runtime_cleared"] = True
                    result["bytes_reclaimed"] += size
                except OSError:
                    result["retained_files"] += 1
            self._remove(self.runtime_dir / PREVIOUS_RUNTIME_LOG, result)
        with self.store.connection() as conn:
            marks = ",".join("?" * len(ACTIVE_JOB_STATES))
            active = {("job", row["id"]) for row in conn.execute(
                f"SELECT id FROM jobs WHERE state IN ({marks})", ACTIVE_JOB_STATES)}
            active |= {("product_clip", row["id"]) for row in conn.execute(
                "SELECT id FROM live_segment_product_clips WHERE status='processing'")}
        for entry in list(self._task_entries()):
            if _owner(entry.name) in active:
                result["retained_files"] += 1
            else:
                self._remove(Path(entry.path), result)
        return result

    @staticmethod
    def _remove(path: Path, result: dict) -> None:
        if path.is_symlink() or not path.is_file():
            return
        size = path.stat().st_size
        try:
            path.unlink()
        except OSError:
            result["retained_files"] += 1
        else:
            result["deleted_files"] += 1
            result["bytes_reclaimed"] += size
