import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    port: int = 8765

    @classmethod
    def from_env(cls):
        root = Path(__file__).resolve().parents[2]
        return cls(
            data_dir=Path(os.environ.get("DOUYINCUT_DATA_DIR", root / "data")).resolve(),
            ffmpeg=os.environ.get("DOUYINCUT_FFMPEG", "ffmpeg"),
            ffprobe=os.environ.get("DOUYINCUT_FFPROBE", "ffprobe"),
            port=int(os.environ.get("DOUYINCUT_PORT", "8765")),
        )

    def prepare(self):
        for folder in ["database", "recordings", "analysis", "previews", "exports", "logs"]:
            (self.data_dir / folder).mkdir(parents=True, exist_ok=True)

    @property
    def database(self) -> Path:
        return self.data_dir / "database" / "douyincut.sqlite3"
