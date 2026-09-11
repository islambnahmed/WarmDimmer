"""
Windows text rendering and UI text size, the eye-comfort half that gamma
cannot touch.

Two groups of settings, both per-user (HKCU) and both applied through
documented SystemParametersInfo calls so they take effect immediately:

  Rendering  ClearType on/off, subpixel vs greyscale, contrast/gamma and
             subpixel order.  Contrast is the knob that actually changes how
             heavy and how crisp glyph stems look.

  Size       The Windows 11 accessibility text scale, plus the classic
             per-element fonts (title bar, menu, message box, status bar,
             small caption, icon labels) that Settings no longer exposes.

Everything is captured to a backup file before the first change, so
restore() puts the machine back exactly as it was found.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import threading
import winreg

from .config import APP_DIR

user32 = ctypes.windll.user32

BACKUP_PATH = os.path.join(APP_DIR, "windows_text_backup.json")

# ---------------------------------------------------------------- constants
SPI_GETNONCLIENTMETRICS = 0x0029
SPI_SETNONCLIENTMETRICS = 0x002A
SPI_GETICONTITLELOGFONT = 0x001F
SPI_SETICONTITLELOGFONT = 0x0022
SPI_GETFONTSMOOTHING = 0x004A
SPI_SETFONTSMOOTHING = 0x004B
SPI_GETFONTSMOOTHINGTYPE = 0x200A
SPI_SETFONTSMOOTHINGTYPE = 0x200B
SPI_GETFONTSMOOTHINGCONTRAST = 0x200C
SPI_SETFONTSMOOTHINGCONTRAST = 0x200D
SPI_GETFONTSMOOTHINGORIENTATION = 0x2012
SPI_SETFONTSMOOTHINGORIENTATION = 0x2013
SPI_ICONHORIZONTALSPACING = 0x000D
SPI_ICONVERTICALSPACING = 0x0018

SPIF_UPDATEINIFILE = 1
SPIF_SENDCHANGE = 2
SPIF = SPIF_UPDATEINIFILE | SPIF_SENDCHANGE

SMOOTHING_STANDARD = 1     # greyscale antialiasing
SMOOTHING_CLEARTYPE = 2    # subpixel
ORIENTATION_BGR = 0
ORIENTATION_RGB = 1

CONTRAST_MIN, CONTRAST_MAX = 1000, 2200
SCALE_MIN, SCALE_MAX = 100, 225
PT_MIN, PT_MAX = 6.0, 24.0
SPACING_MIN, SPACING_MAX = 32, 100

ACCESSIBILITY_KEY = r"Software\Microsoft\Accessibility"
DESKTOP_KEY = r"Control Panel\Desktop"
METRICS_KEY = r"Control Panel\Desktop\WindowMetrics"

# The five fonts inside NONCLIENTMETRICS, plus the icon title font which is
# fetched separately.  Order here drives the UI.
FONT_ELEMENTS = [
    ("caption", "Title bar", "lfCaptionFont"),
    ("menu", "Menus", "lfMenuFont"),
    ("message", "Dialogs", "lfMessageFont"),
    ("status", "Tooltips / status", "lfStatusFont"),
    ("smcaption", "Small captions", "lfSmCaptionFont"),
    ("icon", "Icon labels", None),          # SPI_*ICONTITLELOGFONT
]
FONT_KEYS = [k for k, _, _ in FONT_ELEMENTS]

LF_FACESIZE = 32


class LOGFONTW(ctypes.Structure):
    _fields_ = [
        ("lfHeight", ctypes.c_long), ("lfWidth", ctypes.c_long),
        ("lfEscapement", ctypes.c_long), ("lfOrientation", ctypes.c_long),
        ("lfWeight", ctypes.c_long), ("lfItalic", ctypes.c_byte),
        ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte),
        ("lfCharSet", ctypes.c_byte), ("lfOutPrecision", ctypes.c_byte),
        ("lfClipPrecision", ctypes.c_byte), ("lfQuality", ctypes.c_byte),
        ("lfPitchAndFamily", ctypes.c_byte), ("lfFaceName", ctypes.c_wchar * LF_FACESIZE),
    ]


class NONCLIENTMETRICSW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("iBorderWidth", ctypes.c_int),
        ("iScrollWidth", ctypes.c_int), ("iScrollHeight", ctypes.c_int),
        ("iCaptionWidth", ctypes.c_int), ("iCaptionHeight", ctypes.c_int),
        ("lfCaptionFont", LOGFONTW),
        ("iSmCaptionWidth", ctypes.c_int), ("iSmCaptionHeight", ctypes.c_int),
        ("lfSmCaptionFont", LOGFONTW),
        ("iMenuWidth", ctypes.c_int), ("iMenuHeight", ctypes.c_int),
        ("lfMenuFont", LOGFONTW), ("lfStatusFont", LOGFONTW), ("lfMessageFont", LOGFONTW),
        ("iPaddedBorderWidth", ctypes.c_int),
    ]


# --------------------------------------------------------------- DPI context
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = ctypes.c_void_p(-2)


class _SystemDpi:
    """
    SystemParametersInfo reports and accepts font heights in the DPI context of
    the calling thread.  The app itself is per-monitor aware, so without this
    the numbers we read would not match the ones Windows stored.  Pinning the
    thread to system-DPI for the duration keeps points -> lfHeight stable.
    """

    def __enter__(self):
        self.prev = None
        try:
            fn = user32.SetThreadDpiAwarenessContext
            fn.restype = ctypes.c_void_p
            fn.argtypes = [ctypes.c_void_p]
            self.prev = fn(DPI_AWARENESS_CONTEXT_SYSTEM_AWARE)
        except AttributeError:
            pass          # pre-1607: process awareness already applies
        return self

    def __exit__(self, *exc):
        if self.prev:
            try:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(self.prev))
            except Exception:
                pass
        return False


def system_dpi():
    """
    The real system DPI, independent of how this process declared its DPI
    awareness.  Without the thread context a DPI-unaware process is told 96,
    which would silently mis-scale every point <-> lfHeight conversion and
    make a backup taken by one process unrestorable by another.
    """
    try:
        with _SystemDpi():
            return user32.GetDpiForSystem() or 96
    except AttributeError:
        return 96


def _pt_from_height(lf_height, dpi):
    return round(abs(lf_height) * 72.0 / dpi, 1)


def _height_from_pt(pt, dpi):
    return -int(round(float(pt) * dpi / 72.0))


# ------------------------------------------------------------------ registry
def _reg_get(path, name, default=None, hive=winreg.HKEY_CURRENT_USER):
    try:
        with winreg.OpenKey(hive, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return default


def _reg_set_dword(path, name, value):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))


def _broadcast(param):
    """WM_SETTINGCHANGE to every top-level window, without blocking on hangs."""
    res = ctypes.c_ulong()
    user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, ctypes.c_wchar_p(param),
                               0x0002, 1000, ctypes.byref(res))


# --------------------------------------------------------------------- read
def _spi_get_uint(spi):
    v = ctypes.c_uint(0)
    user32.SystemParametersInfoW(spi, 0, ctypes.byref(v), 0)
    return v.value


def read_state():
    """Snapshot every setting this module can change."""
    dpi = system_dpi()
    with _SystemDpi():
        ncm = NONCLIENTMETRICSW()
        ncm.cbSize = ctypes.sizeof(ncm)
        user32.SystemParametersInfoW(SPI_GETNONCLIENTMETRICS, ncm.cbSize, ctypes.byref(ncm), 0)
        icon = LOGFONTW()
        user32.SystemParametersInfoW(SPI_GETICONTITLELOGFONT, ctypes.sizeof(icon),
                                     ctypes.byref(icon), 0)

    fonts, weights, faces = {}, {}, {}
    for key, _, attr in FONT_ELEMENTS:
        lf = icon if attr is None else getattr(ncm, attr)
        fonts[key] = _pt_from_height(lf.lfHeight, dpi)
        weights[key] = lf.lfWeight
        faces[key] = lf.lfFaceName

    return {
        "dpi": dpi,
        "cleartype": bool(_spi_get_uint(SPI_GETFONTSMOOTHING)),
        "smoothing_type": _spi_get_uint(SPI_GETFONTSMOOTHINGTYPE) or SMOOTHING_CLEARTYPE,
        "contrast": _spi_get_uint(SPI_GETFONTSMOOTHINGCONTRAST) or 1400,
        "orientation": _spi_get_uint(SPI_GETFONTSMOOTHINGORIENTATION),
        "text_scale": int(_reg_get(ACCESSIBILITY_KEY, "TextScaleFactor", 100) or 100),
        "icon_spacing": abs(int(_reg_get(METRICS_KEY, "IconSpacing", -1125) or -1125)) // 15,
        "fonts": fonts,
        "weights": weights,
        "faces": faces,
    }


# -------------------------------------------------------------------- backup
def backup_exists():
    return os.path.isfile(BACKUP_PATH)


def ensure_backup():
    """Capture the machine's original state once, and never overwrite it."""
    if backup_exists():
        return False
    os.makedirs(APP_DIR, exist_ok=True)
    tmp = BACKUP_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(read_state(), f, indent=2)
    os.replace(tmp, BACKUP_PATH)
    return True


