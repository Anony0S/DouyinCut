import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

import httpx

from app.ai.image_generation import fetch_product_image, request_image
from app.ai.providers import CloudConfig, ProviderError


PNG = b"\x89PNG\r\n\x1a\nfixture"


class ImageGenerationTests(unittest.TestCase):
    def test_cover_worker_retains_original_bytes(self):
        from app.core.config import Settings
        from app.worker import Worker

        with tempfile.TemporaryDirectory() as temp:
            worker = Worker(Settings(Path(temp)))
            worker.store = Mock()
            worker.store.cached.return_value = None
            plan = SimpleNamespace(id='plan', revision=1, clips=[SimpleNamespace(source_asset_id='asset')])
            context = Mock()
            job = {'id': 'job_test', 'kind': 'cover', 'payload': {
                'asset_id': 'asset', 'plan_id': 'plan', 'revision': 1,
                'config': {'image_base_url': 'https://images.example/v1', 'image_model': 'model'},
                'reference_path': 'reference.jpg', 'prompt': 'cover'}}
            def convert(source, target, settings, checkpoint):
                target.write_bytes(b'converted')
                return 300, 400
            with patch('app.worker.Plan.model_validate', return_value=plan), \
                 patch('app.core.credentials.read_secret', return_value='key'), \
                 patch('app.ai.providers.interruptible_network_call', return_value=(PNG, 'image/png')), \
                 patch('app.media.processing.prepare_cover_jpeg', side_effect=convert):
                result = worker.execute(job, context)
            self.assertEqual(Path(result['original_path']).read_bytes(), PNG)
            self.assertEqual(result['original_content_type'], 'image/png')
            self.assertEqual(Path(result['path']).read_bytes(), b'converted')
            self.assertFalse(Path(temp, 'covers', 'asset', 'job_test.source.png').exists())

    def test_cloud_config_validates_and_builds_images_edit_endpoint(self):
        config = CloudConfig(image_base_url="https://images.example/v1", image_model="cover-model")
        config.require_image_connection()
        self.assertEqual(config.image_edits_url, "https://images.example/v1/images/edits")
        with self.assertRaises(ValueError):
            CloudConfig(image_base_url="http://images.example/v1")
        with self.assertRaises(ValueError):
            CloudConfig(image_base_url="https://images.example/v1/images/edits")

    def test_request_image_sends_reference_as_multipart_and_normalizes_base64(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "reference.jpg"
            source.write_bytes(b"reference-image")
            def handle(request):
                body = request.content
                self.assertEqual(request.url.path, "/v1/images/edits")
                self.assertEqual(request.headers["authorization"], "Bearer image-fixture-key")
                self.assertIn(b'name="model"', body)
                self.assertIn(b'cover-model', body)
                self.assertIn(b'name="prompt"', body)
                self.assertIn(b'keep the garment', body)
                self.assertIn(b'name="size"\r\n\r\nauto\r\n', body)
                self.assertIn(b'name="image"; filename="reference.jpg"', body)
                self.assertIn(b"reference-image", body)
                return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
            client = httpx.Client(base_url="https://images.example", transport=httpx.MockTransport(handle))
            self.addCleanup(client.close)
            with patch("app.ai.image_generation.httpx.Client", return_value=client):
                result, content_type = request_image({
                    "image_base_url": "https://images.example/v1", "image_model": "cover-model",
                    "image_timeout_seconds": 30,
                }, "image-fixture-key", str(source), "keep the garment")
            self.assertEqual(result, PNG)
            self.assertEqual(content_type, "image/png")

    def test_request_image_404_reports_endpoint_and_base_url_guidance(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "reference.jpg"
            source.write_bytes(b"reference-image")
            client = httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(404, text="not found", request=request)))
            self.addCleanup(client.close)
            with patch("app.ai.image_generation.httpx.Client", return_value=client):
                with self.assertRaisesRegex(ProviderError, r"HTTP 404.*路径 /v1/images/edits.*Base URL"):
                    request_image({
                        "image_base_url": "https://images.example/v1", "image_model": "cover-model",
                        "image_timeout_seconds": 30,
                    }, "image-fixture-key", str(source), "make a cover")

    def test_product_image_download_requires_expected_https_host(self):
        for url in ("http://p3-aio.ecombdimg.com/image.jpg", "https://example.com/image.jpg",
                    "https://p3-aio.ecombdimg.com.evil/image.jpg"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_product_image(url)
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=b"jpeg", request=request,
        )))
        self.addCleanup(client.close)
        with patch("app.ai.image_generation.httpx.Client", return_value=client) as get:
            data, mime = fetch_product_image("https://p3-aio.ecombdimg.com/image.jpg?sig=fixture")
        self.assertEqual(data, b"jpeg")
        self.assertEqual(mime, "image/jpeg")
        self.assertFalse(get.call_args.kwargs["follow_redirects"])
        self.assertFalse(get.call_args.kwargs["trust_env"])
