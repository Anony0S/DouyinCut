import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.api import create_app
from app.core.config import Settings
from app.media.processing import probe
from app.media.thumbnails import ThumbnailCache, PreviewBusy


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required')
class LiveThumbnailTests(unittest.TestCase):
    def test_portrait_and_landscape_keep_top_and_bottom(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary))
            cache = ThumbnailCache(settings)
            for width, height in ((180, 320), (320, 180), (240, 240)):
                with self.subTest(size=(width, height)):
                    source = Path(temporary) / f'{width}x{height}.ts'
                    subprocess.run([settings.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                    f'color=c=red:s={width}x{height}:r=25:d=1', '-vf',
                                    f'drawbox=x=0:y={height // 2}:w=iw:h=ih/2:color=blue:t=fill',
                                    '-c:v', 'libx264', '-f', 'mpegts', str(source)],
                                   check=True, capture_output=True)
                    frame = cache.frame(source, .25)
                    image = Path(temporary) / 'preview.jpg'
                    image.write_bytes(frame)
                    result = subprocess.run([settings.ffprobe, '-v', 'error', '-show_streams',
                                             '-of', 'json', str(image)], check=True, capture_output=True)
                    info = json.loads(result.stdout)['streams'][0]
                    self.assertAlmostEqual(info['width'] / info['height'], width / height, delta=.01)
                    self.assertEqual(max(info['width'], info['height']), 320)
                    pixels = subprocess.run([settings.ffmpeg, '-v', 'error', '-i', 'pipe:0',
                                             '-vf', 'scale=8:8', '-frames:v', '1', '-pix_fmt',
                                             'rgb24', '-f', 'rawvideo', 'pipe:1'], input=frame,
                                            check=True, capture_output=True).stdout
                    top, bottom = pixels[12:15], pixels[-12:-9]
                    self.assertGreater(top[0], top[2] + 100, 'top red marker remains visible')
                    self.assertGreater(bottom[2], bottom[0] + 100, 'bottom blue marker remains visible')

    def test_real_ts_frames_mapping_cache_and_source_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary))
            app = create_app(settings, token='test')
            live = app.state.live_store
            monitor = live.create_monitor('https://live.douyin.com/thumbnail', 'preview')
            session = live.start_session(monitor['id'], Path(temporary))
            started = time.time() - 20
            with app.state.store.connection() as conn:
                conn.execute('UPDATE live_sessions SET started=? WHERE id=?', (started, session))
            directory = settings.data_dir / 'recordings' / 'live' / monitor['id'] / session
            directory.mkdir(parents=True)
            originals = []
            for index, color in enumerate(('red', 'blue')):
                source = directory / f'{index}.ts'
                subprocess.run([settings.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                                f'color=c={color}:s=160x120:r=25:d=4', '-c:v', 'libx264',
                                '-g', '25', '-f', 'mpegts', str(source)], check=True, capture_output=True)
                originals.append((source, hashlib.sha256(source.read_bytes()).hexdigest()))
                live.upsert_segment(session, source, index + 1, 'completed', source.stat().st_size,
                                    probe(source, settings)['duration_us'])
            tail = directory / 'tail.ts'
            tail.write_bytes(b'recording')
            live.upsert_segment(session, tail, 3, 'recording', tail.stat().st_size)
            live.set_current_product(session, promotion_id='product', product_name='product',
                                     event_time=started + .25, received_at=started + .25)
            period = live.recording_session(monitor['id'], session)['product_periods'][0]
            url = f"/api/v1/live/product-periods/{period['id']}/thumbnail"
            with TestClient(app) as client:
                headers = {'X-Session-Token': 'test'}
                self.assertEqual(client.get(url, params={'at': started + 1}).status_code, 401)
                with patch('app.media.thumbnails.subprocess.run', wraps=subprocess.run) as run:
                    for offset, dominant in ((1.4, 0), (5.4, 2)):
                        response = client.get(url, params={'at': started + offset}, headers=headers)
                        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
                        self.assertEqual(response.headers['content-type'], 'image/jpeg')
                        pixel = subprocess.run([settings.ffmpeg, '-v', 'error', '-i', 'pipe:0', '-vf',
                                                'scale=1:1', '-frames:v', '1', '-pix_fmt', 'rgb24',
                                                '-f', 'rawvideo', 'pipe:1'], input=response.content,
                                               capture_output=True, check=True).stdout
                        self.assertEqual(len(pixel), 3)
                        self.assertGreater(pixel[dominant], max(pixel[(dominant + 1) % 3], pixel[(dominant + 2) % 3]) + 50)
                        repeated = client.get(url, params={'at': started + offset + .1}, headers=headers)
                        self.assertEqual(repeated.content, response.content)
                    self.assertEqual(run.call_count, 4, 'two decodes and two pixel inspections; repeats use cache')
                for at in (started - 1, started + 8.5, started + 50):
                    self.assertEqual(client.get(url, params={'at': at}, headers=headers).status_code, 409)
                for at in ('nan', 'inf', '-1'):
                    self.assertEqual(client.get(url, params={'at': at}, headers=headers).status_code, 422)
                originals[0][0].unlink()
                self.assertEqual(client.get(url, params={'at': started + 1.4}, headers=headers).status_code, 409,
                                 'deleted files must not be served from the frame cache')
                self.assertEqual(hashlib.sha256(originals[1][0].read_bytes()).hexdigest(), originals[1][1])

    def test_preview_does_not_queue_when_decoder_is_busy(self):
        cache = ThumbnailCache(Settings(Path('.')))
        cache.lock.acquire()
        try:
            with self.assertRaises(PreviewBusy):
                cache.frame(Path('unused.ts'), 1)
        finally:
            cache.lock.release()

    def test_ts_seek_near_start_transition_and_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary))
            source = Path(temporary) / 'transition.ts'
            subprocess.run([settings.ffmpeg, '-v', 'error', '-y',
                            '-f', 'lavfi', '-i', 'color=c=red:s=160x120:r=25:d=20',
                            '-f', 'lavfi', '-i', 'color=c=blue:s=160x120:r=25:d=4',
                            '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
                            '-c:v', 'libx264', '-g', '25', '-f', 'mpegts', str(source)],
                           check=True, capture_output=True)
            cache = ThumbnailCache(settings)
            for second, dominant in ((0, 0), (1.25, 0), (16.25, 0), (20.25, 2), (23.25, 2)):
                frame = cache.frame(source, second)
                pixel = subprocess.run([settings.ffmpeg, '-v', 'error', '-i', 'pipe:0', '-vf',
                                        'scale=1:1', '-frames:v', '1', '-pix_fmt', 'rgb24',
                                        '-f', 'rawvideo', 'pipe:1'], input=frame, capture_output=True, check=True).stdout
                self.assertEqual(len(pixel), 3)
                self.assertGreater(pixel[dominant], max(pixel[(dominant + 1) % 3], pixel[(dominant + 2) % 3]) + 50)

    def test_long_keyframe_intervals_fall_back_to_accurate_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Settings(Path(temporary))
            source = Path(temporary) / 'long-gop.ts'
            subprocess.run([settings.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                            'testsrc2=s=160x120:r=25:d=24', '-c:v', 'libx264', '-preset',
                            'ultrafast', '-g', '500', '-sc_threshold', '0', '-f', 'mpegts', str(source)],
                           check=True, capture_output=True)
            cache = ThumbnailCache(settings)
            for second in (1.25, 16.25, 20.25, 23.25):
                with self.subTest(second=second):
                    reference = subprocess.run([settings.ffmpeg, '-v', 'error', '-copyts',
                                                '-start_at_zero', '-threads', '1', '-filter_threads', '1',
                                                '-i', str(source), '-ss', str(second), '-frames:v', '1',
                                                '-an', '-vf', 'scale=320:320:force_original_aspect_ratio=decrease',
                                                '-threads', '1', '-c:v', 'mjpeg', '-q:v', '5',
                                                '-f', 'image2pipe', 'pipe:1'], check=True, capture_output=True).stdout
                    self.assertEqual(cache.frame(source, second), reference,
                                     'fast seeks must match sequential decoding, including long GOPs and tail frames')
