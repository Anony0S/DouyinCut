import datetime
import ipaddress
import json
import os
import ssl
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core.config import Settings
from app.desktop_runtime import DesktopSession


class SlowModelHandler(BaseHTTPRequestHandler):
    """Model endpoint that accepts the connection and stalls, like an unresponsive provider."""
    requests = 0
    arrived = None
    release = None
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        type(self).requests += 1
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.arrived.set()
        self.release.wait(120)
        body = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *_args):
        pass


def write_test_certificate(folder: Path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc))
                   .not_valid_after(datetime.datetime(2035, 1, 1, tzinfo=datetime.timezone.utc))
                   .add_extension(x509.SubjectAlternativeName([
                       x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .sign(key, hashes.SHA256()))
    cert = folder / "cert.pem"
    key_path = folder / "key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert, key_path


class SettingsExitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        cert, key = write_test_certificate(self.root)
        SlowModelHandler.requests = 0
        SlowModelHandler.arrived = threading.Event()
        SlowModelHandler.release = threading.Event()
        self.addCleanup(SlowModelHandler.release.set)
        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowModelHandler)
        server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base_url = f"https://127.0.0.1:{server.server_port}/v1"
        # The API role runs in a spawned process, so trust must be set through the inherited environment.
        trusted = patch.dict(os.environ, {"SSL_CERT_FILE": str(cert)})
        trusted.start()
        self.addCleanup(trusted.stop)

        self.settings = Settings(self.root / "work")
        self.settings.prepare()
        frontend = self.root / "frontend"
        frontend.mkdir()
        (frontend / "index.html").write_text("<html>settings exit</html>", encoding="utf-8")
        self.frontend = frontend

    def start_session(self):
        session = DesktopSession(self.settings, self.frontend)
        self.addCleanup(session.close)
        return session, session.start()

    def test_desktop_exit_does_not_wait_for_a_blocked_model_test_and_restart_never_replays_it(self):
        session, info = self.start_session()
        headers = {"X-Session-Token": info.token}
        body = {"config": {"base_url": self.base_url, "planner_model": "draft-model",
                           "chat_timeout_seconds": 300}, "api_key": "draft-private-key"}
        # The request runs in the API role while the model stalls; the client is abandoned on exit.
        threading.Thread(target=lambda: httpx.post(info.url + "/api/v1/settings/cloud/test-model",
                                                   headers=headers, json=body, timeout=None,
                                                   trust_env=False), daemon=True).start()
        self.assertTrue(SlowModelHandler.arrived.wait(15), "model test never reached the provider")

        started = time.monotonic()
        session.close()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 10, "desktop exit waited for the stalled model test")
        self.assertEqual(SlowModelHandler.requests, 1)
        self.assertTrue(all(not role["alive"] for role in session.status().values()))

        restarted, _ = self.start_session()
        time.sleep(2)
        self.assertEqual(SlowModelHandler.requests, 1, "a one-off settings test was replayed after restart")


if __name__ == "__main__":
    unittest.main()
