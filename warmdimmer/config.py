"""JSON config stored in %APPDATA%\\WarmDimmer\\config.json."""
import copy
import json
import os

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "WarmDimmer")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    "temperature": 4200,
    "brightness": 85,
    "enabled": True,          # master pause for everything the app does

    # Each function owns its own switch, so warmth can run without dimming
    # and the text settings are untouched until you turn them on yourself.
    "warmth_enabled": True,
    "dimmer_enabled": True,
    "text_rendering_enabled": False,
    "text_size_enabled": False,
    "text_rendering": None,   # filled from the live system on first run
    "text_size": None,

    # Eye-rest reminders, defaulting to the 20-20-20 pattern. Off until asked
    # for: an app that starts interrupting people uninvited gets uninstalled.
    "break_enabled": False,
    "break_interval_min": 20,
    "break_duration_s": 20,
    "break_idle_reset_s": 120,   # time away that counts as a break already taken
    "break_message": "Time to rest your eyes",
    "break_notify_before_s": 30,  # heads-up before the screen appears (0 = none)
    # Never cover a video call, a game or a film. The break waits instead.
    "break_skip_fullscreen": True,

    "presets": [
        {"name": "Day",     "temperature": 6500, "brightness": 100},
        {"name": "Office",  "temperature": 5000, "brightness": 90},
        {"name": "Evening", "temperature": 3800, "brightness": 75},
        {"name": "Night",   "temperature": 3000, "brightness": 55},
        {"name": "Bed",     "temperature": 2200, "brightness": 35},
    ],

    "hotkeys": {
        "warmer":   "ctrl+alt+left",
        "cooler":   "ctrl+alt+right",
        "brighter": "ctrl+alt+up",
        "dimmer":   "ctrl+alt+down",
        "toggle":   "ctrl+alt+p",
        "show":     "ctrl+alt+w",
    },

    "step_temperature": 200,     # K per hotkey press
    "step_brightness": 5,        # % per hotkey press
    "temperature_min": 1500,
    "temperature_max": 6500,
    "brightness_min": 10,
    "transition_ms": 350,        # smooth fade for presets / hotkeys (0 = instant)

    "start_minimized": False,
    "close_to_tray": True,
    "autostart": False,
    "disabled_monitors": [],
    "reapply_interval_s": 2,
}


_REJECT = object()          # "this stored value is unusable, keep the default"

_NUMERIC = {k for k, v in DEFAULTS.items() if isinstance(v, int) and not isinstance(v, bool)}
_BOOL = {k for k, v in DEFAULTS.items() if isinstance(v, bool)}
_STRING = {k for k, v in DEFAULTS.items() if isinstance(v, str)}

_RENDERING_KEYS = ("cleartype", "smoothing_type", "contrast", "orientation")
_SIZE_KEYS = ("fonts", "bold", "text_scale", "icon_spacing")


def _clean_presets(value):
    if not isinstance(value, list):
        return _REJECT
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            t = int(item["temperature"])
            b = int(item["brightness"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"name": name.strip()[:24], "temperature": t, "brightness": b})
    return out or _REJECT


def _clean_hotkeys(value):
    if not isinstance(value, dict):
        return _REJECT
    out = copy.deepcopy(DEFAULTS["hotkeys"])
    for k, v in value.items():
        if k in out and isinstance(v, str):
            out[k] = v
    return out


def _clean_rendering(value):
    if not isinstance(value, dict) or any(k not in value for k in _RENDERING_KEYS):
        return None          # incomplete: re-read it from the live system
    try:
        return {"cleartype": bool(value["cleartype"]),
                "smoothing_type": int(value["smoothing_type"]),
                "contrast": int(value["contrast"]),
                "orientation": int(value["orientation"])}
    except (TypeError, ValueError):
        return None


def _clean_size(value):
    if not isinstance(value, dict) or any(k not in value for k in _SIZE_KEYS):
        return None
    fonts = value.get("fonts")
    if not isinstance(fonts, dict):
        return None
    clean_fonts = {}
    for k, v in fonts.items():
        try:
            clean_fonts[str(k)] = float(v)
        except (TypeError, ValueError):
            return None
    try:
        return {"fonts": clean_fonts, "bold": bool(value["bold"]),
                "text_scale": int(value["text_scale"]),
                "icon_spacing": int(value["icon_spacing"])}
    except (TypeError, ValueError):
        return None


def _clean(key, value):
    """Return a usable value for `key`, or _REJECT to fall back to the default."""
    if key == "presets":
        return _clean_presets(value)
    if key == "hotkeys":
        return _clean_hotkeys(value)
    if key == "text_rendering":
        return _clean_rendering(value)
    if key == "text_size":
        return _clean_size(value)
    if key == "disabled_monitors":
        return [d for d in value if isinstance(d, str)] if isinstance(value, list) else _REJECT
    if key in _BOOL:
        return bool(value) if isinstance(value, (bool, int)) else _REJECT
    if key in _NUMERIC:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _REJECT
        return int(value)
    if key in _STRING:
        return value if isinstance(value, str) else _REJECT
    return value


class Config(dict):
    @classmethod
    def load(cls):
        """
        Read the saved settings, repairing anything unusable.

        The README invites people to edit this file by hand, so one bad line
        must not stop the app from opening.  Every value is checked against the
        shape of its default; anything that does not fit is quietly replaced
        rather than left to raise somewhere far away at startup.
        """
        data = copy.deepcopy(DEFAULTS)
        stored = {}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                stored = loaded
        except (OSError, ValueError):
            pass
        for key, value in stored.items():
            if key not in DEFAULTS:
                continue                     # unknown key: ignore, do not carry it
            cleaned = _clean(key, value)
            if cleaned is not _REJECT:
                data[key] = cleaned
        return cls(data)

    def save(self):
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)

    def _num(self, key):
        """Belt and braces: load() already coerced these, but clamp_state is
        also called after the UI writes values."""
        try:
            v = self[key]
            return int(v) if not isinstance(v, bool) else int(DEFAULTS[key])
        except (KeyError, TypeError, ValueError):
            return int(DEFAULTS[key])

    def clamp_state(self):
        for key in _NUMERIC:
            self[key] = self._num(key)
        self["temperature_min"] = int(max(1000, min(self["temperature_min"], 6400)))
        self["temperature_max"] = int(max(self["temperature_min"] + 100, min(self["temperature_max"], 10000)))
        self["brightness_min"] = int(max(1, min(self["brightness_min"], 90)))
        self["temperature"] = int(max(self["temperature_min"], min(self["temperature"], self["temperature_max"])))
        self["brightness"] = int(max(self["brightness_min"], min(self["brightness"], 100)))
        self["step_temperature"] = int(max(10, self["step_temperature"]))
        self["step_brightness"] = int(max(1, self["step_brightness"]))
        self["transition_ms"] = int(max(0, min(self["transition_ms"], 3000)))
        self["reapply_interval_s"] = int(max(1, min(self["reapply_interval_s"], 60)))
        self["break_interval_min"] = int(max(1, min(self["break_interval_min"], 240)))
        self["break_duration_s"] = int(max(5, min(self["break_duration_s"], 900)))
        self["break_idle_reset_s"] = int(max(30, min(self["break_idle_reset_s"], 3600)))
        self["break_notify_before_s"] = int(max(0, min(self["break_notify_before_s"], 300)))
