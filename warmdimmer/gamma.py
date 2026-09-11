"""
Per-monitor gamma-ramp control through GDI (SetDeviceGammaRamp).

This is the same hardware path used by f.lux / CareUEyes: the video card's
lookup table is rewritten so the change applies to everything on screen
(games, video, fullscreen apps) with zero overlay cost.
"""
import ctypes
import ctypes.wintypes as wt
import math
import threading
from dataclasses import dataclass

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32

# explicit signatures: essential on 64-bit so handles are not truncated
gdi32.CreateDCW.restype = ctypes.c_void_p
gdi32.CreateDCW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.GetDeviceGammaRamp.restype = wt.BOOL
gdi32.GetDeviceGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SetDeviceGammaRamp.restype = wt.BOOL
gdi32.SetDeviceGammaRamp.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

RAMP = ctypes.c_ushort * 768  # R[256] G[256] B[256]

MONITORINFOF_PRIMARY = 1


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", wt.WCHAR * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wt.RECT), wt.LPARAM
)
user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, MONITORENUMPROC, wt.LPARAM]
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


@dataclass(frozen=True)
class Monitor:
    device: str            # e.g. \\.\DISPLAY1
    rect: tuple            # (x, y, w, h) in physical pixels
    primary: bool

    @property
    def label(self):
        n = self.device.rsplit("DISPLAY", 1)[-1]
        return f"Display {n}" + (" (primary)" if self.primary else "")


def enumerate_monitors():
    """Return active monitors with their device names and physical rects."""
    found = []

    def _cb(hmon, hdc, prect, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(mi)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            found.append(Monitor(
                device=mi.szDevice,
                rect=(r.left, r.top, r.right - r.left, r.bottom - r.top),
                primary=bool(mi.dwFlags & MONITORINFOF_PRIMARY),
            ))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    found.sort(key=lambda m: (not m.primary, m.device))
    return found


# ---------------------------------------------------------------- colour maths
def _helland(kelvin):
    """Tanner Helland blackbody approximation, returns 0..255 floats."""
    t = kelvin / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307

    def clamp(v):
        return max(0.0, min(255.0, v))

    return clamp(r), clamp(g), clamp(b)


_WHITE = _helland(6500)


def temperature_multipliers(kelvin):
    """RGB channel multipliers, normalised so 6500K == (1, 1, 1)."""
    r, g, b = _helland(kelvin)
    return (r / _WHITE[0], g / _WHITE[1], b / _WHITE[2])


def temperature_hex(kelvin):
    r, g, b = temperature_multipliers(kelvin)
    return "#%02x%02x%02x" % tuple(int(min(1.0, c) * 255) for c in (r, g, b))


def build_ramp(kelvin, brightness):
    """Linear ramp scaled by colour temperature and brightness (0..1)."""
    mult = temperature_multipliers(kelvin)
    ramp = RAMP()
    for c in range(3):
        scale = mult[c] * brightness * 65535.0 / 255.0
        base = c * 256
        for i in range(256):
            ramp[base + i] = int(min(65535.0, max(0.0, i * scale + 0.5)))
    return ramp


def identity_ramp():
    return build_ramp(6500, 1.0)


def blend_ramps(a, b, f):
    """a*(1-f) + b*f"""
    out = RAMP()
    for i in range(768):
        out[i] = int(a[i] + (b[i] - a[i]) * f + 0.5)
    return out


# ------------------------------------------------------------------- devices
def _with_dc(device, fn):
    hdc = gdi32.CreateDCW(device, device, None, None)
    if not hdc:
        return None
    try:
        return fn(hdc)
    finally:
        gdi32.DeleteDC(hdc)


def get_ramp(device):
    ramp = RAMP()
    ok = _with_dc(device, lambda hdc: gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(ramp)))
    return ramp if ok else None


def set_ramp(device, ramp):
    return bool(_with_dc(device, lambda hdc: gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))))


def ramp_is_identity(ramp, tolerance=600):
    ident = identity_ramp()
    return all(abs(ramp[i] - ident[i]) <= tolerance for i in range(768))


