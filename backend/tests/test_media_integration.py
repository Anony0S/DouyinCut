import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.core.domain import Candidate, Plan, Product, Transcript
from app.media.editing import build_plan
from app.media.exporting import export_plan
from app.media.processing import extract_audio, import_asset, probe, run_process
from app.core.storage import Store
from app.worker import Worker


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 FFmpeg 与 ffprobe")
class MediaIntegrationTests(unittest.TestCase):
    def test_shutdown_pauses_real_ffmpeg_export_at_checkpoint_and_preserves_published_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(root / 'work')
            settings.prepare()
            source = root / 'source.mp4'
            run_process([settings.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                         '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=2',
                         '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
                         '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(source)])
            source_bytes = source.read_bytes()
            asset = import_asset(source, settings)
            product = Product(name='退出检查点', confirmed=True)
            candidate = Candidate(product_id=product.id, source_asset_id=asset.id,
                                  start_us=0, end_us=2_000_000)
            plan = build_plan(product, [candidate], {asset.id: asset}, [], target_us=2_000_000)
            plan = plan.model_copy(update={'status': 'approved'})
            completed = export_plan(plan, {asset.id: asset}, settings, 'already-published', package=False)
            completed_video = Path(completed['video_path']).read_bytes()
            store = Store(settings.database)
            store.put('asset', asset.model_dump())
            store.put('product', product.model_dump())
            store.put('candidate', candidate.model_dump())
            store.put('plan', plan.model_dump())
            job = store.enqueue('export', {'plan_id': plan.id, 'revision': plan.revision, 'format': 'mp4'})
            worker = Worker(settings)
            ffmpeg_started = threading.Event()
            allow_checkpoint = threading.Event()
            original_run_process = __import__('app.media.processing', fromlist=['run_process']).run_process

            def controlled_run_process(args, checkpoint=None, **kwargs):
                if Path(args[0]).name.lower().startswith('ffmpeg') and checkpoint:
                    def controlled_checkpoint():
                        ffmpeg_started.set()
                        if not allow_checkpoint.wait(10):
                            raise AssertionError('test did not release the media checkpoint')
                        checkpoint()
                    return original_run_process(args, checkpoint=controlled_checkpoint, **kwargs)
                return original_run_process(args, checkpoint=checkpoint, **kwargs)

            with patch('app.media.exporting.run_process', side_effect=controlled_run_process):
                execution = threading.Thread(target=worker.run_once)
                execution.start()
                self.assertTrue(ffmpeg_started.wait(10), 'real FFmpeg export did not start')
                store.pause_for_shutdown()
                allow_checkpoint.set()
                execution.join(15)

            self.assertFalse(execution.is_alive(), 'worker must finish its safe stop')
            self.assertEqual(store.job(job['id'])['state'], 'paused')
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(Path(completed['video_path']).read_bytes(), completed_video)
            self.assertEqual(list((settings.data_dir / 'exports').glob('*.partial')), [])
            self.assertEqual(list((settings.data_dir / 'exports').glob('.*.partial')), [])

    def test_asr_roughcut_automatically_stitches_selected_sentences_and_subtitles(self):
        from app.ai.providers import CloudConfig
        from app.ai.roughcut import TimeSelection
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp) / 'data'); settings.prepare()
            source = Path(temp) / '奶黄千层.mp4'
            run_process([settings.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                         'color=s=160x120:r=25:d=6', '-f', 'lavfi', '-i',
                         'sine=frequency=440:sample_rate=48000:duration=6',
                         '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)])
            asset = import_asset(source, settings)
            store = Store(settings.database); store.put('asset', asset.model_dump())
            job = store.enqueue('analyze', {'asset_id': asset.id, 'product_name': '奶黄千层',
                                            'config': CloudConfig().model_dump()})
            response = {'output': {'sentence': {'begin_time': 0, 'end_time': 6000,
                'text': '上身显瘦。只要九十九。面料柔软。', 'words': [
                    {'begin_time': 0, 'end_time': 1000, 'text': '上身显瘦', 'punctuation': '。'},
                    {'begin_time': 1000, 'end_time': 2000, 'text': '只要九十九', 'punctuation': '。'},
                    {'begin_time': 3000, 'end_time': 4500, 'text': '面料柔软', 'punctuation': '。'}]}}}
            selection = TimeSelection.model_validate({'clips': [
                {'start_ms': 3000, 'end_ms': 4500, 'reason': '面料'},
                {'start_ms': 0, 'end_ms': 1000, 'reason': '上身'}]})
            worker = Worker(settings)
            with patch('app.ai.providers.asr_api_key', return_value='mock-key'), \
                    patch('app.ai.providers.upload_audio', return_value='https://oss.example/audio'), \
                    patch('app.ai.providers.cloud_json', return_value=response), \
                    patch('app.ai.roughcut.select_intervals', return_value=selection):
                worker.run_once()
            self.assertEqual(store.job(job['id'])['state'], 'succeeded')
            self.assertTrue(worker.run_once())  # Real FFmpeg automatic preview.
            preview_job = next(j for j in store.jobs() if j['kind'] == 'preview')
            self.assertEqual(preview_job['state'], 'succeeded', preview_job.get('error'))
            artifact = store.list('export')[0]
            info = probe(Path(artifact['video_path']), settings)
            self.assertLess(abs(info['duration_us'] - 2_500_000), 150_000)
            self.assertTrue(info['has_audio'])
            subtitles = (Path(artifact['path']) / 'subtitles.srt').read_text(encoding='utf-8-sig')
            self.assertIn('00:00:00,000 --> 00:00:01,500\n面料柔软。', subtitles)
            self.assertIn('00:00:01,500 --> 00:00:02,500\n上身显瘦。', subtitles)
            self.assertNotIn('九十九', subtitles)
            self.assertFalse(worker.run_once())

    def test_qwen_audio_chunk_is_mono_16khz_wav_with_correct_tail_duration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(root / "data"); settings.prepare()
            source = root / "source.mp4"
            run_process([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                         "color=s=160x120:r=25:d=11", "-f", "lavfi", "-i",
                         "sine=frequency=440:sample_rate=48000:duration=11",
                         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)])
            asset = import_asset(source, settings)
            target = root / "tail.wav"
            extract_audio(asset, target, settings, start_us=10_000_000, end_us=11_000_000)
            with wave.open(str(target)) as audio:
                self.assertEqual((audio.getnchannels(), audio.getframerate(), audio.getsampwidth()), (1, 16000, 2))
                self.assertLess(abs(audio.getnframes() / 16000 - 1), 0.01)
                self.assertTrue(any(audio.readframes(audio.getnframes())))

    def test_export_keeps_audio_and_video_at_same_source_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(root / "data"); settings.prepare()
            source = root / "delayed-video.mp4"
            video = "color=c=red:s=160x120:r=25:d=2[a];color=c=blue:s=160x120:r=25:d=2[b];[a][b]concat=n=2:v=1:a=0"
            audio = "sine=frequency=440:sample_rate=48000:duration=3.5[a];sine=frequency=880:sample_rate=48000:duration=2.5[b];[a][b]concat=n=2:v=0:a=1"
            run_process([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                         "-itsoffset", "1.5", "-f", "lavfi", "-i", video,
                         "-f", "lavfi", "-i", audio,
                         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-shortest", str(source)])
            asset = import_asset(source, settings)
            self.assertGreater(asset.video_start_us - asset.audio_start_us, 1_400_000)
            product = Product(name="offset test", confirmed=True)
            candidate = Candidate(product_id=product.id, source_asset_id=asset.id,
                                  start_us=3_000_000, end_us=4_000_000)
            plan = build_plan(product, [candidate], {asset.id: asset}, [], target_us=1_000_000,
                              ordered_ids=[candidate.id])
            result = export_plan(plan, {asset.id: asset}, settings, "offset-test", package=False)
            def frame_at(seconds):
                return subprocess.run([settings.ffmpeg, "-v", "error", "-ss", str(seconds),
                                       "-i", result["video_path"], "-frames:v", "1", "-vf", "scale=1:1",
                                       "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                                      capture_output=True, check=True).stdout

            def audio_crossings_at(seconds):
                pcm = subprocess.run([settings.ffmpeg, "-v", "error", "-ss", str(seconds),
                                      "-i", result["video_path"], "-t", "0.2", "-vn", "-ac", "1",
                                      "-ar", "48000", "-f", "s16le", "pipe:1"],
                                     capture_output=True, check=True).stdout
                samples = array('h'); samples.frombytes(pcm)
                return sum(left <= 0 < right or left >= 0 > right
                           for left, right in zip(samples, samples[1:]))

            early, late = frame_at(0.1), frame_at(0.75)
            self.assertGreater(early[0], early[2] + 100)
            self.assertGreater(late[2], late[0] + 100)
            self.assertGreater(audio_crossings_at(0.7), audio_crossings_at(0.1) * 1.5)

    def test_export_motion_continues_immediately_at_each_clip_start(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(root / "data"); settings.prepare()
            source = root / "moving.mp4"
            run_process([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=8",
                         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
                         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)])
            asset = import_asset(source, settings)
            product = Product(name="motion", confirmed=True)
            candidates = [Candidate(product_id=product.id, source_asset_id=asset.id,
                                    start_us=start, end_us=start + 1_500_000)
                          for start in (500_000, 4_000_000)]
            plan = build_plan(product, candidates, {asset.id: asset}, [], target_us=3_000_000,
                              ordered_ids=[candidate.id for candidate in candidates])
            result = export_plan(plan, {asset.id: asset}, settings, "motion-boundaries", package=False)

            def frame_differences(start):
                raw = subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error",
                                      "-ss", str(start), "-i", result["video_path"], "-t", "0.8",
                                      "-vf", "fps=10,scale=32:24", "-pix_fmt", "rgb24",
                                      "-f", "rawvideo", "pipe:1"], capture_output=True, check=True).stdout
                frame_size = 32 * 24 * 3
                frames = [raw[i:i + frame_size] for i in range(0, len(raw), frame_size)]
                return sum(left != right for left, right in zip(frames, frames[1:]))

            self.assertGreaterEqual(frame_differences(0.1), 5)
            self.assertGreaterEqual(frame_differences(1.6), 5)

    def test_actual_render_with_reordered_clips_and_silent_source(self):
        with tempfile.TemporaryDirectory(prefix="douyincut-") as temp:
            root=Path(temp)/"中文 空格目录";root.mkdir()
            settings=Settings(root/"data");settings.prepare()
            red=root/"红色 有声音.mp4";blue=root/"蓝色 静音.mp4"
            run_process([settings.ffmpeg,"-v","error","-y","-f","lavfi","-i","color=c=red:s=320x240:r=25:d=3",
                         "-f","lavfi","-i","sine=frequency=440:sample_rate=48000:duration=3","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-shortest",str(red)])
            run_process([settings.ffmpeg,"-v","error","-y","-f","lavfi","-i","color=c=blue:s=240x320:r=30:d=3",
                         "-c:v","libx264","-pix_fmt","yuv420p",str(blue)])
            a,b=import_asset(red,settings),import_asset(blue,settings)
            product=Product(name="人工确认测试款",confirmed=True)
            ca=Candidate(product_id=product.id,source_asset_id=a.id,start_us=500_000,end_us=1_500_000)
            cb=Candidate(product_id=product.id,source_asset_id=b.id,start_us=250_000,end_us=1_250_000)
            transcripts=[Transcript(source_asset_id=a.id,start_us=600_000,end_us=1_200_000,text="原始中文口播")]
            plan=build_plan(product,[ca,cb],{a.id:a,b.id:b},transcripts,target_us=2_000_000,ordered_ids=[cb.id,ca.id])
            self.assertEqual(plan.subtitles[0].start_us,1_100_000)
            result=export_plan(plan,{a.id:a,b.id:b},settings,"test")
            output=Path(result["video_path"])
            info=probe(output,settings)
            self.assertLess(abs(info["duration_us"]-2_000_000),150_000)
            self.assertTrue(info["has_audio"])
            self.assertEqual((info["width"],info["height"]),(240,320))
            frame=subprocess.run([settings.ffmpeg,"-v","error","-ss","0.5","-i",str(output),"-frames:v","1","-vf","scale=1:1","-pix_fmt","rgb24","-f","rawvideo","pipe:1"],capture_output=True,check=True).stdout
            self.assertGreater(frame[2],frame[0]+100)  # first clip must actually be blue
            self.assertIn("00:00:01,100",(output.parent/"subtitles.srt").read_text(encoding="utf-8-sig"))
            manifest=json.loads((output.parent/"manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["clips"][0]["source_asset_id"],b.id)
            self.assertTrue((output.parent/"clips/001.mp4").is_file())
            self.assertFalse(list((settings.data_dir/"exports").glob("*.partial")))
            recovered=export_plan(plan,{a.id:a,b.id:b},settings,"test")
            self.assertEqual(recovered,result)
            self.assertEqual(len(list((settings.data_dir/"exports").iterdir())),1)

    def test_import_worker_persists_and_does_not_repeat_completed_job(self):
        with tempfile.TemporaryDirectory() as temp:
            settings=Settings(Path(temp)/"data");settings.prepare()
            path=Path(temp)/"source.mp4"
            run_process([settings.ffmpeg,"-v","error","-y","-f","lavfi","-i","color=s=160x120:d=1",
                         "-c:v","libx264",str(path)])
            store=Store(settings.database)
            job=store.enqueue("import",{"path":str(path)})
            worker=Worker(settings)
            self.assertTrue(worker.run_once())
            self.assertEqual(store.job(job["id"])["state"],"succeeded")
            self.assertFalse(worker.run_once())
            self.assertEqual(len(store.list("asset")),1)

    def test_live_ts_import_converts_project_asset_to_mp4_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp) / "data"); settings.prepare()
            source = Path(temp) / "source.ts"
            run_process([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                         "color=s=160x120:r=25:d=1", "-f", "lavfi", "-i",
                         "sine=frequency=440:sample_rate=48000:duration=1", "-c:v", "libx264",
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mpegts", str(source)])
            store = Store(settings.database)
            job = store.enqueue("import", {"path": str(source), "display_name": source.name,
                                            "live_product_clip_id": "clip-ts-import"})

            self.assertTrue(Worker(settings).run_once())

            asset = store.get("asset", store.job(job["id"])["result"]["asset_id"])
            self.assertEqual(Path(asset["path"]).suffix, ".mp4")
            self.assertEqual(asset["name"], "source.mp4")
            output = Path(asset["path"])
            self.assertGreater(probe(output, settings)["duration_us"], 0)
            codecs = json.loads(run_process([settings.ffprobe, "-v", "error", "-show_streams",
                                             "-of", "json", str(output)]))
            self.assertEqual({stream["codec_type"]: stream["codec_name"] for stream in codecs["streams"]},
                             {"video": "h264", "audio": "aac"})
            self.assertTrue(source.is_file())
            self.assertFalse((settings.data_dir / "previews" / "assets").exists())

    def test_ts_conversion_copies_h264_aac_streams_without_reencoding(self):
        from app.media.processing import convert_to_mp4

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(root / "data"); settings.prepare()
            source, target = root / "copy-source.ts", root / "copy-target.mp4"
            run_process([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                         "testsrc2=size=160x120:rate=25:duration=2", "-f", "lavfi", "-i",
                         "sine=frequency=440:sample_rate=48000:duration=2", "-c:v", "libx264",
                         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mpegts", str(source)])
            with patch("app.media.processing.run_process", wraps=run_process) as run:
                convert_to_mp4(source, target, settings)

            ffmpeg_calls = [call.args[0] for call in run.call_args_list
                            if Path(call.args[0][0]).name == Path(settings.ffmpeg).name]
            self.assertEqual(len(ffmpeg_calls), 1)
            self.assertIn(["-c:v", "copy"], [ffmpeg_calls[0][i:i + 2]
                                                for i in range(len(ffmpeg_calls[0]) - 1)])
            self.assertIn(["-c:a", "copy"], [ffmpeg_calls[0][i:i + 2]
                                                for i in range(len(ffmpeg_calls[0]) - 1)])
            streams = json.loads(run_process([settings.ffprobe, "-v", "error", "-show_streams",
                                              "-of", "json", str(target)]))["streams"]
            self.assertEqual({stream["codec_type"]: stream["codec_name"] for stream in streams},
                             {"video": "h264", "audio": "aac"})

    def test_ts_conversion_transcodes_incompatible_video_to_h264(self):
        from app.media.processing import convert_to_mp4

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(root / "data"); settings.prepare()
            source, target = root / "mpeg2-source.ts", root / "mpeg2-target.mp4"
            run_process([settings.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                         "testsrc2=size=160x120:rate=25:duration=1", "-f", "lavfi", "-i",
                         "sine=frequency=440:sample_rate=48000:duration=1", "-c:v", "mpeg2video",
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mpegts", str(source)])

            with patch("app.media.processing.run_process", wraps=run_process) as run:
                convert_to_mp4(source, target, settings)

            ffmpeg_calls = [call.args[0] for call in run.call_args_list
                            if Path(call.args[0][0]).name == Path(settings.ffmpeg).name]
            self.assertEqual(len(ffmpeg_calls), 1)
            self.assertIn(["-c:v", "libx264"], [ffmpeg_calls[0][i:i + 2]
                                                   for i in range(len(ffmpeg_calls[0]) - 1)])
            streams = json.loads(run_process([settings.ffprobe, "-v", "error", "-show_streams",
                                              "-of", "json", str(target)]))["streams"]
            self.assertEqual({stream["codec_type"]: stream["codec_name"] for stream in streams},
                             {"video": "h264", "audio": "aac"})


if __name__ == "__main__":
    unittest.main()
