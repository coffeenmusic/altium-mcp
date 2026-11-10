"""Unit tests for foreground_guard (pure Python, no windows).

Run from server/:  python -m unittest tests.test_foreground_guard -v
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import foreground_guard as fg  # noqa: E402

TERMINAL, ALTIUM, BROWSER = 10, 20, 30          # process ids
H_TERMINAL, H_ALTIUM, H_BROWSER = 101, 201, 301  # window handles
PID = {H_TERMINAL: TERMINAL, H_ALTIUM: ALTIUM, H_BROWSER: BROWSER, 0: 0}


class DeciderTest(unittest.TestCase):
    def test_gives_back_when_altium_takes_the_foreground(self):
        d = fg.Decider(TERMINAL, {ALTIUM})
        self.assertEqual([d.step(p) for p in (TERMINAL, 0, ALTIUM, TERMINAL)],
                         [fg.WAIT, fg.WAIT, fg.GIVE_BACK, fg.WAIT])

    def test_leaves_alone_when_altium_was_in_front(self):
        d = fg.Decider(ALTIUM, {ALTIUM})
        self.assertFalse(d.active)
        self.assertEqual(d.step(ALTIUM), fg.STOP)

    def test_leaves_alone_when_altium_is_not_running(self):
        self.assertFalse(fg.Decider(TERMINAL, set()).active)

    def test_leaves_alone_without_a_foreground_window(self):
        self.assertFalse(fg.Decider(0, {ALTIUM}).active)

    def test_stops_when_the_user_moves_to_another_program(self):
        d = fg.Decider(TERMINAL, {ALTIUM})
        self.assertEqual(d.step(BROWSER), fg.STOP)

    def test_gives_back_a_limited_number_of_times(self):
        d = fg.Decider(TERMINAL, {ALTIUM}, max_give_backs=2)
        self.assertEqual([d.step(ALTIUM) for _ in range(3)], [fg.GIVE_BACK, fg.GIVE_BACK, fg.STOP])


class FakeDesktop:
    """A foreground window that changes on a schedule, and a give-back
    that puts the given window in front."""

    def __init__(self, schedule):
        self.schedule = list(schedule)   # (seconds after start, hwnd)
        self.t0 = time.monotonic()
        self.forced = None
        self.given = []
        self.lock = threading.Lock()

    def front(self):
        with self.lock:
            now = time.monotonic() - self.t0
            due = [h for t, h in self.schedule if t <= now]
            current = due[-1] if due else self.schedule[0][1]
            if self.forced is not None and self.forced[0] >= len(due):
                return self.forced[1]
            return current

    def give_back(self, hwnd):
        with self.lock:
            now = time.monotonic() - self.t0
            self.given.append(hwnd)
            self.forced = (len([t for t, _ in self.schedule if t <= now]), hwnd)

    def guard(self, altium_pids=(ALTIUM,), **kw):
        kw.setdefault("poll", 0.005)
        return fg.Guard(self.front, PID.get, lambda: altium_pids, self.give_back, **kw)


class GuardTest(unittest.TestCase):
    def test_altium_taking_the_foreground_is_undone(self):
        desk = FakeDesktop([(0, H_TERMINAL), (0.05, H_ALTIUM)])
        g = desk.guard(linger=0.1).start()
        g.join(2)
        self.assertEqual(desk.given, [H_TERMINAL])
        self.assertEqual(desk.front(), H_TERMINAL)
        self.assertFalse(g._thread.is_alive())

    def test_nothing_happens_when_altium_was_in_front(self):
        desk = FakeDesktop([(0, H_ALTIUM)])
        g = desk.guard().start()
        self.assertIsNone(g._thread)
        self.assertEqual(desk.given, [])

    def test_the_user_switching_programs_ends_the_watch(self):
        desk = FakeDesktop([(0, H_TERMINAL), (0.03, H_BROWSER), (0.06, H_ALTIUM)])
        g = desk.guard().start()
        g.join(2)
        self.assertEqual(desk.given, [])

    def test_altium_brought_up_on_purpose_is_left_in_front(self):
        desk = FakeDesktop([(0, H_TERMINAL), (0.1, H_ALTIUM)])
        g = desk.guard().start()
        time.sleep(0.03)
        fg.altium_wanted()
        g.join(2)
        self.assertEqual(desk.given, [])

    def test_ends_after_the_timeout(self):
        desk = FakeDesktop([(0, H_TERMINAL)])
        g = desk.guard(timeout=0.05).start()
        g.join(2)
        self.assertFalse(g._thread.is_alive())
        self.assertEqual(desk.given, [])

    def test_stop(self):
        desk = FakeDesktop([(0, H_TERMINAL)])
        g = desk.guard(timeout=30).start()
        g.stop()
        g.join(2)
        self.assertFalse(g._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