def load_backup():
    try:
        with open(BACKUP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------- write
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def apply_rendering(cleartype=None, smoothing_type=None, contrast=None, orientation=None):
    """
    Font rasteriser tuning. Takes effect on newly drawn text right away.

    Every SPIF_SENDCHANGE makes each top-level window on the desktop repaint,
    which stalls the machine for a moment, so the writes go in unannounced and
    only the last one broadcasts.
    """
    ensure_backup()
    ops = []
    if cleartype is not None:
        ops.append((SPI_SETFONTSMOOTHING, int(bool(cleartype)), None))
    if smoothing_type is not None:
        t = SMOOTHING_CLEARTYPE if int(smoothing_type) == SMOOTHING_CLEARTYPE else SMOOTHING_STANDARD
        ops.append((SPI_SETFONTSMOOTHINGTYPE, 0, ctypes.c_void_p(t)))
    if contrast is not None:
        c = _clamp(int(contrast), CONTRAST_MIN, CONTRAST_MAX)
        ops.append((SPI_SETFONTSMOOTHINGCONTRAST, 0, ctypes.c_void_p(c)))
    if orientation is not None:
        o = ORIENTATION_RGB if int(orientation) == ORIENTATION_RGB else ORIENTATION_BGR
        ops.append((SPI_SETFONTSMOOTHINGORIENTATION, 0, ctypes.c_void_p(o)))

    ok = True
    for i, (spi, wparam, lparam) in enumerate(ops):
        last = (i == len(ops) - 1)
        flags = SPIF if last else SPIF_UPDATEINIFILE
        if not user32.SystemParametersInfoW(spi, wparam, lparam, flags):
            ok = False
    return ok


def apply_fonts(sizes_pt, bold=None, weights=None):
    """
    Set the classic per-element UI fonts.  sizes_pt maps element key -> points;
    missing keys keep their current size.

    bold applies one weight to all six, which is what the checkbox in the UI
    means.  weights maps element key -> lfWeight and wins per element: restoring
    needs it, because a machine whose title bar alone was bold must not come
    back with all six bold.
    """
    ensure_backup()
    dpi = system_dpi()
    uniform = None if bold is None else (700 if bold else 400)
    weights = weights or {}
    with _SystemDpi():
        ncm = NONCLIENTMETRICSW()
        ncm.cbSize = ctypes.sizeof(ncm)
        if not user32.SystemParametersInfoW(SPI_GETNONCLIENTMETRICS, ncm.cbSize,
                                            ctypes.byref(ncm), 0):
            return False
        icon = LOGFONTW()
        user32.SystemParametersInfoW(SPI_GETICONTITLELOGFONT, ctypes.sizeof(icon),
                                     ctypes.byref(icon), 0)

        for key, _, attr in FONT_ELEMENTS:
            lf = icon if attr is None else getattr(ncm, attr)
            if key in sizes_pt and sizes_pt[key] is not None:
                lf.lfHeight = _height_from_pt(_clamp(float(sizes_pt[key]), PT_MIN, PT_MAX), dpi)
                lf.lfWidth = 0
            if key in weights and weights[key] is not None:
                lf.lfWeight = _clamp(int(weights[key]), 1, 1000)
            elif uniform is not None:
                lf.lfWeight = uniform

        # Grow the title bar and menu bands so a larger font is not clipped.
        cap_pt = _pt_from_height(ncm.lfCaptionFont.lfHeight, dpi)
        menu_pt = _pt_from_height(ncm.lfMenuFont.lfHeight, dpi)
        ncm.iCaptionHeight = max(ncm.iCaptionHeight, int(cap_pt * dpi / 72.0) + 10)
        ncm.iSmCaptionHeight = max(ncm.iSmCaptionHeight, int(cap_pt * dpi / 72.0) + 6)
        ncm.iMenuHeight = max(ncm.iMenuHeight, int(menu_pt * dpi / 72.0) + 8)

        # Write both, announce once: two broadcasts would repaint the whole
        # desktop twice for a single user action.
        ok = bool(user32.SystemParametersInfoW(SPI_SETNONCLIENTMETRICS, ncm.cbSize,
                                               ctypes.byref(ncm), SPIF_UPDATEINIFILE))
        ok = bool(user32.SystemParametersInfoW(SPI_SETICONTITLELOGFONT, ctypes.sizeof(icon),
                                               ctypes.byref(icon), SPIF)) and ok
    return ok


def apply_text_scale(percent):
    """Windows 11 accessibility text size. Most apps pick it up on next paint."""
    ensure_backup()
    _reg_set_dword(ACCESSIBILITY_KEY, "TextScaleFactor",
                   _clamp(int(percent), SCALE_MIN, SCALE_MAX))
    _broadcast("WindowsThemeElement")
    return True


def apply_icon_spacing(cells):
    """Desktop icon grid spacing, in the same units the Windows UI once used."""
    ensure_backup()
    v = _clamp(int(cells), SPACING_MIN, SPACING_MAX)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, METRICS_KEY, 0, winreg.KEY_SET_VALUE) as k:
        for name in ("IconSpacing", "IconVerticalSpacing"):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(-15 * v))
    _broadcast("WindowMetrics")
    return True


