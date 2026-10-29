"""Unit tests for exchange_lock (no Altium needed).

Run from server/:  python -m unittest tests.test_exchange_lock -v
"""
import asyncio
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SERVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, SERVER_DIR)

from exchange_lock import ExchangeLock  # noqa: E402

# Holds the lock until stdin closes, so the test decides when it ends
HOLDER = """
import asyncio, sys
sys.path.insert(0, sys.argv[2])
from pathlib import Path
from exchange_lock import ExchangeLock
lock = ExchangeLock(Path(sys.argv[1]))
assert asyncio.run(lock.acquire(5))
print("held", flush=True)
sys.stdin.read()
"""


class ExchangeLockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "bridge.lock"

    def tearDown(self):
        self.dir.cleanup()

    def test_second_holder_waits_until_release(self):
        a, b = ExchangeLock(self.path), ExchangeLock(self.path)
        self.assertTrue(asyncio.run(a.acquire(1)))
        self.assertFalse(asyncio.run(b.acquire(0.3)))
        a.release()
        self.assertTrue(asyncio.run(b.acquire(1)))
        b.release()

    def test_release_without_acquire_is_harmless(self):
        ExchangeLock(self.path).release()

    def _holder(self):
        p = subprocess.Popen([sys.executable, "-c", HOLDER, str(self.path), SERVER_DIR],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def test_other_process_blocks_until_it_releases(self):
        p = self._holder()
        lock = ExchangeLock(self.path)
        self.assertFalse(asyncio.run(lock.acquire(0.3)))
        p.stdin.close()
        p.wait(5)
        self.assertTrue(asyncio.run(lock.acquire(2)))
        lock.release()

    def test_killed_holder_does_not_keep_the_lock(self):
        # A server killed mid-command (client closed, crash) must not leave
        # every other server locked out
        p = self._holder()
        p.kill()
        p.wait(5)
        lock = ExchangeLock(self.path)
        start = time.monotonic()
        self.assertTrue(asyncio.run(lock.acquire(2)))
        self.assertLess(time.monotonic() - start, 1.5)
        lock.release()


if __name__ == "__main__":
    unittest.main()
