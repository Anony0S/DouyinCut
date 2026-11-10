"""Verify frozen OSS probes against a local HTTPS fixture, without real credentials or cloud traffic."""

import argparse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import runpy
import ssl
import tempfile
import threading
from urllib.parse import unquote

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    support = runpy.run_path(str(Path(__file__).with_name('verify-windows-media.py')))
    FrozenSession = support['FrozenSession']
    WorkDirectoryRegistry = support['WorkDirectoryRegistry']
    with tempfile.TemporaryDirectory(prefix='DouyinCut OSS 中文 ') as temporary:
        root = Path(temporary)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'DouyinCut local test')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                       .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                       .add_extension(x509.SubjectAlternativeName([
                           x509.DNSName('oss-cn-test.aliyuncs.com'),
                           x509.DNSName('*.oss-cn-test.aliyuncs.com')]), critical=False)
                       .sign(private_key, hashes.SHA256()))
        cert_path, key_path = root / 'fixture-ca.pem', root / 'fixture-key.pem'
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(private_key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert_path, key_path)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            received = []
            stages = []
            deny = False

            def do_CONNECT(self):
                Handler.stages.append('connect')
                # Only our local fixture endpoint is allowed; never forward to the network.
                if self.path not in ('test-bucket.oss-cn-test.aliyuncs.com:443', 'oss-cn-test.aliyuncs.com:443'):
                    self.send_error(502)
                    return
                self.send_response(200)
                self.end_headers()
                self.connection = tls.wrap_socket(self.connection, server_side=True)
                Handler.stages.append('tls')
                self.rfile = self.connection.makefile('rb')
                self.wfile = self.connection.makefile('wb')
                self.handle_one_request()
                self.close_connection = True

            def do_PUT(self):
                Handler.stages.append('put')
                payload = self.rfile.read(int(self.headers['Content-Length']))
                Handler.received.append((unquote(self.path), payload))
                body = b'<Error><Code>AccessDenied</Code><Message>fixture</Message><RequestId>fixture</RequestId></Error>' if Handler.deny else b''
                self.send_response(403 if Handler.deny else 200)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'application/xml')
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(('PYTHON', 'VITE_', 'DOUYINCUT_', 'UV_', 'VIRTUAL_ENV'))
               and key.upper() not in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'REQUESTS_CA_BUNDLE'}}
        env.update(HTTPS_PROXY=f'http://127.0.0.1:{server.server_port}', NO_PROXY='127.0.0.1,localhost',
                   REQUESTS_CA_BUNDLE=str(cert_path))
        env['PATH'] = os.pathsep.join([str(Path(os.environ['SystemRoot']) / 'System32'), os.environ['SystemRoot']])
        work, locator = root / '工作目录', root / 'workspace.json'
        work.mkdir()
        WorkDirectoryRegistry(locator).select(work)
        session = None
        try:
            session = FrozenSession(args.runtime.resolve(), root, work, locator, env)
            # The API includes child startup/cleanup around its 30-second deadline.
            session.client.timeout = 40
            body = {'config': {'oss_endpoint': 'https://oss-cn-test.aliyuncs.com', 'oss_bucket': 'test-bucket'},
                    'access_key_id': 'fixture-id', 'access_key_secret': 'fixture-secret'}
            result = session.request('POST', '/settings/cloud/test-oss', json=body)
            assert result['ok'], (result, Handler.stages)
            assert len(Handler.received) == 1, Handler.received
            path, payload = Handler.received[0]
            assert '/douyincut/audio/test-' in path and path.endswith('.txt'), path
            assert payload == b'DouyinCut OSS test', payload
            Handler.deny = True
            result = session.request('POST', '/settings/cloud/test-oss', json=body)
            assert not result['ok'] and 'HTTP 403' in result['message'] and 'AccessDenied' in result['message'], result
            assert 'fixture-secret' not in str(result), result
            assert session.request('GET', '/jobs') == []
            print('Frozen OSS probes passed: real child process, HTTPS upload, access-denied diagnostics, no jobs or cloud traffic.')
        finally:
            if session is not None:
                session.close()
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
