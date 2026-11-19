"""Give the foreground back when Altium takes it while running our command.

Every command reaches Altium by starting X2.EXE with -RScriptingSystem:...
(or -REditScript:Stop). The running Altium receives it and brings its main
window to the front, about 2 s after the start. Measured on Altium 26.11:
every time, also when X2.EXE is started without cmd.exe and with
SW_SHOWNOACTIVATE, and LockSetForegroundWindow is refused to the server (a
background process). Whatever the user was typing elsewhere then goes to
Altium, where single letters are menu shortcuts.

It can't be prevented from outside, so a watcher started with each launch
gives the window that was in front the foreground back as soon as Altium has
taken it. It leaves things alone when
  * Altium was in front already (the user is working in it),
  * Altium is not running yet (it is being started and should come up),
  * the user moves to another program meanwhile,
  * a capture brings Altium up on purpose (altium_wanted()).

The decision is pure (Decider) so it can be tested without windows; Guard
runs it in a thread with the win32 calls passed in.
"""
import logging
import threading
import time
from typing import Callable, Iterable, Optional

logger = logging.getLogger("AltiumMCPServer")

GIVE_BACK, WAIT, STOP = "give_back", "wait", "stop"


class Decider:
    """What to do on each look at the foreground window's process."""

    def __init__(self, previous_pid: int, altium_pids: Iterable[int], max_give_backs: int = 3):
        self.previous_pid = previous_pid
        self.altium_pids = set(altium_pids)
        self.max_give_backs = max_give_backs
        self.given_back = 0
        self.active = (bool(previous_pid) and bool(self.altium_pids)
                       and previous_pid not in self.altium_pids)

    def step(self, front_pid: int) -> str:
        if not self.active:
            return STOP
        if not front_pid or front_pid == self.previous_pid:
            return WAIT    # no foreground for a moment, or nothing changed
        if front_pid in self.altium_pids:
            if self.given_back >= self.max_give_backs:
                return STOP
            self.given_back += 1
            return GIVE_BACK
        return STOP        # the user went to another program


_wanted_lock = threading.Lock()
_wanted_epoch = 0


def altium_wanted() -> None:
    """Altium is about to be brought up on purpose: running guards stop."""
    global _wanted_epoch
    with _wanted_lock:
        _wanted_epoch += 1


def _epoch() -> int:
    with _wanted_lock:
        return _wanted_epoch


class Guard:
    """Watch the foreground for one launch.

    Ends by itself: `linger` seconds after the last give-back (Altium may
    raise itself once more), after `timeout`, or on stop().
    """

    def __init__(self, front: Callable[[], int], pid_of: Callable[[int], int],
                 altium_pids: Callable[[], Iterable[int]], give_back: Callable[[int], None],
                 timeout: float = 30.0, linger: float = 1.0, poll: float = 0.02,
                 max_give_backs: int = 3):
        self._front, self._pid_of = front, pid_of
        self._altium_pids, self._give_back = altium_pids, give_back
        self.timeout, self.linger, self.poll = timeout, linger, poll
        self.max_give_backs = max_give_backs
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.previous = 0
        self.decider: Optional[Decider] = None
        self.given_back = 0

    def start(self) -> "Guard":
        try:
            self.previous = self._front() or 0
            self.decider = Decider(self._pid_of(self.previous) if self.previous else 0,
                                   self._altium_pids(), self.max_give_backs)
        except Exception as e:
            logger.debug(f"Foreground guard not started: {e}")
            return self
        if self.decider.active:
            self._thread = threading.Thread(target=self._run, name="foreground-guard", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        epoch = _epoch()
        end = time.monotonic() + self.timeout
        while not self._stop.is_set() and time.monotonic() < end and _epoch() == epoch:
            try:
                action = self.decider.step(self._pid_of(self._front() or 0))
            except Exception as e:
                logger.debug(f"Foreground guard: {e}")
                action = WAIT
            if action == STOP:
                break
            if action == GIVE_BACK:
                try:
                    self._give_back(self.previous)
                    self.given_back += 1
                    logger.info("Altium took the foreground for a command; gave it back")
                except Exception as e:
                    logger.debug(f"Foreground guard could not give the foreground back: {e}")
                end = min(end, time.monotonic() + self.linger)
            time.sleep(self.poll)
