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
                subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                                "-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=25:d=3",
                                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
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
            expected_durations = [source["end"] - source["start"] for source in period["sources"]]
            job = live.request_period_product_clips(period["id"], [])
            Worker(settings).run_once()
            completed = store.job(job["id"])
            self.assertEqual(completed["state"], "succeeded", completed["error"])
            self.assertEqual(completed["result"]["created"], 2)
            clips = sorted(live.recording_session(monitor["id"], session_id)["product_clips"],
                           key=lambda clip: clip["segment_index"])
            self.assertEqual({clip["promotion_id"] for clip in clips}, {"chosen"})
            for clip, expected in zip(clips, expected_durations, strict=True):
                media = probe(Path(clip["path"]), settings)
                self.assertEqual(Path(clip["path"]).suffix, ".mp4")
                self.assertTrue(media["has_audio"])
                self.assertGreater(media["width"], 0)
                self.assertAlmostEqual(media["duration_us"] / 1_000_000, expected, delta=.15)
            for source, digest in originals:
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
            self.assertEqual(live.request_period_product_clips(period["id"], [])["state"], "noop")
