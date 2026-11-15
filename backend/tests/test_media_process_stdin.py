import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.media.processing import MediaError, probe, probe_stream_timing, run_process


class MediaProcessStdinTests(unittest.TestCase):
    def test_child_receives_eof_instead_of_host_control_input(self):
        with patch('app.media.processing.subprocess.Popen', wraps=subprocess.Popen) as popen:
            output = run_process([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'], timeout=5)
        self.assertEqual(output.strip(), "''")
        self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_ffprobe_json_is_not_contaminated_by_decoder_diagnostics(self):
        metadata = {
            'format': {'duration': '1200.063166'},
            'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920, 'start_time': '1.4'},
                        {'codec_type': 'audio', 'start_time': '1.38'}],
        }
        script = ("import sys; print('[h264] non-existing SPS 0 referenced in buffering period', "
                  "file=sys.stderr); print(" + repr(json.dumps(metadata)) + ")")
        popen = subprocess.Popen

        def noisy_ffprobe(_args, **kwargs):
            return popen([sys.executable, '-c', script], **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary))
            with patch('app.media.processing.subprocess.Popen', side_effect=noisy_ffprobe):
                info = probe(Path('recording.ts'), settings)
                timing = probe_stream_timing(Path('recording.ts'), settings)
        self.assertEqual(info['duration_us'], 1_200_063_166)
        self.assertEqual((info['width'], info['height']), (1080, 1920))
        self.assertEqual(timing, {'video_start_us': 1_400_000, 'audio_start_us': 1_380_000})

    def test_stdout_stays_separate_while_logs_keep_both_streams(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'media.log'
            for returncode in (0, 3):
                with self.subTest(returncode=returncode):
                    script = ("import sys; print('metadata'); print('decoder diagnostic', "
                              f"file=sys.stderr); sys.exit({returncode})")
                    if returncode:
                        with self.assertRaises(MediaError):
                            run_process([sys.executable, '-c', script], log_path=log)
                    else:
                        output = run_process([sys.executable, '-c', script], log_path=log)
                        self.assertEqual(output.strip(), 'metadata')
                    self.assertIn('metadata', log.read_text(encoding='utf-8'))
                    self.assertIn('decoder diagnostic', log.read_text(encoding='utf-8'))
