import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.config import Settings
from app.core.storage import Store
from app.live_monitoring import LiveMonitorRequest, LiveMonitorSettings
from app.live_monitoring.service import LiveMonitorWorker


class SlowResolver:
    """Resolver that blocks for a long time, simulating network timeout."""
    calls = 0

    async def resolve(self, url: str):
        type(self).calls += 1
        # Simulate a network call that takes 30 seconds (typical HTTP timeout)
        await __import__("asyncio").sleep(30)
        return {"is_live": False, "stream_url": "", "anchor_name": "测试主播", "room_id": "blocked"}


class LiveMonitorExitTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = Settings(Path(temp.name))
        self.settings.prepare()
        self.store = Store(self.settings.database)
        SlowResolver.calls = 0

    def test_desktop_exit_does_not_wait_for_blocked_resolver_network_call(self):
        from app.live_monitoring import LiveStore
        live = LiveStore(self.store)
        monitor = live.create_monitor("https://live.douyin.com/blocked", "阻塞测试")
        live.update_settings(monitor["id"], LiveMonitorSettings(auto_record=False, notify_live=False))

        worker = LiveMonitorWorker(self.settings, resolver=SlowResolver(), poll_seconds=1)
        stop = threading.Event()

        # Start the worker in a background thread
        thread = threading.Thread(target=worker.run, args=(stop,), daemon=True)
        thread.start()
        time.sleep(0.5)  # Let the worker enter its main loop

        # The worker polls every 1 second. On the first run_once, it will call the resolver.
        time.sleep(1.5)  # Let run_once enter the blocked resolver call

        self.assertGreater(SlowResolver.calls, 0, "resolver was never called")

        # Signal exit and measure how long it actually takes
        started = time.time()
        stop.set()
        thread.join(timeout=12)
        elapsed = time.time() - started

        self.assertFalse(thread.is_alive(), "live monitor worker did not exit")
        self.assertLess(elapsed, 10, f"exit took {elapsed:.1f}s, should be under 10s (8s timeout + 1s poll)")


if __name__ == "__main__":
    unittest.main()
