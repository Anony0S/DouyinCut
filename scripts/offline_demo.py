"""Create clearly synthetic inputs and exercise the real CLI exporter without cloud calls."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings
from app.media.processing import run_process


def main():
    demo = ROOT / ".runtime" / "offline-demo"
    demo.mkdir(parents=True, exist_ok=True)
    settings = Settings(demo / "data")
    source = demo / "合成测试素材.mp4"
    run_process([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                 "testsrc2=size=320x240:rate=30:duration=6", "-f", "lavfi", "-i",
                 "sine=frequency=440:sample_rate=48000:duration=6", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)])
    recipe = {
        "product_name": "合成测试素材（非真实服装识别）", "target_duration_us": 4_000_000,
        "clips": [
            {"path": str(source), "start_us": 3_000_000, "end_us": 5_000_000, "tags": ["front"],
             "transcripts": [{"start_us": 3_200_000, "end_us": 4_200_000, "text": "测试字幕：先播放后半段"}]},
            {"path": str(source), "start_us": 0, "end_us": 2_000_000, "tags": ["detail"],
             "transcripts": [{"start_us": 200_000, "end_us": 1_200_000, "text": "测试字幕：再播放前半段"}]},
        ],
    }
    recipe_path = demo / "recipe.json"
    recipe_path.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "DOUYINCUT_DATA_DIR": str(settings.data_dir), "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-m", "app.cli", "render", str(recipe_path)],
                            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", check=True)
    artifact = json.loads(result.stdout)
    (demo / "result.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(artifact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
