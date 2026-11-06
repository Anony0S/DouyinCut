import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.media import encoding
from app.media.processing import MediaError
from app.worker import Cancelled, Paused


class ProductClipEncodingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.settings = Settings(Path(temporary.name), ffmpeg="configured-ffmpeg")
        self.output = Path(temporary.name) / "selected.mp4"
        cache = patch.dict(encoding._encoders, clear=True)
        cache.start()
        self.addCleanup(cache.stop)

    def test_advertised_encoder_must_pass_real_probe_and_result_is_cached(self):
        def run(args, **kwargs):
            self.assertEqual(args[0], "configured-ffmpeg")
            if "-encoders" in args:
                return " V..... h264_nvenc encoder\n V..... h264_qsv encoder\n V..... h264_amf encoder"
            if args[args.index("-c:v") + 1] != "h264_amf":
                raise MediaError("Driver/device unavailable")
            self.assertIn("-frames:v", args)
            return ""
        with patch("app.media.processing.run_process", side_effect=run) as process:
            first = encoding.select_h264_encoder(self.settings)
            second = encoding.select_h264_encoder(self.settings)
        self.assertEqual(first.name, "h264_amf")
        self.assertEqual(first, second)
        self.assertEqual(process.call_count, 4)

    def test_advertised_but_unusable_gpu_falls_back_to_cpu_and_is_cached(self):
        with patch("app.media.processing.run_process", side_effect=[
            " V..... h264_nvenc encoder", MediaError("No GPU"),
        ]) as process:
            self.assertEqual(encoding.select_h264_encoder(self.settings), encoding.CPU_ENCODER)
            self.assertEqual(encoding.select_h264_encoder(self.settings), encoding.CPU_ENCODER)
        self.assertEqual(process.call_count, 2)

    def test_encoder_cache_is_scoped_to_configured_ffmpeg(self):
        with patch("app.media.processing.run_process", return_value="") as process:
            encoding.select_h264_encoder(self.settings)
            encoding.select_h264_encoder(Settings(self.settings.data_dir, ffmpeg="another-ffmpeg"))
        self.assertEqual(process.call_count, 2)

    def test_hardware_failure_retries_same_selection_with_limited_cpu_and_removes_partial(self):
        hardware = encoding.HARDWARE_ENCODERS[0]
        calls = []
        def run(args, **kwargs):
            self.assertFalse(self.output.exists())
            calls.append(args)
            self.output.write_bytes(b"partial" if len(calls) == 1 else b"finished")
            if len(calls) == 1:
                raise MediaError("Hardware session failed")
        base = [self.settings.ffmpeg, "-ss", "7", "-i", "source.mp4", "-t", "20"]
        with (patch("app.media.encoding.select_h264_encoder", return_value=hardware),
              patch("app.media.processing.run_process", side_effect=run),
              patch("app.media.encoding.os.cpu_count", return_value=16)):
            selected = encoding.encode_h264(base, self.output, self.settings, duration=20)
        self.assertEqual(selected, "libx264")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(args[:len(base)] == base for args in calls))
        self.assertEqual(calls[1][calls[1].index("-threads:v") + 1], "4")
        self.assertEqual(self.output.read_bytes(), b"finished")

    def test_final_failure_cleans_partial_output(self):
        def run(*args, **kwargs):
            self.output.write_bytes(b"partial")
            raise MediaError("Encode failed")
        with (patch("app.media.encoding.select_h264_encoder", return_value=encoding.HARDWARE_ENCODERS[0]),
              patch("app.media.processing.run_process", side_effect=run) as process):
            with self.assertRaises(MediaError):
                encoding.encode_h264([self.settings.ffmpeg], self.output, self.settings, duration=1)
        self.assertEqual(process.call_count, 2)
        self.assertFalse(self.output.exists())

    def test_pause_and_cancel_during_hardware_encoding_never_trigger_cpu_retry(self):
        for exception in (Paused, Cancelled):
            with self.subTest(exception=exception):
                def run(*args, **kwargs):
                    self.output.write_bytes(b"partial")
                    raise exception()
                with (patch("app.media.encoding.select_h264_encoder", return_value=encoding.HARDWARE_ENCODERS[0]),
                      patch("app.media.processing.run_process", side_effect=run) as process):
                    with self.assertRaises(exception):
                        encoding.encode_h264([self.settings.ffmpeg], self.output, self.settings, duration=1)
                self.assertEqual(process.call_count, 1)
                self.assertFalse(self.output.exists())

    def test_cancel_during_detection_propagates_without_caching_unavailability(self):
        with patch("app.media.processing.run_process", side_effect=[
            " V..... h264_amf encoder", Cancelled(),
        ]):
            with self.assertRaises(Cancelled):
                encoding.select_h264_encoder(self.settings)
        self.assertNotIn(self.settings.ffmpeg, encoding._encoders)


if __name__ == "__main__":
    unittest.main()
