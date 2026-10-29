"""A lock shared by every server process that uses the exchange folder.

All servers write the same request.json and read the same response.json, so
two of them (two Claude sessions, or Claude Desktop and Claude Code) must
never have a command in flight at the same time: the second request would
overwrite the first before Altium reads it, and the first server would then
take the second one's reply for a stale one and delete it (issue #38).

The lock is a byte-range lock on a file in the exchange folder. Windows
releases it when the owning process exits, so a crashed or killed server
never leaves it held.
"""

import asyncio
import msvcrt
import time
from pathlib import Path


class ExchangeLock:
    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def _try_lock(self) -> bool:
        f = open(self.path, "a+b")
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            f.close()
            return False
        self._file = f
        return True

    async def acquire(self, timeout: float, poll: float = 0.1) -> bool:
        """Wait up to `timeout` seconds for the lock; False if another server kept it."""
        deadline = time.monotonic() + timeout
        while not self._try_lock():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(poll)
        return True

    def release(self) -> None:
        f, self._file = self._file, None
        if f is None:
            return
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()
