import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.core.config import Settings


class JobPaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Settings(Path(self.temp.name)), token="paging-test")
        self.client = TestClient(self.app)
        self.headers = {"X-Session-Token": "paging-test"}
        self.store = self.app.state.store
        # Tied timestamps exercise the ID half of the cursor.
        with self.store.connection() as conn:
            for number in range(45):
                conn.execute("INSERT INTO jobs(id,kind,payload,state,created,updated) VALUES(?,?,?,?,?,?)",
                             (f"job-{number:03}", "preview", "{}", "succeeded", 100, 100))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def page(self, **params):
        response = self.client.get("/api/v1/jobs", params={"limit": 21, **params}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_cursor_keeps_history_stable_when_head_inserted_and_cursor_deleted(self):
        first = self.page()
        self.assertEqual([job["id"] for job in first[:20]], [f"job-{i:03}" for i in range(44, 24, -1)])
        cursor = first[19]
        with self.store.connection() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (cursor["id"],))
            conn.execute("INSERT INTO jobs(id,kind,payload,state,created,updated) VALUES(?,?,?,?,?,?)",
                         ("new-job", "preview", "{}", "queued", 101, 101))
        second = self.page(before_created=cursor["created"], before_id=cursor["id"])
        self.assertEqual([job["id"] for job in second[:20]], [f"job-{i:03}" for i in range(24, 4, -1)])
        cursor = second[19]
        last = self.page(before_created=cursor["created"], before_id=cursor["id"])
        self.assertEqual([job["id"] for job in last], [f"job-{i:03}" for i in range(4, -1, -1)])
        self.assertNotIn("new-job", [job["id"] for job in second + last])

    def test_cursor_validation_and_authentication(self):
        self.assertEqual(self.client.get("/api/v1/jobs?limit=21").status_code, 401)
        for params in [{"before_created": 100}, {"before_id": "job-001"},
                       {"before_created": 100, "before_id": "job-001", "offset": 1},
                       {"before_created": "nan", "before_id": "job-001"}]:
            response = self.client.get("/api/v1/jobs", params=params, headers=self.headers)
            self.assertEqual(response.status_code, 422, response.text)
