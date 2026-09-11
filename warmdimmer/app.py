"""Application core: state, timers, hotkeys, tray, persistence."""
import atexit
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
import winreg

from . import APP_NAME
from .breaks import BreakTimer, IDLE, DUE
from .breakscreen import BreakScreen
from .config import Config, APP_DIR
from .gamma import GammaController, GammaWorker, gamma_range_unlocked, request_gamma_unlock
from .hotkeys import HotkeyManager
from .overlay import DimOverlay
from .textclarity import TextWorker
from .tray import Tray

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.OpenEventW.restype = ctypes.c_void_p
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
kernel32.SetEvent.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

MUTEX_NAME = "Local\\WarmDimmer.SingleInstance"
EVENT_NAME = "Local\\WarmDimmer.ShowWindow"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "WarmDimmer"


def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def acquire_single_instance():
    """Returns a mutex handle, or None if another instance is running
    (in which case that instance is asked to show its window)."""
    # ctypes.get_last_error() reads the value saved by the call itself.  Going
    # through kernel32.GetLastError() runs a GetProcAddress on first use, which
    # can overwrite the code we are about to test and let a second instance
    # start and fight the first over gamma and hotkeys.
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        ev = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, EVENT_NAME)
        if ev:
            kernel32.SetEvent(ev)
            kernel32.CloseHandle(ev)
        return None
    return handle


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    entry = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "WarmDimmer.pyw"))
    return f'"{exe}" "{entry}"'


def get_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
            return True
    except OSError:
        return False


def set_autostart(enabled):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def _smoothstep(p):
    return p * p * (3 - 2 * p)


def _ui_scale():
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