class GammaController:
    """
    Applies (temperature, brightness) to a set of monitors, remembers the
    original ramps, and reports how much of the request the driver accepted.

    Windows (without the GdiIcmGammaRange registry unlock) refuses ramps that
    deviate too far from identity.  When that happens we binary-search the
    closest accepted ramp and return the achieved factor, so the caller can
    cover the remaining darkness with an overlay.
    """

    def __init__(self):
        self.monitors = enumerate_monitors()
        self.originals = {}
        # The user's explicit "leave this screen alone" choices.  Kept as the
        # source of truth rather than a set of currently-enabled devices, so a
        # monitor that sleeps and wakes does not quietly switch itself back on.
        self.disabled_devices = set()
        self._identity = identity_ramp()
        self.limited = False  # True when the driver clamped our last request
        self._last_set = {}     # device -> (kelvin, brightness) last pushed
        self._last_factor = {}  # device -> how much of it the driver accepted
        self._clamp_factor = {}  # device -> last blend a clamped driver allowed
        self.snapshot_originals()

    @property
    def enabled_devices(self):
        return {m.device for m in self.monitors if m.device not in self.disabled_devices}

    def set_enabled(self, device, enabled):
        if enabled:
            self.disabled_devices.discard(device)
        else:
            self.disabled_devices.add(device)

    # -- lifecycle -------------------------------------------------------
    def snapshot_originals(self):
        for m in self.monitors:
            if m.device in self.originals:
                continue
            current = get_ramp(m.device)
            if current is None or not ramp_is_identity(current):
                # Either unreadable, or a leftover from a previous crash:
                # identity is the safest restore target.
                current = identity_ramp()
            self.originals[m.device] = current

    def refresh_monitors(self):
        """
        Re-enumerate.  Returns True when anything changed, including a
        resolution or arrangement change: the overlay is sized from these
        rectangles, so comparing device names alone would leave it covering
        the wrong part of the screen after a mode switch.
        """
        new = enumerate_monitors()
        before = [(m.device, m.rect) for m in self.monitors]
        after = [(m.device, m.rect) for m in new]
        if before == after:
            return False
        gone = {m.device for m in self.monitors} - {m.device for m in new}
        self.monitors = new
        for device in gone:
            self._last_set.pop(device, None)
            self._last_factor.pop(device, None)
            self._clamp_factor.pop(device, None)
        self.snapshot_originals()
        return True

    def restore(self):
        for m in self.monitors:
            set_ramp(m.device, self.originals.get(m.device, self._identity))
        self._last_set.clear()
        self._last_factor.clear()

    # -- apply -------------------------------------------------------------
    def apply(self, kelvin, brightness, force=False):
        """
        Push (kelvin, brightness) to every enabled monitor.
        Returns the achieved factor in 0..1 (1 == fully applied).

        Each SetDeviceGammaRamp costs ~17 ms of driver time, so a device whose
        ramp is already what we want is skipped unless `force` is set (the
        periodic re-assert uses force to win back ramps other apps stole).
        """
        neutral = abs(kelvin - 6500) < 1 and brightness > 0.999
        target = None
        worst = 1.0
        limited = False
        for m in self.monitors:
            dev = m.device
            want = None if (dev in self.disabled_devices or neutral) else (kelvin, brightness)
            if not force and self._last_set.get(dev, "?") == want:
                # Nothing to push, but this device still has a say in the
                # result: reporting 1.0 here would drop the overlay that is
                # covering a clamped driver.
                worst = min(worst, self._last_factor.get(dev, 1.0))
                limited = limited or self._last_factor.get(dev, 1.0) < 1.0
                continue
            self._last_set[dev] = want
            if want is None:
                # Off, or a screen the user excluded: put back the ramp this
                # machine actually had, which on a calibrated monitor is its
                # colour profile and not a synthetic straight line.
                set_ramp(dev, self.originals.get(dev, self._identity))
                self._last_factor[dev] = 1.0
                continue
            if target is None:
                target = build_ramp(kelvin, brightness)
            if set_ramp(dev, target):
                self._last_factor[dev] = 1.0
                continue

            limited = True
            best = self._accept_clamped(dev, target)
            self._last_factor[dev] = best
            worst = min(worst, best)
        self.limited = limited
        return worst

    def _accept_clamped(self, device, target):
        """
        Find the strongest blend toward `target` this driver will take.

        Windows refuses ramps too far from identity unless GdiIcmGammaRange is
        unlocked.  The blend it accepts barely moves between calls, so the last
        one is tried first: that turns the periodic re-assert from eight
        SetDeviceGammaRamp calls (~150 ms) into one.
        """
        cached = self._clamp_factor.get(device)
        if cached is not None and set_ramp(device, blend_ramps(self._identity, target, cached)):
            return cached
        lo, hi, best = 0.0, 1.0, 0.0
        for _ in range(7):
            mid = (lo + hi) / 2
            if set_ramp(device, blend_ramps(self._identity, target, mid)):
                best, lo = mid, mid
            else:
                hi = mid
        set_ramp(device, blend_ramps(self._identity, target, best))
        self._clamp_factor[device] = best
        return best


class GammaWorker:
    """
    Runs GammaController on its own thread so the ~17 ms driver call never
    blocks the UI.  Requests coalesce: while one ramp is uploading, further
    requests overwrite each other, so the driver always gets the newest value
    and a fast slider drag stays smooth instead of queueing up stale frames.
    """

    def __init__(self, controller, on_result=None):
        self.ctl = controller
        self.on_result = on_result      # (target, factor, limited), worker thread
        self._lock = threading.Lock()   # guards all controller access
        self._slot_lock = threading.Lock()
        self._wake = threading.Event()
        self._target = None
        self._force = False
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="gamma", daemon=True)
        self._thread.start()

    def request(self, kelvin, brightness, force=False):
        """Non-blocking: queue (and coalesce) a ramp update."""
        with self._slot_lock:
            self._target = (kelvin, brightness)
            self._force = self._force or force
        self._wake.set()

    def call(self, fn):
        """Run fn(controller) synchronously, serialised against the worker."""
        with self._lock:
            return fn(self.ctl)

    def restore(self):
        """Drop any pending request, then put the original ramps back."""
        with self._slot_lock:
            self._target, self._force = None, False
        with self._lock:
            self.ctl.restore()

    def _run(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._stop:
                return
            with self._slot_lock:
                target, force = self._target, self._force
                self._target, self._force = None, False
            if target is None:
                continue
            try:
                with self._lock:
                    factor = self.ctl.apply(target[0], target[1], force=force)
                    limited = self.ctl.limited
            except Exception:
                continue
            if self.on_result:
                try:
                    self.on_result(target, factor, limited)
                except Exception:
                    pass

    def stop(self):
        self._stop = True
        self._wake.set()
        self._thread.join(2.0)


# --------------------------------------------------------------- registry
def gamma_range_unlocked():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM") as k:
            val, _ = winreg.QueryValueEx(k, "GdiIcmGammaRange")
            return int(val) == 256
    except OSError:
        return False


def request_gamma_unlock():
    """Elevate and write GdiIcmGammaRange=256 (takes effect after sign-out/restart)."""
    cmd = (r'add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM" '
           r'/v GdiIcmGammaRange /t REG_DWORD /d 256 /f')
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "reg.exe", cmd, None, 0)
    return rc > 32
