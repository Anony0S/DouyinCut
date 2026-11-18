"""Fill a missing timeline thumbnail after Jianying initializes its project."""
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

from .service import draft_root

logger = logging.getLogger(__name__)


def sync_pending_cover(draft: Path) -> bool:
    marker_path = draft / "douyincut_draft.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    timeline_id = marker.get("timeline_cover_pending")
    if marker.get("state") != "complete" or not isinstance(timeline_id, str) or not re.fullmatch(r"[A-Fa-f0-9]{32}", timeline_id):
        return False
    project = json.loads((draft / "Timelines" / "project.json").read_text(encoding="utf-8"))
    if project.get("main_timeline_id") != timeline_id:
        return False
    timeline = draft / "Timelines" / timeline_id
    if not (timeline / "draft_content.json").is_file():
        return False
    target = timeline / "draft_cover.jpg"
    source = draft / "Resources" / "DouyinCut_cover.jpg"
    if not target.exists():
        # Publish a complete copy without ever replacing a native/user cover.
        fd, temp_name = tempfile.mkstemp(prefix=".douyincut-cover-", suffix=".tmp", dir=draft)
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
            try:
                os.link(temp_name, target)
            except FileExistsError:
                pass
        finally:
            Path(temp_name).unlink(missing_ok=True)
    marker.pop("timeline_cover_pending")
    fd, temp_name = tempfile.mkstemp(prefix=".douyincut-marker-", suffix=".tmp", dir=draft)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(marker, output, ensure_ascii=False, indent=2)
        os.replace(temp_name, marker_path)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return True


def sync_pending_covers() -> None:
    try:
        root = draft_root()
    except RuntimeError:
        return
    for draft in root.glob("DC_*"):
        try:
            sync_pending_cover(draft)
        except (FileNotFoundError, json.JSONDecodeError):
            # Jianying has not finished initializing the project yet.
            continue
        except (OSError, ValueError, TypeError):
            logger.warning("Failed to synchronize Jianying cover for %s", draft, exc_info=True)