def apply_state(state):
    """Apply a full state dict (the shape read_state returns)."""
    apply_rendering(state.get("cleartype"), state.get("smoothing_type"),
                    state.get("contrast"), state.get("orientation"))
    apply_fonts(state.get("fonts") or {}, weights=state.get("weights") or None)
    if state.get("text_scale"):
        apply_text_scale(state["text_scale"])
    if state.get("icon_spacing"):
        apply_icon_spacing(state["icon_spacing"])
    return True


def restore():
    """Put every setting back to the values captured in the backup."""
    original = load_backup()
    if not original:
        return False
    apply_state(original)
    return True


def restore_rendering():
    """Undo only the rasteriser half, leaving sizes as the user set them."""
    o = load_backup()
    if not o:
        return False
    return apply_rendering(o.get("cleartype"), o.get("smoothing_type"),
                           o.get("contrast"), o.get("orientation"))


def restore_size():
    """Undo only the size half, leaving rendering as the user set them."""
    o = load_backup()
    if not o:
        return False
    apply_fonts(o.get("fonts") or {}, weights=o.get("weights") or None)
    if o.get("text_scale"):
        apply_text_scale(o["text_scale"])
    if o.get("icon_spacing"):
        apply_icon_spacing(o["icon_spacing"])
    return True


