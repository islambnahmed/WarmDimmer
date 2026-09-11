"""
Click-through dimming overlay: one borderless, topmost, transparent window
per monitor.  Used only for the part of the dimming the gamma driver refuses
(when Windows' gamma range is locked), so normally it stays hidden.
"""
import ctypes
import tkinter as tk

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _get_long = user32.GetWindowLongPtrW
    _set_long = user32.SetWindowLongPtrW
    _get_long.restype = ctypes.c_longlong
    _set_long.restype = ctypes.c_longlong
    _get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
else:
    _get_long = user32.GetWindowLongW
    _set_long = user32.SetWindowLongW
user32.GetAncestor.restype = ctypes.c_void_p
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]


class DimOverlay:
    def __init__(self, root):
        self.root = root
        self.windows = {}      # device -> Toplevel
        self.alpha = 0.0
        self.color = "#000000"

    def sync_monitors(self, monitors):
        wanted = {m.device: m.rect for m in monitors}
        for dev in list(self.windows):
            if dev not in wanted:
                self.windows.pop(dev).destroy()
        for dev, rect in wanted.items():
            x, y, w, h = rect
            top = self.windows.get(dev)
            if top is None:
                top = tk.Toplevel(self.root)
                top.overrideredirect(True)
                top.attributes("-topmost", True)
                top.attributes("-alpha", 0.0)
                top.configure(bg=self.color)
                top.withdraw()
                self.windows[dev] = top
            top.geometry(f"{w}x{h}+{x}+{y}")
        self._apply()

    def _make_click_through(self, top):
        try:
            hwnd = user32.GetAncestor(top.winfo_id(), GA_ROOT)
            style = _get_long(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            _set_long(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def set(self, alpha, color="#000000"):
        self.alpha = max(0.0, min(0.92, float(alpha)))
        self.color = color
        self._apply()

    def _apply(self):
        for top in self.windows.values():
            if self.alpha < 0.01:
                if top.winfo_viewable():
                    top.withdraw()
                continue
            top.configure(bg=self.color)
            top.attributes("-alpha", self.alpha)
            if not top.winfo_viewable():
                top.deiconify()
                top.update_idletasks()
                self._make_click_through(top)
                top.attributes("-topmost", True)
                top.lift()

    def keep_on_top(self):
        if self.alpha >= 0.01:
            for top in self.windows.values():
                top.attributes("-topmost", True)
                top.lift()

    def destroy(self):
        for top in self.windows.values():
            top.destroy()
        self.windows.clear()
