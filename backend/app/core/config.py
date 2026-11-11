import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    port: int = 8765
    # The application log that the process owner appends stderr to; None when nothing collects it.
    runtime_log: Path | None = None

    @classmethod
    def from_env(cls):
        root = Path(__file__).resolve().parents[2]
        return cls(
            data_dir=Path(os.environ.get("DOUYINCUT_DATA_DIR", root / "data")).resolve(),
            ffmpeg=os.environ.get("DOUYINCUT_FFMPEG", "ffmpeg"),
            ffprobe=os.environ.get("DOUYINCUT_FFPROBE", "ffprobe"),
            port=int(os.environ.get("DOUYINCUT_PORT", "8765")),
            runtime_log=Path(os.environ["DOUYINCUT_LOG_FILE"]).resolve() if os.environ.get("DOUYINCUT_LOG_FILE") else None,
        )

    def prepare(self):
        for folder in ["database", "recordings", "analysis", "previews", "exports", "logs"]:
            (self.data_dir / folder).mkdir(parents=True, exist_ok=True)

    @property
    def database(self) -> Path:
        return self.data_dir / "database" / "douyincut.sqlite3"
