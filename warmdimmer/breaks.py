"""
Eye-rest reminders, on the 20-20-20 pattern: every 20 minutes, look at
something far away for 20 seconds.  Both numbers are settings.

Two details decide whether a reminder like this is useful or merely annoying:

  It must not count time you were not there.  Windows reports how long the
  keyboard and mouse have been idle, and a long idle stretch counts as a break
  already taken, so stepping away for lunch does not earn you a reminder the
  moment you sit down.

  It must be interruptible.  A full-screen prompt that cannot be dismissed
  gets the whole app uninstalled, so every break can be skipped or postponed.
"""
import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


try:
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    _tick = kernel32.GetTickCount64
except AttributeError:
    kernel32.GetTickCount.restype = wt.DWORD
    _tick = kernel32.GetTickCount


def idle_seconds():
    """How long since the last key press or mouse move, in seconds."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    # GetLastInputInfo is a 32-bit tick count; mask so it lines up after the
    # ~49 day wrap instead of reporting a wildly negative idle time.
    return max(0.0, ((_tick() & 0xFFFFFFFF) - info.dwTime) & 0xFFFFFFFF) / 1000.0


# SHQueryUserNotificationState: the documented way to ask "is now a bad moment
# to put something on screen".  Anything other than ACCEPTS_NOTIFICATIONS means
# a game, a video, a presentation or a full-screen call is in front.
QUNS_ACCEPTS_NOTIFICATIONS = 5
MONITOR_DEFAULTTONEAREST = 2
# Windows itself is always full screen; so is the desktop.  Neither counts.
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"}


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def _foreground_covers_a_screen():
    """Fallback for apps that never set the notification state."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    if buf.value in _SHELL_CLASSES:
        return False
    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return (rect.left <= m.left and rect.top <= m.top
            and rect.right >= m.right and rect.bottom >= m.bottom)


def busy_on_screen():
    """
    True while something is running full screen: a video call, a game, a film,
    a presentation.  Covering that with a rest reminder is at best rude and, if
    a screen is being shared, embarrassing.
    """
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            if state.value and state.value != QUNS_ACCEPTS_NOTIFICATIONS:
                return True
    except Exception:
        pass
    try:
        return _foreground_covers_a_screen()
    except Exception:
        return False


IDLE = "idle"          # counting down to the next break
DUE = "due"            # break is running
PAUSED = "paused"      # feature off, or the user is away


class BreakTimer:
    """
    Pure timing logic, driven by tick() from the UI loop.  Owns no widgets, so
    it can be tested without a screen.
    """

    def __init__(self, cfg, on_start=None, on_end=None):
        self.cfg = cfg
        self.on_start = on_start
        self.on_end = on_end
        self.state = IDLE
        self.next_at = None       # monotonic time the break is due
        self.ends_at = None       # monotonic time a running break finishes
        self.skipped = 0
        self.taken = 0
        self.deferred = False   # it was time, but the screen was busy
        self._away = False
        self._manual = False    # a break the user asked for, not one on schedule
        self.reset()

    # ----------------------------------------------------------- settings
    @property
    def interval(self):
        return max(60, int(self.cfg.get("break_interval_min", 20)) * 60)

    @property
    def duration(self):
        return max(5, int(self.cfg.get("break_duration_s", 20)))

    @property
    def away_after(self):
        return max(30, int(self.cfg.get("break_idle_reset_s", 120)))

    @property
    def enabled(self):
        return bool(self.cfg.get("break_enabled", False))

    @property
    def skip_when_busy(self):
        return bool(self.cfg.get("break_skip_fullscreen", True))

    # -------------------------------------------------------------- state
    def reset(self, now=None):
        now = time.monotonic() if now is None else now
        self.next_at = now + self.interval
        self.ends_at = None
        self.state = IDLE

    def seconds_left(self, now=None):
        now = time.monotonic() if now is None else now
        if self.state == DUE and self.ends_at is not None:
            return max(0, int(round(self.ends_at - now)))
        if self.next_at is None:
            return 0
        return max(0, int(round(self.next_at - now)))

    def postpone(self, minutes=5, now=None):
        now = time.monotonic() if now is None else now
        self.end_break(now)
        self.next_at = now + max(60, int(minutes) * 60)

    def skip(self, now=None):
        self.skipped += 1
        self.end_break(now)

    def start_break(self, now=None, manual=False):
        now = time.monotonic() if now is None else now
        self.state = DUE
        self.deferred = False
        self._manual = manual
        self.ends_at = now + self.duration
        if self.on_start:
            self.on_start()

    def end_break(self, now=None):
        now = time.monotonic() if now is None else now
        was_running = self.state == DUE
        self.state = IDLE
        self._manual = False
        self.ends_at = None
        self.next_at = now + self.interval
        if was_running and self.on_end:
            self.on_end()

    # --------------------------------------------------------------- tick
    def tick(self, now=None, idle=None, busy=None):
        """
        Advance the clock.  Returns the state so the caller can redraw.
        `idle` and `busy` are injectable so the behaviour can be tested
        without waiting and without a real full-screen app.
        """
        now = time.monotonic() if now is None else now

        # A break running right now always finishes, even one the user asked
        # for after switching the reminder off.
        if self.state == DUE:
            if self.ends_at is not None and now >= self.ends_at:
                self.taken += 1
                self.end_break(now)
            return self.state

        if not self.enabled:
            self.state = PAUSED
            return self.state

        if self.state == PAUSED:
            self.reset(now)

        idle = idle_seconds() if idle is None else idle

        if idle >= self.away_after:
            # Being away from the machine is the break; keep the clock parked
            # so returning does not immediately trigger a reminder.  Never pull
            # it closer than it already was, or walking away right after asking
            # to postpone would shorten the delay instead of extending it.
            self._away = True
            self.deferred = False
            parked = now + self.interval
            self.next_at = parked if self.next_at is None else max(self.next_at, parked)
            return self.state
        self._away = False

        if self.next_at is not None and now >= self.next_at:
            if self.skip_when_busy and (busy_on_screen() if busy is None else busy):
                # Hold it, do not drop it: the reminder appears the moment the
                # call or the film is over.
                self.deferred = True
                return self.state
            self.start_break(now)
        return self.state


def format_left(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds}s"
