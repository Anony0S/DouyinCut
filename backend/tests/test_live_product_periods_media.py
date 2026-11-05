"""Real media coverage for lecture-scoped, cross-segment product cuts."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from app.core.config import Settings
from app.core.storage import Store
from app.live_monitoring import LiveStore
from app.media.processing import probe
from app.worker import Worker


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class ProductPeriodMediaTests(unittest.TestCase):
    def test_worker_cuts_only_one_lecture_from_two_ts_sources_to_playable_mp4(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary))
            settings.prepare()
            store = Store(settings.database)
            live = LiveStore(store)
            monitor = live.create_monitor("https://live.douyin.com/media-period", "media")
            session_id = live.start_session(monitor["id"], Path(temporary))
            started = time.time() - 10
            with store.connection() as conn:
                conn.execute("UPDATE live_sessions SET started=? WHERE id=?", (started, session_id))
            directory = settings.data_dir / "recordings" / "live" / monitor["id"] / session_id
            directory.mkdir(parents=True)
            originals = []
            for index, color in enumerate(("red", "blue")):
                source = directory / f"media-{index:06d}.ts"
                size = "160x120" if index == 0 else "120x160"
                args = [settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=25:d=3"]
                if index == 0:
                    args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=3"]
                subprocess.run([*args, "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                                "-f", "mpegts", str(source)], check=True, capture_output=True)
                originals.append((source, hashlib.sha256(source.read_bytes()).hexdigest()))
                live.upsert_segment(session_id, source, index + 1, "completed", source.stat().st_size,
                                    probe(source, settings)["duration_us"])
            for offset, promotion_id in ((0, "before"), (1, "chosen"), (4.5, "after")):
                live.set_current_product(session_id, promotion_id=promotion_id, product_name=promotion_id,
                                         event_time=started + offset, received_at=started + offset)
            live.stop_session(monitor["id"], session_id)
            detail = live.recording_session(monitor["id"], session_id)
            period = next(item for item in detail["product_periods"] if item["promotion_id"] == "chosen")
            selected_start, selected_end = started + 1.2, started + 4.3
            job = live.request_period_product_clips(period["id"], [], selected_start, selected_end)
            Worker(settings).run_once()
            completed = store.job(job["id"])
            self.assertEqual(completed["state"], "succeeded", completed["error"])
            self.assertEqual(completed["result"]["created"], 1)
            clips = live.recording_session(monitor["id"], session_id)["product_clips"]
            self.assertEqual(len(clips), 1)
            self.assertEqual({clip["promotion_id"] for clip in clips}, {"chosen"})
            clip = clips[0]
            self.assertEqual((clip["start"], clip["end"]), (selected_start, selected_end))
            media = probe(Path(clip["path"]), settings)
            self.assertEqual(Path(clip["path"]).suffix, ".mp4")
            self.assertTrue(media["has_audio"])
            self.assertEqual((media["width"], media["height"]), (160, 120))
            self.assertAlmostEqual(media["duration_us"] / 1_000_000, selected_end - selected_start, delta=.15)
            for second, dominant in ((.5, 0), (2.5, 2)):
                pixel = subprocess.run([
                    settings.ffmpeg, "-v", "error", "-ss", str(second), "-i", clip["path"],
                    "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
                ], check=True, capture_output=True).stdout
                self.assertEqual(len(pixel), 3)
                self.assertGreater(pixel[dominant], max(pixel[(dominant + 1) % 3], pixel[(dominant + 2) % 3]) + 50)
            self.assertFalse(list(directory.glob(".product-clip-*")))
            self.assertEqual(len(list(directory.glob("商品片段_*.mp4"))), 1)
            for source, digest in originals:
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
            self.assertEqual(live.request_period_product_clips(period["id"], [])["state"], "noop")
