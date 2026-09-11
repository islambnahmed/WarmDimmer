"""System tray icon (pystray).  All callbacks post to the app queue."""
import threading

import pystray
from PIL import Image, ImageDraw

from . import APP_NAME
from .gamma import temperature_hex


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def make_icon_image(kelvin, brightness=100, enabled=True, size=64):
    """A soft glowing disc tinted with the current colour temperature."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r, g, b = _hex_to_rgb(temperature_hex(kelvin)) if enabled else (150, 155, 165)
    k = 0.45 + 0.55 * (brightness / 100.0) if enabled else 0.75
    core = (int(r * k), int(g * k), int(b * k), 255)
    halo = (int(r * k), int(g * k), int(b * k), 70)
    pad = size * 0.08
    d.ellipse([pad, pad, size - pad, size - pad], fill=halo)
    pad = size * 0.22
    d.ellipse([pad, pad, size - pad, size - pad], fill=core)
    if not enabled:
        w = max(2, size // 12)
        d.line([size * 0.25, size * 0.75, size * 0.75, size * 0.25], fill=(30, 32, 38, 255), width=w)
    return img


class Tray:
    def __init__(self, app_queue, get_state, get_presets, get_break=None):
        self.queue = app_queue
        self.get_state = get_state
        self.get_presets = get_presets
        self.get_break = get_break or (lambda: None)
        self.icon = None
        self._thread = None
        self._last_key = None

    # -- menu ------------------------------------------------------------
    def _menu(self):
        def post(*msg):
            return lambda icon=None, item=None: self.queue.put(msg)

        preset_items = [
            pystray.MenuItem(p["name"], post("preset", i))
            for i, p in enumerate(self.get_presets())
        ] or [pystray.MenuItem("(no presets)", None, enabled=False)]

        return pystray.Menu(
            pystray.MenuItem("Open Warm Dimmer", post("show"), default=True),
            pystray.MenuItem("Enabled", post("toggle"),
                             checked=lambda item: bool(self.get_state()["enabled"])),
            pystray.MenuItem("Presets", pystray.Menu(*preset_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Take a break now", post("break_now")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", post("exit")),
        )

    def _image(self):
        s = self.get_state()
        return make_icon_image(s["temperature"], s["brightness"], s["enabled"])

    def _title(self):
        s = self.get_state()
        head = "paused" if not s["enabled"] else f"{s['temperature']}K / {s['brightness']}%"
        nxt = self.get_break()
        return f"{APP_NAME} - {head}" + (f"\nnext break in {nxt}" if nxt else "")

    # -- lifecycle -------------------------------------------------------
    def start(self):
        self.icon = pystray.Icon(APP_NAME, self._image(), self._title(), self._menu())
        self._thread = threading.Thread(target=self.icon.run, name="tray", daemon=True)
        self._thread.start()

    def refresh(self, rebuild_menu=False):
        """Redraw the icon, but skip it when nothing visible changed.

        set_values() fires on every slider pixel; regenerating the PIL image
        each time would make dragging feel heavy, and a 25K colour difference
        is invisible in a 16px icon anyway.
        """
        if not self.icon:
            return
        s = self.get_state()
        key = (s["temperature"] // 100, s["brightness"] // 2, s["enabled"])
        if key == self._last_key and not rebuild_menu:
            return
        self._last_key = key
        try:
            self.icon.icon = self._image()
            self.icon.title = self._title()
            if rebuild_menu:
                self.icon.menu = self._menu()
                self.icon.update_menu()
        except Exception:
            pass

    def notify(self, text):
        if self.icon:
            try:
                self.icon.notify(text, APP_NAME)
            except Exception:
                pass

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