class WarmDimmerApp:
    def __init__(self):
        self.cfg = Config.load()
        self.cfg.clamp_state()
        self.temperature = self.cfg["temperature"]
        self.brightness = self.cfg["brightness"]
        self.enabled = bool(self.cfg["enabled"])
        self.limited = False
        self.gamma_unlocked = gamma_range_unlocked()

        self.queue = queue.Queue()
        self.listeners = []
        self._stopping = False
        self._anim = None
        self._save_job = None
        self._queue_job = None
        self._reapply_job = None
        self._break_job = None
        self._hw = (6500, 1.0)

        self.root = tk.Tk()
        self.root.withdraw()

        self.gamma = GammaController()
        self.gamma.disabled_devices |= set(self.cfg.get("disabled_monitors", []))
        self.worker = GammaWorker(self.gamma, self._on_gamma_result)
        self.overlay = DimOverlay(self.root)
        self.overlay.sync_monitors(self.gamma.monitors)

        self.text_worker = TextWorker(
            lambda section, label, ok, err: self.queue.put(("text", section, label, ok, err)))

        self.breaks = BreakTimer(self.cfg, on_start=self._break_started, on_end=self._break_ended)
        self.break_screen = BreakScreen(self.root, self._skip_break, self._postpone_break,
                                        scale=_ui_scale())
        self._break_warned = False

        self.hotkeys = HotkeyManager(self.queue)
        self.hotkey_failures = self.hotkeys.set_bindings(self.cfg["hotkeys"])

        self.tray = Tray(self.queue, self.state, lambda: self.cfg["presets"],
                         get_break=self._break_summary)

        from .ui import MainWindow
        self.window = None
        self.window = MainWindow(self)

        self.tray.start()
        self._start_show_listener()
        atexit.register(self._restore_hw)

        self.apply_now()
        self._queue_job = self.root.after(50, self._tick_queue)
        self._reapply_job = self.root.after(self.cfg["reapply_interval_s"] * 1000, self._tick_reapply)
        self._break_job = self.root.after(500, self._tick_breaks)
        if not self.cfg["start_minimized"]:
            self.show_window()

    # ------------------------------------------------------------ state
    def state(self):
        return {"temperature": self.temperature, "brightness": self.brightness,
                "enabled": self.enabled, "limited": self.limited}

    def add_listener(self, fn):
        self.listeners.append(fn)

    def _emit(self, kind):
        for fn in list(self.listeners):
            try:
                fn(kind)
            except Exception:
                pass

    def clamp(self, temperature, brightness):
        t = int(max(self.cfg["temperature_min"], min(int(temperature), self.cfg["temperature_max"])))
        b = int(max(self.cfg["brightness_min"], min(int(brightness), 100)))
        return t, b

    def set_values(self, temperature=None, brightness=None, animate=False):
        t = self.temperature if temperature is None else temperature
        b = self.brightness if brightness is None else brightness
        t, b = self.clamp(t, b)
        if (t, b) == (self.temperature, self.brightness) and self._anim is None:
            return
        self.temperature, self.brightness = t, b
        self.cfg["temperature"], self.cfg["brightness"] = t, b
        if animate and self.cfg["transition_ms"] > 0:
            self._start_animation()
        else:
            self._cancel_animation()
            self.apply_now()
        self._emit("state")
        self.tray.refresh()
        self.schedule_save()

    def toggle(self, enabled=None):
        self.enabled = (not self.enabled) if enabled is None else bool(enabled)
        self.cfg["enabled"] = self.enabled
        self._start_animation()
        self._emit("state")
        self.tray.refresh()
        self.schedule_save()

    def nudge(self, d_temp=0, d_bright=0):
        self.set_values(self.temperature + d_temp, self.brightness + d_bright, animate=True)

    # ------------------------------------------------------------ hardware
    def _target_hw(self):
        """Warmth and dimming are separate switches under one master pause."""
        if not self.enabled:
            return 6500, 1.0
        # .get with a default: the README invites hand-editing config.json, and
        # a deleted key must not break the core warmth/dimming path.
        kelvin = self.temperature if self.cfg.get("warmth_enabled", True) else 6500
        bright = self.brightness / 100.0 if self.cfg.get("dimmer_enabled", True) else 1.0
        return kelvin, bright

    def set_section_enabled(self, section, enabled):
        """section is 'warmth' or 'dimmer'."""
        key = f"{section}_enabled"
        if self.cfg.get(key) == bool(enabled):
            return
        self.cfg[key] = bool(enabled)
        self._start_animation()
        self._emit("state")
        self.tray.refresh()
        self.schedule_save()

    def _apply_hw(self, kelvin, bright, force=False):
        """Hand the values to the gamma thread; never blocks the UI."""
        self._hw = (kelvin, bright)
        self.worker.request(kelvin, bright, force=force)

    def _on_gamma_result(self, target, factor, limited):
        # worker thread -> UI thread
        self.queue.put(("gamma", target, factor, limited))

    def _handle_gamma_result(self, target, factor, limited):
        bright = target[1]
        achieved = 1.0 - factor * (1.0 - bright)
        alpha = 0.0 if achieved - bright < 0.005 else 1.0 - bright / achieved
        self.overlay.set(alpha)
        if limited != self.limited:
            self.limited = limited
            self._emit("limited")

    def apply_now(self, force=False):
        self._apply_hw(*self._target_hw(), force=force)

    def _start_animation(self):
        self._cancel_animation()
        ms = self.cfg["transition_ms"]
        if ms <= 0:
            self.apply_now()
            return
        start_hw = self._hw
        t0 = time.perf_counter()

        def step():
            p = min(1.0, (time.perf_counter() - t0) * 1000.0 / ms)
            e = _smoothstep(p)
            tk_, tb = self._target_hw()
            k = start_hw[0] + (tk_ - start_hw[0]) * e
            b = start_hw[1] + (tb - start_hw[1]) * e
            self._apply_hw(k, b)
            if p < 1.0:
                self._anim = self.root.after(16, step)
            else:
                self._anim = None

        self._anim = self.root.after(0, step)

    def _cancel_animation(self):
        if self._anim is not None:
            try:
                self.root.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None

    def _restore_hw(self):
        try:
            self.worker.restore()
        except Exception:
            pass

    def reset_display(self):
        """Emergency: identity ramp + hide overlay, and pause."""
        self._cancel_animation()
        self.enabled = False
        self.cfg["enabled"] = False
        self._restore_hw()
        self.overlay.set(0.0)
        self._hw = (6500, 1.0)
        self._emit("state")
        self.tray.refresh()
        self.schedule_save()

    # ------------------------------------------------------------ breaks
    def _break_summary(self):
        from .breaks import format_left
        if not self.breaks.enabled:
            return None
        return format_left(self.breaks.seconds_left())

    def _break_started(self):
        self._break_warned = False
        self.break_screen.show(self.gamma.monitors, self.cfg.get("break_message", ""),
                               self.breaks.duration)
        self._emit("break")

    def _break_ended(self):
        self.break_screen.hide()
        self.tray.refresh()
        self._emit("break")

    def _skip_break(self):
        self.breaks.skip()
        self.break_screen.hide()
        self._emit("break")

    def _postpone_break(self):
        self.breaks.postpone(5)
        self.break_screen.hide()
        self._emit("break")

    def set_break_enabled(self, on):
        self.cfg["break_enabled"] = bool(on)
        if on:
            self.breaks.reset()
        else:
            self.breaks.skip()
            self.break_screen.hide()
        self._emit("break")
        self.schedule_save()

    def take_break_now(self):
        """Asked for deliberately, so it runs even with the reminder switched
        off and even while something is full screen."""
        if self.breaks.state != DUE:
            self.breaks.start_break(manual=True)

    def _tick_breaks(self):
        if self._stopping:
            return
        try:
            before = self.breaks.state
            state = self.breaks.tick()
            if state == DUE:
                self.break_screen.update(self.breaks.seconds_left())
            elif before == DUE:
                self.break_screen.hide()
            else:
                warn = int(self.cfg.get("break_notify_before_s", 0))
                left = self.breaks.seconds_left()
                if warn and left > warn:
                    # Arm the warning again. Without this a break that never
                    # arrived - because you stepped away or were in a call -
                    # left the flag set and the next one crept up silently.
                    self._break_warned = False
                elif (warn and not self._break_warned and self.breaks.enabled
                        and left <= warn):
                    self._break_warned = True
                    self.tray.notify(f"Eye break in {left}s")
            self._emit("break_tick")
        except Exception:
            pass
        self._break_job = self.root.after(500, self._tick_breaks)

    # ------------------------------------------------------------ presets
    def apply_preset(self, index):
        try:
            p = self.cfg["presets"][index]
        except IndexError:
            return
        if not self.enabled:
            self.enabled = True
            self.cfg["enabled"] = True
        self.temperature, self.brightness = self.clamp(p["temperature"], p["brightness"])
        self.cfg["temperature"], self.cfg["brightness"] = self.temperature, self.brightness
        self._start_animation()
        self._emit("state")
        self.tray.refresh()
        self.schedule_save()

    def add_preset(self, name):
        name = (name or "").strip()[:24]
        if not name:
            return
        self.cfg["presets"].append({"name": name, "temperature": self.temperature,
                                    "brightness": self.brightness})
        self._presets_changed()

    def overwrite_preset(self, index):
        p = self.cfg["presets"][index]
        p["temperature"], p["brightness"] = self.temperature, self.brightness
        self._presets_changed()

    def rename_preset(self, index, name):
        name = (name or "").strip()[:24]
        if name:
            self.cfg["presets"][index]["name"] = name
            self._presets_changed()

    def delete_preset(self, index):
        del self.cfg["presets"][index]
        self._presets_changed()

    def move_preset(self, index, delta):
        ps = self.cfg["presets"]
        j = index + delta
        if 0 <= j < len(ps):
            ps[index], ps[j] = ps[j], ps[index]
            self._presets_changed()

    def _presets_changed(self):
        self._emit("presets")
        self.tray.refresh(rebuild_menu=True)
        self.schedule_save()

    # ------------------------------------------------------------ settings
    def update_hotkeys(self, bindings):
        self.cfg["hotkeys"].update(bindings)
        self.hotkey_failures = self.hotkeys.set_bindings(self.cfg["hotkeys"])
        self.schedule_save()
        self._emit("settings")
        return self.hotkey_failures

    def update_settings(self, **kw):
        for k, v in kw.items():
            if k in self.cfg:
                self.cfg[k] = v
        self.cfg.clamp_state()
        if "autostart" in kw:
            set_autostart(bool(kw["autostart"]))
        # ranges may have moved the current values
        self.temperature, self.brightness = self.cfg["temperature"], self.cfg["brightness"]
        self.apply_now()
        self.schedule_save()
        self._emit("settings")
        self._emit("state")

    def set_monitor_enabled(self, device, enabled):
        def mutate(c):
            c.set_enabled(device, enabled)
            return sorted(c.disabled_devices)

        self.cfg["disabled_monitors"] = self.worker.call(mutate)
        self.apply_now(force=True)
        self.schedule_save()

    def unlock_gamma_range(self):
        return request_gamma_unlock()

    def schedule_save(self):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(600, self._save_now)

    def _save_now(self):
        self._save_job = None
        try:
            self.cfg.save()
        except OSError:
            pass

    # ------------------------------------------------------------ window
    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def hide_window(self):
        self.root.withdraw()

    def on_close_request(self):
        if self.cfg["close_to_tray"]:
            self.hide_window()
        else:
            self.quit()

    def quit(self):
        if self._stopping:
            return
        self._stopping = True
        self._cancel_animation()
        # Drop every pending timer before destroying the interpreter, otherwise
        # Tk fires them against a dead interpreter and prints spurious errors.
        for attr in ("_save_job", "_queue_job", "_reapply_job", "_break_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._save_now()
        self.hotkeys.stop()
        self.tray.stop()
        self.break_screen.hide()
        self.overlay.destroy()
        self.text_worker.stop()      # lets a queued text change finish first
        self.worker.stop()
        self._restore_hw()
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------ loops
    def _tick_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        if not self._stopping:
            self._queue_job = self.root.after(25, self._tick_queue)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "gamma":
            self._handle_gamma_result(msg[1], msg[2], msg[3])
        elif kind == "text":
            if self.window is not None:
                self.window.on_text_result(msg[1], msg[2], msg[3], msg[4])
        elif kind == "hotkey":
            name = msg[1]
            st, sb = self.cfg["step_temperature"], self.cfg["step_brightness"]
            if name == "warmer":
                self.nudge(d_temp=-st)
            elif name == "cooler":
                self.nudge(d_temp=+st)
            elif name == "brighter":
                self.nudge(d_bright=+sb)
            elif name == "dimmer":
                self.nudge(d_bright=-sb)
            elif name == "toggle":
                self.toggle()
            elif name == "show":
                if self.root.state() == "withdrawn":
                    self.show_window()
                else:
                    self.hide_window()
        elif kind == "show":
            self.show_window()
        elif kind == "toggle":
            self.toggle()
        elif kind == "preset":
            self.apply_preset(msg[1])
        elif kind == "break_now":
            self.take_break_now()
        elif kind == "exit":
            self.quit()

    def _tick_reapply(self):
        if self._stopping:
            return
        try:
            if self.worker.call(lambda c: c.refresh_monitors()):
                self.overlay.sync_monitors(self.gamma.monitors)
                self._emit("monitors")
            if self._anim is None:
                # re-assert: some apps and drivers reset the ramp behind us
                self.apply_now(force=True)
            self.overlay.keep_on_top()
        except Exception:
            pass
        if not self._stopping:
            self._reapply_job = self.root.after(self.cfg["reapply_interval_s"] * 1000,
                                                self._tick_reapply)

    def _start_show_listener(self):
        def run():
            ev = kernel32.CreateEventW(None, False, False, EVENT_NAME)
            if not ev:
                return
            while not self._stopping:
                if kernel32.WaitForSingleObject(ev, 500) == 0:
                    self.queue.put(("show",))
            kernel32.CloseHandle(ev)

        threading.Thread(target=run, name="show-listener", daemon=True).start()

    def run(self):
        self.root.mainloop()


def main():
    set_dpi_awareness()
    handle = acquire_single_instance()
    if handle is None:
        return 0
    app = WarmDimmerApp()
    try:
        app.run()
    finally:
        app.quit()
    return 0
