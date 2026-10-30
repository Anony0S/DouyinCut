"""Verify a relocated frozen workbench using bundled tools and saved cloud fixtures.

No cloud submissions: only successful current-format ASR/decision cache records
are seeded while the runtime is stopped. Real import, preview, plan editing and
export jobs run in the frozen worker through the authenticated production API.
"""

import argparse
from array import array
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.ai.providers import CloudConfig
from app.bundled_media import bundled_settings
from app.core.storage import Store
from app.work_directory import WorkDirectoryRegistry
from runtime_process import assert_process_exited, stop_owned_process


class FrozenSession:
    def __init__(self, executable, root, work, locator, env):
        self.messages = queue.Queue()
        self.errors = (root / 'runtime-stderr.log').open('a', encoding='utf-8')
        self.process = subprocess.Popen(
            [str(executable), '--work-dir', str(work), '--workspace-config', str(locator), '--host-controlled'],
            cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors,
            text=True, encoding='utf-8',
        )

        def read():
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
            self.messages.put({'event': 'eof'})

        threading.Thread(target=read, daemon=True).start()
        try:
            assert self.messages.get(timeout=20)['event'] == 'awaiting_host'
            self.process.stdin.write('start\n')
            self.process.stdin.flush()
            ready = self.messages.get(timeout=60)
            assert ready['event'] == 'ready', ready
            self.client = httpx.Client(base_url=ready['url'], headers={'X-Session-Token': ready['token']},
                                       trust_env=False, timeout=30)
        except BaseException:
            stop_owned_process(self.process)
            self.errors.close()
            raise

    def request(self, method, path, **kwargs):
        result = self.client.request(method, '/api/v1' + path, **kwargs)
        result.raise_for_status()
        return result.json()

    def job(self, job_id):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = self.request('GET', '/jobs/' + job_id)
            if job['state'] == 'succeeded':
                return job
            assert job['state'] not in {'failed', 'cancelled'}, job
            time.sleep(0.2)
        raise AssertionError(f'Task did not finish: {job_id}')

    def idle(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            jobs = self.request('GET', '/jobs')
            assert not [job for job in jobs if job['state'] == 'failed'], jobs
            if all(job['state'] == 'succeeded' for job in jobs):
                return
            time.sleep(0.2)
        raise AssertionError('Frozen jobs did not become idle')

    def close(self):
        try:
            self.client.close()
            self.process.stdin.write('shutdown\n')
            self.process.stdin.flush()
            self.process.wait(timeout=20)
            assert self.process.returncode == 0
            stopped = self.messages.get(timeout=5)
            assert stopped['event'] == 'stopped', stopped
            for role in stopped['roles'].values():
                assert not role['alive'] and role['exitcode'] == 0, stopped
                assert_process_exited(role['pid'])
        finally:
            stop_owned_process(self.process)
            self.errors.close()


def run(tool, *args, env):
    return subprocess.run([str(tool), '-hide_banner', '-loglevel', 'error', *args], env=env,
                          capture_output=True, check=True, timeout=60).stdout


def check_artifact(artifact, tools, env, blue_first=True):
    video = Path(artifact['video_path'])
    metadata = json.loads(run(tools.ffprobe, '-show_format', '-show_streams', '-of', 'json', str(video), env=env))
    assert abs(float(metadata['format']['duration']) - 2.5) < 0.15, metadata
    audio = next(stream for stream in metadata['streams'] if stream['codec_type'] == 'audio')
    visual = next(stream for stream in metadata['streams'] if stream['codec_type'] == 'video')
    assert abs(float(audio['duration']) - float(visual['duration'])) < 0.15

    def pixel(at):
        return run(tools.ffmpeg, '-ss', str(at), '-i', str(video), '-frames:v', '1',
                   '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1', env=env)

    def crossings(at):
        data = run(tools.ffmpeg, '-ss', str(at), '-i', str(video), '-t', '0.2', '-vn',
                   '-ar', '16000', '-ac', '1', '-f', 's16le', 'pipe:1', env=env)
        samples = array('h', data)
        return sum(left <= 0 < right for left, right in zip(samples, samples[1:]))

    first, last = pixel(0.3), pixel(2.0)
    assert len(first) == len(last) == 3
    if blue_first:
        assert first[2] > first[0] + 100 and last[0] > last[2] + 100, (first, last)
        assert crossings(0.3) > crossings(2.0) * 1.5
        expected = '00:00:00,000 --> 00:00:01,500\n面料柔软。'
    else:
        assert first[0] > first[2] + 100 and last[2] > last[0] + 100, (first, last)
        assert crossings(2.0) > crossings(0.3) * 1.5
        expected = '00:00:01,000 --> 00:00:02,500\n面料柔软。'
    subtitles = (video.parent / 'subtitles.srt').read_text(encoding='utf-8-sig')
    assert expected in subtitles, subtitles
    assert '上身显瘦。' in subtitles and '九十九' not in subtitles
    return hashlib.sha256(video.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--keep-output', type=Path, help='New isolated evidence directory; never an existing work directory')
    args = parser.parse_args()
    temporary = tempfile.TemporaryDirectory(prefix='DouyinCut 媒体 验收 ') if args.keep_output is None else None
    root = Path(temporary.name) if temporary else args.keep_output.resolve()
    if temporary is None:
        root.mkdir(parents=True, exist_ok=False)
    try:
        copied = root / '程序 runtime'
        shutil.copytree(args.runtime.resolve().parent, copied)
        executable = copied / args.runtime.name
        tools = bundled_settings(root / '工作 数据', copied / '_internal')
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(('PYTHON', 'VITE_', 'DOUYINCUT_', 'UV_', 'VIRTUAL_ENV'))}
        system_root = os.environ['SystemRoot']
        env['PATH'] = os.pathsep.join([str(Path(system_root) / 'System32'), system_root])
        for key in ('LLM_API_KEY', 'ASR_API_KEY', 'OSS_ACCESS_KEY_ID', 'OSS_ACCESS_KEY_SECRET'):
            env['DOUYINCUT_' + key] = 'offline-fixture-never-submit'
        env['DOUYINCUT_FFMPEG'] = str(root / 'must-not-use-ffmpeg.exe')
        env['DOUYINCUT_FFPROBE'] = str(root / 'must-not-use-ffprobe.exe')
        source = root / '用户 原视频.mp4'
        run(tools.ffmpeg, '-y', '-f', 'lavfi', '-i',
            'color=c=red:s=160x120:r=30:d=3[a];color=c=blue:s=160x120:r=30:d=3[b];[a][b]concat=n=2:v=1:a=0',
            '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=3[a];sine=frequency=880:sample_rate=48000:duration=3[b];[a][b]concat=n=2:v=0:a=1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source), env=env)
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        work = tools.data_dir
        work.mkdir()
        locator = root / '用户 配置' / 'workspace.json'
        WorkDirectoryRegistry(locator).select(work)
        session = FrozenSession(executable, root, work, locator, env)
        draft = {'product_name': '离线验收款', 'instructions': '保留面料和上身，去掉价格',
                 'target_duration_us': 15_000_000, 'duration_tolerance_us': 15_000_000}
        try:
            assert session.client.get('/').status_code == 200
            assert session.client.get('/api/v1/events', headers={'X-Session-Token': 'wrong'}).status_code == 401
            with session.client.stream('GET', '/api/v1/events') as stream:
                assert stream.status_code == 200
                assert next(stream.iter_lines()) == 'event: jobs'
            job = session.request('POST', '/assets/upload', params={'filename': source.name}, content=source.read_bytes())
            imported = session.job(job['id'])
            asset_id = imported['result']['asset_id']
            detail = session.request('GET', '/projects/' + asset_id)
            asset = detail['asset']
            assert (asset['width'], asset['height'], asset['has_audio']) == (160, 120, True)
            assert asset['sha256'] == original and Path(asset['path']) != source
            assert Path(asset['path']).is_relative_to(work / 'recordings' / 'imports')
            media_url = '/api/v1/assets/' + asset_id + '/media'
            assert session.client.get(media_url, headers={'X-Session-Token': 'wrong'}).status_code == 401
            assert hashlib.sha256(session.client.get(media_url).content).hexdigest() == original
            assert session.client.post('/api/v1/auth').status_code == 200
            cookie_media = session.client.get(media_url, headers={'X-Session-Token': ''})
            assert cookie_media.status_code == 200
            session.request('PATCH', '/projects/' + asset_id, json=draft)
        finally:
            session.close()

        # Controlled cloud-success fixture at the durable filesystem boundary.
        # Invalid hosts and dummy process credentials prevent accidental paid calls.
        store = Store(tools.database)
        store.start_session()
        config = CloudConfig(base_url='https://offline-fixture.invalid/v1', asr_base_url='https://offline-fixture.invalid')
        endpoint = hashlib.sha256(config.asr_url.encode()).hexdigest()
        response = {'output': {'sentence': {'begin_time': 0, 'end_time': 6000,
            'text': '上身显瘦。只要九十九。面料柔软。', 'words': [
                {'begin_time': 0, 'end_time': 1000, 'text': '上身显瘦', 'punctuation': '。'},
                {'begin_time': 1000, 'end_time': 2000, 'text': '只要九十九', 'punctuation': '。'},
                {'begin_time': 3000, 'end_time': 4500, 'text': '面料柔软', 'punctuation': '。'}]}}}
        store.cache(f"qwen-audio-v1:{asset['sha256']}:{endpoint}:{config.asr_model}:0:{asset['duration_us']}",
                    {'status': 'succeeded', 'response': response})
        job = store.enqueue('analyze', {'asset_id': asset_id, 'config': config.model_dump(), **draft})
        store.cache('roughcut-timestamps-v1:' + job['id'], {'clips': [
            {'start_ms': 3000, 'end_ms': 4500, 'reason': '面料'},
            {'start_ms': 0, 'end_ms': 1000, 'reason': '上身'}]})
        session = FrozenSession(executable, root, work, locator, env)
        try:
            assert session.client.post('/api/v1/auth').status_code == 200
            analysis = session.job(job['id'])
            session.idle()
            transcript = session.request('GET', '/jobs/' + job['id'] + '/transcript')
            assert transcript['ready'] and len(transcript['sentences']) == 3
            detail = session.request('GET', '/projects/' + asset_id)
            plan = next(plan for plan in detail['plans'] if plan['id'] == analysis['result']['plan_id'])
            preview = next(item for item in detail['artifacts'] if item['format'] == 'preview')
            check_artifact(preview, tools, env)
            old_preview_hash = hashlib.sha256(Path(preview['video_path']).read_bytes()).hexdigest()
            plan['clips'].reverse()
            cursor = 0
            for clip in plan['clips']:
                clip['timeline_start_us'] = cursor
                cursor += clip['source_end_us'] - clip['source_start_us']
            saved = session.request('PATCH', '/plans/' + plan['id'], json=plan)
            assert saved['revision'] == plan['revision'] + 1
            session.idle()
            approved = session.request('POST', '/plans/' + plan['id'] + '/approve')
            export = session.request('POST', '/plans/' + plan['id'] + '/exports', json={'format': 'mp4'})
            output = session.job(export['id'])['result']
            output_hash = check_artifact(output, tools, env, blue_first=False)
            assert output['revision'] == approved['revision']
            assert session.request('GET', '/projects/' + asset_id)['draft'] == draft
            for item in (preview, output):
                url = '/api/v1/exports/' + item['id'] + '/media'
                with httpx.Client(base_url=session.client.base_url, trust_env=False) as anonymous:
                    assert anonymous.get(url).status_code == 401
                streamed = session.client.get(url, headers={'Range': 'bytes=0-63', 'X-Session-Token': ''})
                assert streamed.status_code == 206 and len(streamed.content) == 64
            repeated = session.request('POST', '/plans/' + plan['id'] + '/exports', json={'format': 'mp4'})
            second = session.job(repeated['id'])['result']
            assert second['path'] != output['path']
            assert hashlib.sha256(Path(output['video_path']).read_bytes()).hexdigest() == output_hash
            assert hashlib.sha256(Path(preview['video_path']).read_bytes()).hexdigest() == old_preview_hash
            assert hashlib.sha256(source.read_bytes()).hexdigest() == original
            assert not list((work / 'exports').glob('*.partial'))
            
            # Test JianYing draft generation with bundled templates
            jianying_job = session.request('POST', '/plans/' + plan['id'] + '/jianying-drafts',
                                          json={'revision': approved['revision']})
            jianying = session.job(jianying_job['id'])['result']
            draft_path = Path(jianying['draft_path'])
            assert draft_path.exists(), f"JianYing draft not found at {draft_path}"
            assert (draft_path / "draft_meta_info.json").exists(), "draft_meta_info.json missing"
            assert (draft_path / "draft_content.json").exists(), "draft_content.json missing"
            assert (draft_path / "draft_materials").is_dir(), "draft_materials directory missing"
            
            # Verify draft contains video clips and subtitles
            draft_content = json.loads((draft_path / "draft_content.json").read_text(encoding="utf-8"))
            materials = draft_content.get("materials", {})
            assert "videos" in materials, "No videos in draft materials"
            assert len(materials["videos"]) > 0, "Draft has no video materials"
            
            # Verify materials directory contains actual media files
            material_files = list((draft_path / "draft_materials").glob("*"))
            assert len(material_files) > 0, "draft_materials directory is empty"
            
            report = {'result': 'passed', 'bundled_version': '8.1-full_build-www.gyan.dev',
                      'jianying_draft_generated': True, 'jianying_templates_bundled': True,
                      'system_only_path': True, 'media_environment_overrides_ignored': True,
                      'chinese_space_paths': True, 'authenticated_api_sse_range_media': True,
                      'real_decoded_clip_order_audio_and_srt': True, 'source_unchanged': True,
                      'history_preserved': True, 'cloud_fixture': 'saved_successful_current_format_responses',
                      'paid_cloud_requests': 0, 'production_frontend_served': True,
                      'native_frontend_interaction': 'not_automated', 'output': output}
        finally:
            session.close()

        # Missing bundled executable must fail despite hostile/global overrides.
        probe = Path(tools.ffprobe)
        hidden = probe.with_suffix('.missing')
        probe.rename(hidden)
        try:
            rejected = subprocess.run([str(executable), '--work-dir', str(work)], cwd=root, env=env,
                                      input='', capture_output=True, text=True, encoding='utf-8', timeout=20)
        finally:
            hidden.rename(probe)
        assert rejected.returncode == 1
        error = json.loads(rejected.stdout)
        assert error['event'] == 'error' and error['roles'] == {} and '随包媒体工具' in error['message'], error
        report['roles_exited'] = True
        report['missing_bundled_media_rejected'] = True
        (root / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == '__main__':
    main()