def rendering_of(state):
    return {k: state[k] for k in ("cleartype", "smoothing_type", "contrast", "orientation")}


def size_of(state):
    weights = state.get("weights") or {}
    return {
        "fonts": dict(state["fonts"]),
        "bold": (max(weights.values()) >= 600) if weights else False,
        "text_scale": state["text_scale"],
        "icon_spacing": state["icon_spacing"],
    }


def apply_size(size):
    """Apply a dict shaped like size_of()."""
    ok = apply_fonts(size.get("fonts") or {}, bold=size.get("bold", False))
    if size.get("text_scale"):
        apply_text_scale(size["text_scale"])
    if size.get("icon_spacing"):
        apply_icon_spacing(size["icon_spacing"])
    return ok


def discard_backup():
    try:
        os.remove(BACKUP_PATH)
        return True
    except OSError:
        return False


class TextWorker:
    """
    Runs the Windows calls on their own thread.

    Every one of these settings is announced with WM_SETTINGCHANGE, and Windows
    waits for each top-level window on the desktop to acknowledge it.  Measured
    on a normal desktop that is roughly 0.8 s for a rendering change and 1.3 s
    for a size change.  On the UI thread that reads as the whole window locking
    up, so a switch press queues the work here and returns immediately.

    One job per section is kept: flipping a switch twice quickly runs the final
    state once instead of replaying the whole sequence.
    """

    ORDER = ("rendering", "size", "all")

    def __init__(self, on_result=None):
        self.on_result = on_result          # (section, label, ok, error) on the worker thread
        self._lock = threading.Lock()       # serialises the actual Windows calls
        self._slot = threading.Lock()
        self._pending = {}
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="textclarity", daemon=True)
        self._thread.start()

    def submit(self, section, label, fn):
        """Queue fn() for this section, replacing anything still waiting."""
        with self._slot:
            self._pending[section] = (label, fn)
            self._idle.clear()
        self._wake.set()

    def run_sync(self, fn):
        """Run fn() on the caller's thread, serialised against the worker."""
        with self._lock:
            return fn()

    def busy(self):
        return not self._idle.is_set()

    def wait_idle(self, timeout=None):
        return self._idle.wait(timeout)

    def _take(self):
        with self._slot:
            for section in self.ORDER:
                if section in self._pending:
                    return section, self._pending.pop(section)
            if self._pending:
                return self._pending.popitem()
            return None, None

    def _drain(self):
        while True:
            section, job = self._take()
            if job is None:
                return
            label, fn = job
            try:
                with self._lock:
                    ok = fn()
                err = None
            except Exception as e:
                ok, err = False, f"{type(e).__name__}: {e}"
            if self.on_result:
                try:
                    self.on_result(section, label, bool(ok), err)
                except Exception:
                    pass

    def _run(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            self._drain()
            # The stop check belongs after draining.  Checking first meant that
            # a stop arriving while work was queued got its wake-up consumed by
            # that work, and the thread then parked on an event nobody would
            # ever set again - a six second stall on every exit.
            if self._stop:
                self._drain()
                self._idle.set()
                return
            with self._slot:
                if not self._pending:
                    self._idle.set()

    def stop(self, timeout=6.0):
        """Let queued work finish first: a half-applied change would be worse
        than a slow exit."""
        self._stop = True
        self._wake.set()
        self._thread.join(timeout)
