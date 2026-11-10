import http.client
import json
import importlib.util
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ['uvicorn', 'fastapi'])


@unittest.skipUnless(AVAILABLE, 'FastAPI/uvicorn dependencies unavailable')
class EventAuthTests(unittest.TestCase):
    def test_live_stream_accepts_header_even_without_cookie_or_with_stale_cookie(self):
        import uvicorn
        from app.api import create_app
        from app.core.config import Settings
        with tempfile.TemporaryDirectory() as folder:
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            app = create_app(Settings(Path(folder), port=port), token='mock-event-session')
            server = uvicorn.Server(uvicorn.Config(app, log_level='critical', timeout_graceful_shutdown=1))
            thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            deadline = time.monotonic() + 5
            try:
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(server.started, 'Test server did not start')
                for cookie in (None, f'douyincut_session_{port}=stale-session'):
                    headers = {'X-Session-Token': 'mock-event-session', 'Accept': 'text/event-stream'}
                    if cookie:
                        headers['Cookie'] = cookie
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                    try:
                        connection.request('GET', '/api/v1/events', headers=headers)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertIn('text/event-stream', response.getheader('Content-Type'))
                        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                        self.assertEqual(response.readline(), b'event: jobs\n')
                        self.assertEqual(json.loads(response.readline().removeprefix(b'data: ')),
                                         {'total': 0, 'updated_at': 0, 'active': 0})
                        response.close()
                    finally:
                        connection.close()
                for headers in ({}, {'X-Session-Token': 'mock-event-session', 'Origin': 'https://untrusted.example'}):
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                    try:
                        connection.request('GET', '/api/v1/events', headers=headers)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 401)
                        response.read()
                    finally:
                        connection.close()
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                sock.close()
                self.assertFalse(thread.is_alive(), 'Test server did not stop')
