"""
Global hotkeys via RegisterHotKey on a dedicated message-loop thread.
Events are pushed to a queue as ("hotkey", name).
"""
import ctypes
import ctypes.wintypes as wt
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000

MODS = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}

VK = {
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "space": 0x20, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "return": 0x0D,
    "backspace": 0x08, "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "pause": 0x13, "printscreen": 0x2C,
    "plus": 0xBB, "=": 0xBB, "minus": 0xBD, "-": 0xBD, "comma": 0xBC, ",": 0xBC,
    "period": 0xBE, ".": 0xBE, "slash": 0xBF, "/": 0xBF, "backslash": 0xDC, "\\": 0xDC,
    "semicolon": 0xBA, ";": 0xBA, "quote": 0xDE, "'": 0xDE, "[": 0xDB, "]": 0xDD, "`": 0xC0,
    "add": 0x6B, "subtract": 0x6D, "multiply": 0x6A, "divide": 0x6F,
}
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x6F + _i
for _i in range(10):
    VK[f"numpad{_i}"] = 0x60 + _i
    VK[f"num{_i}"] = 0x60 + _i
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    VK[_c] = ord(_c.upper())


def parse_hotkey(text):
    """'ctrl+alt+up' -> (modifiers, vk).  Raises ValueError on bad input."""
    parts = [p.strip().lower() for p in str(text).replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty")
    mods, key = 0, None
    for p in parts:
        if p in MODS:
            mods |= MODS[p]
        elif p in VK:
            if key is not None:
                raise ValueError("two keys")
            key = VK[p]
        else:
            raise ValueError(f"unknown key '{p}'")
    if key is None:
        raise ValueError("no main key")
    if mods == 0:
        raise ValueError("needs a modifier (ctrl/alt/shift/win)")
    return mods, key


class HotkeyManager:
    def __init__(self, out_queue):
        self.queue = out_queue
        self._thread = None
        self._tid = None
        self._ready = threading.Event()
        self.failed = {}      # name -> reason
        self.bindings = {}

    def set_bindings(self, bindings):
        """bindings: {name: 'ctrl+alt+x'}; empty string disables that action."""
        self.stop()
        self.bindings = {k: v for k, v in bindings.items() if v}
        self.failed = {}
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return dict(self.failed)

    def stop(self):
        if self._thread and self._thread.is_alive() and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(2.0)
        self._thread = None
        self._tid = None

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        ids = {}
        next_id = 1
        for name, text in self.bindings.items():
            try:
                mods, vk = parse_hotkey(text)
            except ValueError as e:
                self.failed[name] = str(e)
                continue
            if user32.RegisterHotKey(None, next_id, mods | MOD_NOREPEAT, vk):
                ids[next_id] = name
                next_id += 1
            else:
                self.failed[name] = "already in use by another app"
        self._ready.set()
        msg = wt.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in ids:
                    self.queue.put(("hotkey", ids[msg.wParam]))
        finally:
            for hid in ids:
                user32.UnregisterHotKey(None, hid)
