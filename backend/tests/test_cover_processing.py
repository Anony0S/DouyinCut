import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.core.config import Settings
from app.media.processing import prepare_cover_jpeg, run_process


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg 与 ffprobe")
class CoverProcessingTests(unittest.TestCase):
    def test_proportions_preserve_resolution_and_jpeg_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(data_dir=root, ffmpeg=shutil.which("ffmpeg"), ffprobe=shutil.which("ffprobe"))
            for width, height, expected in [(300, 400, (300, 400)), (320, 480, (320, 427)), (500, 400, (300, 400))]:
                with self.subTest(width=width, height=height):
                    source = root / f"{width}-{height}.jpg"
                    target = root / f"{width}-{height}-out.jpg"
                    run_process([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                                 f"color=red:s={width}x{height}", "-frames:v", "1", str(source)])
                    self.assertEqual(prepare_cover_jpeg(source, target, settings), expected)
                    data = json.loads(run_process([settings.ffprobe, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "json", str(target)]))
                    self.assertEqual((data["streams"][0]["width"], data["streams"][0]["height"]), expected)
                    if width * 4 == height * 3:
                        self.assertEqual(source.read_bytes(), target.read_bytes())
                    second_target = root / f"{width}-{height}-second.jpg"
                    self.assertEqual(prepare_cover_jpeg(target, second_target, settings), expected)
                    self.assertEqual(target.read_bytes(), second_target.read_bytes())
