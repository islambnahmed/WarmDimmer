"""Tkinter main window: dark, compact, two controls, presets, settings."""
import copy
import ctypes
import os
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

from . import APP_NAME, __version__
from . import textclarity as tc
from .breaks import DUE, format_left
from .config import APP_DIR
from .gamma import temperature_hex
from .tray import make_icon_image

# ------------------------------------------------------------------ theme
BG = "#121419"
PANEL = "#1b1e26"
PANEL2 = "#252a35"
PANEL3 = "#303645"
BORDER = "#2b303c"
TEXT = "#eceef2"
MUTED = "#8d93a1"
ACCENT = "#ffa94d"
BRIGHT = "#e9ecf3"
GOOD = "#5ad19a"
WARN = "#f2c94c"
DANGER = "#ff6b6b"

FONT = ("Segoe UI", 10)
FONT_S = ("Segoe UI", 9)
FONT_B = ("Segoe UI", 10, "bold")
FONT_H = ("Segoe UI", 8, "bold")
FONT_T = ("Segoe UI", 14, "bold")
FONT_BIG = ("Segoe UI", 20, "bold")


def _dpi_scale():
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


S = _dpi_scale()


def px(n):
    return int(round(n * S))


# ------------------------------------------------------------------ widgets
class Slider(tk.Canvas):
    """Flat slider. Supports reversed ranges (from_ > to)."""

    def __init__(self, master, from_, to, value=None, step=1, command=None,
                 fill=ACCENT, track=PANEL3, knob="#f7f7f9", **kw):
        super().__init__(master, height=px(30), bg=kw.pop("bg", PANEL),
                         highlightthickness=0, cursor="hand2", **kw)
        self.from_, self.to, self.step = from_, to, step
        self.command = command
        self.fill, self.track, self.knob = fill, track, knob
        self.value = from_ if value is None else value
        self.pad = px(12)
        self._dragging = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_dragging", False))
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Left>", lambda e: self._nudge(-1))
        self.bind("<Right>", lambda e: self._nudge(+1))

    # geometry helpers
    def _lo_hi(self):
        return min(self.from_, self.to), max(self.from_, self.to)

    def _frac(self):
        span = self.to - self.from_
        return 0.0 if span == 0 else (self.value - self.from_) / span

    def _x_range(self):
        return self.pad, max(self.pad + 1, self.winfo_width() - self.pad)

    def _draw(self):
        self.delete("all")
        h = self.winfo_height()
        y = h / 2
        x0, x1 = self._x_range()
        w = px(6)
        self.create_line(x0, y, x1, y, width=w, fill=self.track, capstyle="round")
        xk = x0 + (x1 - x0) * self._frac()
        if xk - x0 > 1:
            self.create_line(x0, y, xk, y, width=w, fill=self.fill, capstyle="round")
        r = px(9)
        self.create_oval(xk - r, y - r, xk + r, y + r, fill=self.knob, outline=self.fill, width=px(2))

    def _value_from_x(self, x):
        x0, x1 = self._x_range()
        frac = max(0.0, min(1.0, (x - x0) / (x1 - x0)))
        v = self.from_ + frac * (self.to - self.from_)
        v = self.from_ + round((v - self.from_) / self.step) * self.step
        lo, hi = self._lo_hi()
        return int(max(lo, min(hi, v)))

    def _press(self, e):
        self.focus_set()
        self._dragging = True
        self.set(self._value_from_x(e.x), fire=True)

    def _drag(self, e):
        if self._dragging:
            self.set(self._value_from_x(e.x), fire=True)

    def _wheel(self, e):
        self._nudge(1 if e.delta > 0 else -1)

    def _nudge(self, d):
        sign = 1 if self.to >= self.from_ else -1
        lo, hi = self._lo_hi()
        self.set(int(max(lo, min(hi, self.value + d * sign * self.step))), fire=True)

    def set(self, value, fire=False):
        lo, hi = self._lo_hi()
        value = int(max(lo, min(hi, value)))
        changed = value != self.value
        self.value = value
        self._draw()
        if fire and changed and self.command:
            self.command(value)

    def set_range(self, from_, to):
        self.from_, self.to = from_, to
        self.set(self.value)

    def set_fill(self, color):
        self.fill = color
        self._draw()


class Toggle(tk.Canvas):
    def __init__(self, master, value=True, command=None, small=False, **kw):
        self.w, self.h = (px(36), px(18)) if small else (px(48), px(24))
        super().__init__(master, width=self.w, height=self.h, bg=kw.pop("bg", BG),
                         highlightthickness=0, cursor="hand2", **kw)
        self.value = value
        self.command = command
        self.bind("<Button-1>", lambda e: self.set(not self.value, fire=True))
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.w, self.h
        r = h / 2
        color = ACCENT if self.value else PANEL3
        self.create_oval(0, 0, h, h, fill=color, outline=color)
        self.create_oval(w - h, 0, w, h, fill=color, outline=color)
        self.create_rectangle(r, 0, w - r, h, fill=color, outline=color)
        pad = max(2, h // 8)
        k = h - 2 * pad
        x = (w - h + pad) if self.value else pad
        self.create_oval(x, pad, x + k, pad + k, fill="#ffffff", outline="#ffffff")

    def set(self, value, fire=False):
        self.value = bool(value)
        self._draw()
        if fire and self.command:
            self.command(self.value)


class Segmented(tk.Frame):
    """A row of mutually exclusive flat buttons."""

    def __init__(self, master, options, index=0, command=None, width=None, **kw):
        super().__init__(master, bg=kw.pop("bg", PANEL), **kw)
        self.command = command
        self.index = index
        self.enabled = True     # False = the choice is meaningless right now
        self.muted = False      # True = still choosable, just not being applied
        self.buttons = []
        for i, text in enumerate(options):
            b = tk.Label(self, text=text, font=FONT_S, cursor="hand2",
                         padx=px(6), pady=px(5))
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(3), 0))
            self.grid_columnconfigure(i, weight=1, minsize=(width // len(options)) if width else 0)
            b.bind("<Button-1>", lambda e, i=i: self._click(i))
            self.buttons.append(b)
        self._paint()

    def _click(self, i):
        if not self.enabled or i == self.index:
            return
        self.index = i
        self._paint()
        if self.command:
            self.command(i)

    def set(self, index):
        self.index = max(0, min(len(self.buttons) - 1, int(index)))
        self._paint()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self._paint()

    def set_muted(self, muted):
        """Grey it out but keep it clickable: a switched-off section can still
        be configured, and the choice is applied when the switch goes on."""
        self.muted = bool(muted)
        self._paint()

    def _paint(self):
        for i, b in enumerate(self.buttons):
            on = (i == self.index)
            if not self.enabled:
                b.configure(bg=PANEL2, fg=BORDER, cursor="arrow")
            elif self.muted:
                b.configure(bg=PANEL3 if on else PANEL2, fg=MUTED, cursor="hand2")
            else:
                b.configure(bg=ACCENT if on else PANEL2,
                            fg="#1a1206" if on else TEXT, cursor="hand2")


def dark_title_bar(window):
    """Ask DWM for a dark title bar (Windows 10 20H1+; falls back to older attribute)."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


class ScrollFrame(tk.Frame):
    """Vertical scroll container; put content in .inner"""

    def __init__(self, master):
        super().__init__(master, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Dark.Vertical.TScrollbar", gripcount=0, background=PANEL3, troughcolor=BG,
                        bordercolor=BG, lightcolor=PANEL3, darkcolor=PANEL3, arrowcolor=MUTED,
                        arrowsize=px(10), width=px(8))
        style.map("Dark.Vertical.TScrollbar", background=[("active", ACCENT)])
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                  style="Dark.Vertical.TScrollbar")
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")


def flat_button(master, text, command, primary=False, small=False, **kw):
    bg = ACCENT if primary else PANEL2
    fg = "#1a1206" if primary else TEXT
    b = tk.Button(master, text=text, command=command, bg=bg, fg=fg,
                  activebackground=("#ffbf74" if primary else PANEL3), activeforeground=fg,
                  relief="flat", bd=0, cursor="hand2",
                  font=(FONT_S if small else FONT_B),
                  padx=px(10 if small else 14), pady=px(3 if small else 6), **kw)
    return b


def card(parent, title, right_widget_factory=None, toggle_command=None, toggle_value=True):
    """
    A titled panel.  Pass toggle_command to give the section its own switch:
    each function on a tab is independent, so warmth can be on while dimming
    is off, and the text settings stay untouched until their own switch is on.
    Returns (outer, body) or (outer, body, toggle).
    """
    outer = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
    head = tk.Frame(outer, bg=PANEL)
    head.pack(fill="x", padx=px(14), pady=(px(10), 0))
    tk.Label(head, text=title.upper(), bg=PANEL, fg=MUTED, font=FONT_H).pack(side="left")
    toggle = None
    if toggle_command is not None:
        toggle = Toggle(head, value=toggle_value, command=toggle_command, small=True, bg=PANEL)
        toggle.pack(side="right")
    if right_widget_factory:
        right_widget_factory(head).pack(side="right", padx=(0, px(8) if toggle else 0))
    body = tk.Frame(outer, bg=PANEL)
    body.pack(fill="both", expand=True, padx=px(14), pady=(px(6), px(12)))
    return (outer, body, toggle) if toggle_command is not None else (outer, body)


def dark_entry(master, textvariable=None, width=18):
    return tk.Entry(master, textvariable=textvariable, width=width, bg=PANEL2, fg=TEXT,
                    insertbackground=TEXT, relief="flat", font=FONT,
                    highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)


def dark_spin(master, textvariable, from_, to, inc=1, width=7):
    return tk.Spinbox(master, textvariable=textvariable, from_=from_, to=to, increment=inc,
                      width=width, bg=PANEL2, fg=TEXT, buttonbackground=PANEL3,
                      insertbackground=TEXT, relief="flat", font=FONT,
                      highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)


def dark_check(master, text, variable, command=None):
    return tk.Checkbutton(master, text=text, variable=variable, command=command,
                          bg=PANEL, fg=TEXT, selectcolor=PANEL2, activebackground=PANEL,
                          activeforeground=TEXT, font=FONT, relief="flat", bd=0,
                          highlightthickness=0, cursor="hand2", anchor="w")


class TextPrompt(tk.Toplevel):
    """Small dark prompt returning a string (or None)."""

    def __init__(self, master, title, label, initial=""):
        super().__init__(master, bg=PANEL)
        self.result = None
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.configure(highlightbackground=BORDER, highlightthickness=1)
        tk.Label(self, text=label, bg=PANEL, fg=TEXT, font=FONT).pack(padx=px(16), pady=(px(14), px(6)), anchor="w")
        self.var = tk.StringVar(value=initial)
        e = dark_entry(self, self.var, width=26)
        e.pack(padx=px(16), fill="x")
        e.icursor("end")
        e.select_range(0, "end")
        row = tk.Frame(self, bg=PANEL)
        row.pack(padx=px(16), pady=px(12), fill="x")
        flat_button(row, "OK", self._ok, primary=True, small=True).pack(side="right")
        flat_button(row, "Cancel", self.destroy, small=True).pack(side="right", padx=(0, px(8)))
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        dark_title_bar(self)
        mx, my = master.winfo_rootx(), master.winfo_rooty()
        self.geometry(f"+{mx + px(60)}+{my + px(160)}")
        e.focus_set()
        self.grab_set()
        self.wait_window()

    def _ok(self):
        self.result = self.var.get()
        self.destroy()


# ------------------------------------------------------------------ window
class MainWindow:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.cfg = app.cfg
        root = self.root

        root.title(APP_NAME)
        root.configure(bg=BG)
        root.resizable(False, False)
        # Tall enough for the Control tab's status line, which the third tab
        # had squeezed down to a single unreadable pixel; capped so the window
        # still fits on a short screen at high DPI.
        height = min(px(672), root.winfo_screenheight() - px(70))
        root.geometry(f"{px(440)}x{height}")
        root.protocol("WM_DELETE_WINDOW", app.on_close_request)
        root.bind("<Escape>", lambda e: app.hide_window())
        try:
            root.tk.call("tk", "scaling", S * 96 / 72)
        except Exception:
            pass
        self._icon_img = ImageTk.PhotoImage(make_icon_image(app.temperature, app.brightness, True, 64))
        root.iconphoto(True, self._icon_img)

        self._build_header()
        self._build_tabs()
        self._build_control_tab()
        self._build_break_tab()
        self._build_text_tab()
        self._build_settings_tab()
        self._show_tab("control")
        dark_title_bar(root)

        app.add_listener(self.on_event)
        self.on_event("state")
        self.on_event("limited")
        self.on_event("settings")

    # ---------------------------------------------------------- header
    def _build_header(self):
        h = tk.Frame(self.root, bg=BG)
        h.pack(fill="x", padx=px(18), pady=(px(16), px(8)))
        tk.Label(h, text=APP_NAME, bg=BG, fg=TEXT, font=FONT_T).pack(side="left")
        tk.Label(h, text=f"v{__version__}", bg=BG, fg=MUTED, font=FONT_S).pack(side="left", padx=(px(8), 0), pady=(px(5), 0))
        self.toggle = Toggle(h, value=self.app.enabled, command=lambda v: self.app.toggle(v))
        self.toggle.pack(side="right")
        self.toggle_label = tk.Label(h, text="ON", bg=BG, fg=ACCENT, font=FONT_B)
        self.toggle_label.pack(side="right", padx=(0, px(10)))

    def _build_tabs(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=px(18))
        self.tab_buttons = {}
        for key, text in (("control", "Control"), ("break", "Break"),
                          ("text", "Text"), ("settings", "Settings")):
            b = tk.Label(bar, text=text, bg=BG, fg=MUTED, font=FONT_B, cursor="hand2",
                         padx=px(4), pady=px(6))
            b.pack(side="left", padx=(0, px(16)))
            b.bind("<Button-1>", lambda e, k=key: self._show_tab(k))
            self.tab_buttons[key] = b
        self.tab_frames = {}
        self.tab_area = tk.Frame(self.root, bg=BG)
        self.tab_area.pack(fill="both", expand=True)
        self.tab_frames["control"] = tk.Frame(self.tab_area, bg=BG)
        self.break_scroll = ScrollFrame(self.tab_area)
        self.tab_frames["break"] = self.break_scroll
        self.text_scroll = ScrollFrame(self.tab_area)
        self.tab_frames["text"] = self.text_scroll
        self.settings_scroll = ScrollFrame(self.tab_area)
        self.tab_frames["settings"] = self.settings_scroll

    def _show_tab(self, key):
        # ScrollFrame grabs the wheel with bind_all; drop it before switching
        # so the hidden tab does not keep stealing scroll events.
        try:
            self.root.unbind_all("<MouseWheel>")
        except Exception:
            pass
        for k, f in self.tab_frames.items():
            f.pack_forget()
            self.tab_buttons[k].configure(fg=MUTED)
        self.tab_frames[key].pack(fill="both", expand=True)
        self.tab_buttons[key].configure(fg=TEXT)
        if key == "settings":
            self._refresh_monitor_list()
        elif key == "text":
            self._reload_text_state()
        elif key == "break":
            self._refresh_break()

    # ---------------------------------------------------------- control tab
    def _build_control_tab(self):
        f = self.tab_frames["control"]
        pad = dict(padx=px(18), pady=(px(8), 0))
        cfg = self.cfg

        # warmth
        c, body, self.warmth_toggle = card(
            f, "Warmth", toggle_value=cfg["warmth_enabled"],
            toggle_command=lambda v: self.app.set_section_enabled("warmth", v))
        c.pack(fill="x", **pad)
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="Color temperature", bg=PANEL, fg=TEXT, font=FONT).pack(side="left", anchor="s", pady=(0, px(4)))
        self.temp_value = tk.Label(row, text="", bg=PANEL, fg=ACCENT, font=FONT_BIG)
        self.temp_value.pack(side="right")
        self.temp_slider = Slider(body, cfg["temperature_max"], cfg["temperature_min"],
                                  value=self.app.temperature, step=50,
                                  command=lambda v: self.app.set_values(temperature=v))
        self.temp_slider.pack(fill="x", pady=(px(2), 0))
        hint = tk.Frame(body, bg=PANEL)
        hint.pack(fill="x")
        tk.Label(hint, text="Cool / neutral", bg=PANEL, fg=MUTED, font=FONT_S).pack(side="left")
        tk.Label(hint, text="Warm", bg=PANEL, fg=MUTED, font=FONT_S).pack(side="right")

        # dimmer
        c, body, self.dimmer_toggle = card(
            f, "Dimmer", toggle_value=cfg["dimmer_enabled"],
            toggle_command=lambda v: self.app.set_section_enabled("dimmer", v))
        c.pack(fill="x", **pad)
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="Brightness", bg=PANEL, fg=TEXT, font=FONT).pack(side="left", anchor="s", pady=(0, px(4)))
        self.bright_value = tk.Label(row, text="", bg=PANEL, fg=BRIGHT, font=FONT_BIG)
        self.bright_value.pack(side="right")
        self.bright_slider = Slider(body, cfg["brightness_min"], 100, value=self.app.brightness, step=1,
                                    fill=BRIGHT, command=lambda v: self.app.set_values(brightness=v))
        self.bright_slider.pack(fill="x", pady=(px(2), 0))
        hint = tk.Frame(body, bg=PANEL)
        hint.pack(fill="x")
        tk.Label(hint, text="Dark", bg=PANEL, fg=MUTED, font=FONT_S).pack(side="left")
        tk.Label(hint, text="Full", bg=PANEL, fg=MUTED, font=FONT_S).pack(side="right")

        # presets
        def add_btn(head):
            return flat_button(head, "+ Save current", self._add_preset, small=True)

        c, body = card(f, "Presets", add_btn)
        c.pack(fill="x", **pad)
        self.preset_grid = tk.Frame(body, bg=PANEL)
        self.preset_grid.pack(fill="x")
        tk.Label(body, text="Right-click a preset to overwrite, rename, reorder or delete.",
                 bg=PANEL, fg=MUTED, font=FONT_S).pack(anchor="w", pady=(px(6), 0))
        self._rebuild_presets()

        # footer
        foot = tk.Frame(f, bg=BG)
        foot.pack(fill="x", side="bottom", padx=px(18), pady=px(14))
        self.status = tk.Label(foot, text="", bg=BG, fg=MUTED, font=FONT_S, justify="left",
                               anchor="w", wraplength=px(280))
        self.status.pack(side="left", fill="x", expand=True)
        flat_button(foot, "Hide to tray", self.app.hide_window, small=True).pack(side="right")

    def _rebuild_presets(self):
        for w in self.preset_grid.winfo_children():
            w.destroy()
        cols = 3
        for i in range(cols):
            self.preset_grid.grid_columnconfigure(i, weight=1, uniform="p")
        self.preset_buttons = []
        for i, p in enumerate(self.cfg["presets"]):
            b = tk.Button(self.preset_grid, text=p["name"], bg=PANEL2, fg=TEXT, relief="flat", bd=0,
                          activebackground=PANEL3, activeforeground=TEXT, font=FONT_B, cursor="hand2",
                          pady=px(7), command=lambda i=i: self.app.apply_preset(i))
            b.grid(row=i // cols, column=i % cols, sticky="ew", padx=px(3), pady=px(3))
            b.bind("<Button-3>", lambda e, i=i: self._preset_menu(e, i))
            self.preset_buttons.append(b)
        self._highlight_presets()

    def _highlight_presets(self):
        live = (self.app.enabled and self.cfg["warmth_enabled"] and self.cfg["dimmer_enabled"])
        for b, p in zip(self.preset_buttons, self.cfg["presets"]):
            active = (live and p["temperature"] == self.app.temperature
                      and p["brightness"] == self.app.brightness)
            b.configure(bg=ACCENT if active else PANEL2, fg="#1a1206" if active else TEXT,
                        activebackground="#ffbf74" if active else PANEL3)

    def _preset_menu(self, e, i):
        m = tk.Menu(self.root, tearoff=0, bg=PANEL2, fg=TEXT, activebackground=ACCENT,
                    activeforeground="#1a1206", relief="flat", bd=0, font=FONT)
        p = self.cfg["presets"][i]
        m.add_command(label=f"{p['name']}  ({p['temperature']}K / {p['brightness']}%)", state="disabled")
        m.add_separator()
        m.add_command(label="Overwrite with current values", command=lambda: self.app.overwrite_preset(i))
        m.add_command(label="Rename", command=lambda: self._rename_preset(i))
        m.add_command(label="Move left", command=lambda: self.app.move_preset(i, -1))
        m.add_command(label="Move right", command=lambda: self.app.move_preset(i, +1))
        m.add_separator()
        m.add_command(label="Delete", command=lambda: self.app.delete_preset(i))
        m.tk_popup(e.x_root, e.y_root)

    def _add_preset(self):
        name = TextPrompt(self.root, "New preset", "Preset name:",
                          f"{self.app.temperature}K / {self.app.brightness}%").result
        if name:
            self.app.add_preset(name)

    def _rename_preset(self, i):
        name = TextPrompt(self.root, "Rename preset", "New name:", self.cfg["presets"][i]["name"]).result
        if name:
            self.app.rename_preset(i, name)

    # ---------------------------------------------------------- break tab
    def _build_break_tab(self):
        f = self.break_scroll.inner
        pad = dict(padx=(px(18), px(10)), pady=(px(8), 0))
        cfg = self.cfg

        c, body, self.break_toggle = card(
            f, "Eye rest reminder", toggle_value=cfg["break_enabled"],
            toggle_command=lambda v: self.app.set_break_enabled(v))
        c.pack(fill="x", **pad)

        self.break_countdown = tk.Label(body, text="", bg=PANEL, fg=ACCENT, font=FONT_BIG)
        self.break_countdown.pack(anchor="w")
        self.break_state = tk.Label(body, text="", bg=PANEL, fg=MUTED, font=FONT_S,
                                    anchor="w", wraplength=px(340), justify="left")
        self.break_state.pack(fill="x")

        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(px(10), 0))
        flat_button(row, "Take a break now", self.app.take_break_now,
                    primary=True, small=True).pack(side="left")
        flat_button(row, "Postpone 5 min", lambda: self.app._postpone_break(),
                    small=True).pack(side="left", padx=(px(6), 0))

        c, body = card(f, "Timing")
        c.pack(fill="x", **pad)
        grid = tk.Frame(body, bg=PANEL)
        grid.pack(fill="x")
        self.break_vars = {}
        fields = [
            ("break_interval_min", "Every (minutes)", 1, 240, 1),
            ("break_duration_s", "Rest for (seconds)", 5, 900, 5),
            ("break_idle_reset_s", "Away counts as rest after (s)", 30, 3600, 30),
            ("break_notify_before_s", "Warn me before (s, 0 = off)", 0, 300, 5),
        ]
        for n, (key, label, lo, hi, inc) in enumerate(fields):
            tk.Label(grid, text=label, bg=PANEL, fg=MUTED, font=FONT_S, anchor="w",
                     width=26).grid(row=n, column=0, sticky="w", pady=px(2))
            var = tk.StringVar(value=str(cfg[key]))
            self.break_vars[key] = var
            dark_spin(grid, var, lo, hi, inc, width=6).grid(row=n, column=1, sticky="w", pady=px(2))
        tk.Label(body, text="The 20-20-20 habit is 20 minutes of work, then 20 seconds "
                            "looking at something far away.",
                 bg=PANEL, fg=MUTED, font=FONT_S, anchor="w",
                 wraplength=px(340), justify="left").pack(fill="x", pady=(px(6), 0))

        self.var_skip_fullscreen = tk.BooleanVar(value=bool(cfg.get("break_skip_fullscreen", True)))
        dark_check(body, "Wait if something is full screen", self.var_skip_fullscreen,
                   lambda: self.app.update_settings(
                       break_skip_fullscreen=self.var_skip_fullscreen.get())).pack(anchor="w",
                                                                                   pady=(px(6), 0))
        tk.Label(body, text="Video calls, games and films are left alone. The reminder waits "
                            "and appears as soon as you are out.",
                 bg=PANEL, fg=MUTED, font=FONT_S, anchor="w",
                 wraplength=px(340), justify="left").pack(fill="x")

        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(px(8), 0))
        tk.Label(row, text="Message", bg=PANEL, fg=MUTED, font=FONT_S).pack(side="left")
        self.break_msg = tk.StringVar(value=cfg.get("break_message", ""))
        dark_entry(row, self.break_msg, width=24).pack(side="left", fill="x",
                                                       expand=True, padx=(px(8), px(8)))
        flat_button(row, "Apply", self._apply_break_settings, small=True).pack(side="right")

        c, body = card(f, "So far")
        c.pack(fill="x", **pad)
        self.break_stats = tk.Label(body, text="", bg=PANEL, fg=MUTED, font=FONT_S, anchor="w")
        self.break_stats.pack(fill="x")
        tk.Frame(f, bg=BG, height=px(12)).pack()
        self._refresh_break()

    def _apply_break_settings(self):
        vals = {}
        for k, var in self.break_vars.items():
            try:
                vals[k] = int(float(var.get()))
            except ValueError:
                pass
        vals["break_message"] = (self.break_msg.get() or "Time to rest your eyes")[:80]
        self.app.update_settings(**vals)
        for k, var in self.break_vars.items():
            var.set(str(self.cfg[k]))
        self.app.breaks.reset()
        self._refresh_break()

    def _refresh_break(self):
        b = self.app.breaks
        self.break_toggle.set(self.cfg["break_enabled"])
        if not b.enabled:
            self.break_countdown.configure(text="Off", fg=MUTED)
            self.break_state.configure(
                text="Turn this on and the app will remind you to look away from the screen.")
        elif b.state == DUE:
            self.break_countdown.configure(text=format_left(b.seconds_left()), fg=ACCENT)
            self.break_state.configure(text="Resting now.")
        elif b.deferred:
            self.break_countdown.configure(text="Waiting", fg=WARN)
            self.break_state.configure(
                text="A break is due, but something is running full screen. "
                     "It will appear as soon as you are out of it.")
        else:
            self.break_countdown.configure(text=format_left(b.seconds_left()), fg=ACCENT)
            self.break_state.configure(
                text=f"until the next break, every {b.interval // 60} minutes for "
                     f"{b.duration} seconds. The clock pauses while you are away from the machine.")
        self.break_stats.configure(text=f"{b.taken} breaks taken, {b.skipped} skipped this session.")

    # ---------------------------------------------------------- text tab
    def _build_text_tab(self):
        f = self.text_scroll.inner
        pad = dict(padx=(px(18), px(10)), pady=(px(8), 0))
        self._text_jobs = {"rendering": None, "size": None}
        self._text_busy = {}
        self._text_loading = False
        self.text_error = ""
        st = tc.read_state()

        # Seed the saved values from the live system the first time only, so a
        # fresh install shows what Windows is doing rather than our guesses.
        if not self.cfg.get("text_rendering"):
            self.cfg["text_rendering"] = tc.rendering_of(st)
        if not self.cfg.get("text_size"):
            self.cfg["text_size"] = tc.size_of(st)

        # --- rendering ---------------------------------------------------
        c, body, self.rendering_toggle = card(
            f, "Rendering", toggle_value=self.cfg["text_rendering_enabled"],
            toggle_command=lambda v: self._text_section_toggle("rendering", v))
        c.pack(fill="x", **pad)

        tk.Label(body, text="Smoothing", bg=PANEL, fg=MUTED, font=FONT_S,
                 anchor="w").pack(fill="x")
        self.smoothing_seg = Segmented(
            body, ["Off", "Greyscale", "ClearType"],
            command=lambda i: self._text_change("smoothing"))
        self.smoothing_seg.pack(fill="x", pady=(px(3), px(10)))

        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="Contrast", bg=PANEL, fg=TEXT, font=FONT).pack(side="left")
        self.contrast_value = tk.Label(row, text="", bg=PANEL, fg=ACCENT, font=FONT_B)
        self.contrast_value.pack(side="right")
        self.contrast_slider = Slider(
            body, tc.CONTRAST_MIN, tc.CONTRAST_MAX, value=st["contrast"], step=50,
            command=lambda v: self._text_change("contrast"))
        self.contrast_slider.pack(fill="x")
        hint = tk.Frame(body, bg=PANEL)
        hint.pack(fill="x")
        tk.Label(hint, text="Lighter strokes", bg=PANEL, fg=MUTED, font=FONT_S).pack(side="left")
        tk.Label(hint, text="Heavier, sharper", bg=PANEL, fg=MUTED, font=FONT_S).pack(side="right")

        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(px(10), 0))
        tk.Label(row, text="Subpixel order", bg=PANEL, fg=MUTED, font=FONT_S).pack(side="left")
        self.orient_seg = Segmented(row, ["RGB", "BGR"], width=px(120),
                                    command=lambda i: self._text_change("orientation"))
        self.orient_seg.pack(side="right")

        self.preview = tk.Label(
            body, text="Sharp text is easy on the eyes  ·  0123456789",
            bg=PANEL2, fg=TEXT, font=("Segoe UI", 11), pady=px(10), padx=px(10),
            anchor="w", wraplength=px(330), justify="left")
        self.preview.pack(fill="x", pady=(px(10), 0))
        tk.Label(body, text="Rendering changes apply to text drawn from now on. "
                            "Some apps only repaint after you restart them.",
                 bg=PANEL, fg=MUTED, font=FONT_S, anchor="w",
                 wraplength=px(340), justify="left").pack(fill="x", pady=(px(6), 0))

        # --- size --------------------------------------------------------
        c, body, self.size_toggle = card(
            f, "Size", toggle_value=self.cfg["text_size_enabled"],
            toggle_command=lambda v: self._text_section_toggle("size", v))
        c.pack(fill="x", **pad)

        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="Windows text scale", bg=PANEL, fg=TEXT, font=FONT).pack(side="left")
        self.scale_value = tk.Label(row, text="", bg=PANEL, fg=ACCENT, font=FONT_B)
        self.scale_value.pack(side="right")
        self.scale_slider = Slider(
            body, tc.SCALE_MIN, tc.SCALE_MAX, value=st["text_scale"], step=5,
            command=lambda v: self._text_change("scale"))
        self.scale_slider.pack(fill="x")

        tk.Label(body, text="UI element text size (points)", bg=PANEL, fg=MUTED,
                 font=FONT_S, anchor="w").pack(fill="x", pady=(px(12), px(4)))
        grid = tk.Frame(body, bg=PANEL)
        grid.pack(fill="x")
        for i in (1, 3):
            grid.grid_columnconfigure(i, weight=1)
        self.font_vars = {}
        for n, (key, label, _) in enumerate(tc.FONT_ELEMENTS):
            r, col = n % 3, (n // 3) * 2
            tk.Label(grid, text=label, bg=PANEL, fg=MUTED, font=FONT_S, anchor="w",
                     width=13).grid(row=r, column=col, sticky="w", pady=px(2))
            var = tk.StringVar(value=str(st["fonts"][key]))
            self.font_vars[key] = var
            sp = dark_spin(grid, var, tc.PT_MIN, tc.PT_MAX, 0.5, width=5)
            sp.grid(row=r, column=col + 1, sticky="w", padx=(0, px(12)), pady=px(2))
            sp.configure(command=lambda: self._text_change("fonts"))
            var.trace_add("write", lambda *a: self._text_change("fonts"))

        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(px(8), 0))
        self.var_bold = tk.BooleanVar(value=max(st["weights"].values()) >= 600)
        dark_check(row, "Bold UI text", self.var_bold,
                   lambda: self._text_change("fonts")).pack(side="left")
        flat_button(row, "Set all to 9 pt", lambda: self._set_all_fonts(9.0), small=True).pack(side="right")
        flat_button(row, "+1 pt", lambda: self._bump_fonts(1.0), small=True).pack(side="right", padx=(0, px(6)))

        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(px(12), 0))
        tk.Label(row, text="Desktop icon spacing", bg=PANEL, fg=TEXT, font=FONT).pack(side="left")
        self.spacing_value = tk.Label(row, text="", bg=PANEL, fg=ACCENT, font=FONT_B)
        self.spacing_value.pack(side="right")
        self.spacing_slider = Slider(
            body, tc.SPACING_MIN, tc.SPACING_MAX, value=st["icon_spacing"], step=1,
            command=lambda v: self._text_change("spacing"))
        self.spacing_slider.pack(fill="x")
        tk.Label(body, text="Text scale and icon spacing reach most apps immediately; "
                            "File Explorer and the desktop update after signing out.",
                 bg=PANEL, fg=MUTED, font=FONT_S, anchor="w",
                 wraplength=px(340), justify="left").pack(fill="x", pady=(px(6), 0))

        # --- restore -----------------------------------------------------
        c, body = card(f, "Original Windows settings")
        c.pack(fill="x", **pad)
        self.text_backup_label = tk.Label(body, text="", bg=PANEL, fg=MUTED, font=FONT_S,
                                          anchor="w", wraplength=px(340), justify="left")
        self.text_backup_label.pack(fill="x")
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(px(8), 0))
        flat_button(row, "Restore Windows defaults", self._text_restore,
                    primary=True, small=True).pack(side="left")
        flat_button(row, "Re-read from Windows", lambda: self._reload_text_state(True),
                    small=True).pack(side="left", padx=(px(6), 0))
        tk.Frame(f, bg=BG, height=px(12)).pack()

        self._reload_text_state()
        self._resync_enabled_sections(st)

    @staticmethod
    def _size_matches(a, b):
        """Windows stores font height as a whole number, so a saved 11.0 pt can
        read back as 10.8. Compare with that quantisation in mind."""
        if (bool(a.get("bold")) != bool(b.get("bold"))
                or a.get("text_scale") != b.get("text_scale")
                or a.get("icon_spacing") != b.get("icon_spacing")):
            return False
        fa, fb = a.get("fonts") or {}, b.get("fonts") or {}
        for key in set(fa) | set(fb):
            if abs(float(fa.get(key, 0)) - float(fb.get(key, 0))) > 0.5:
                return False
        return True

    def _resync_enabled_sections(self, live):
        """
        A switch that reads 'on' has to mean Windows really holds those values.
        Windows keeps them across a restart, so normally there is nothing to do;
        this only fires when something changed them while the app was closed.
        """
        if not (self.cfg["text_rendering_enabled"] or self.cfg["text_size_enabled"]):
            return
        if not tc.backup_exists():
            # Re-applying now would let ensure_backup() record the current,
            # possibly already-altered state as the user's original.
            self.text_error = ("The saved copy of your original Windows settings is missing, "
                               "so nothing was re-applied. Switch a section off and on to "
                               "start over.")
            self._sync_text_labels()
            return
        if self.cfg["text_rendering_enabled"]:
            want = dict(self.cfg["text_rendering"])
            if tc.rendering_of(live) != want:
                self._submit_text("rendering", "restart", lambda: tc.apply_rendering(**want))
        if self.cfg["text_size_enabled"]:
            want = copy.deepcopy(self.cfg["text_size"])
            if not self._size_matches(tc.size_of(live), want):
                self._submit_text("size", "restart", lambda: tc.apply_size(want))

    # -- text tab behaviour ----------------------------------------------
    def _reload_text_state(self, from_windows=False):
        """
        Fill the widgets.  Normally from the values this app has saved, so a
        section that is switched off still shows what it would apply; pass
        from_windows to resync with whatever Windows currently has instead.
        """
        if from_windows:
            st = tc.read_state()
            self.cfg["text_rendering"] = tc.rendering_of(st)
            self.cfg["text_size"] = tc.size_of(st)
            self.app.schedule_save()
        r = self.cfg["text_rendering"]
        s = self.cfg["text_size"]
        self._text_loading = True
        try:
            if not r["cleartype"]:
                self.smoothing_seg.set(0)
            else:
                self.smoothing_seg.set(2 if r["smoothing_type"] == tc.SMOOTHING_CLEARTYPE else 1)
            self.contrast_slider.set(r["contrast"])
            self.orient_seg.set(0 if r["orientation"] == tc.ORIENTATION_RGB else 1)
            self.scale_slider.set(s["text_scale"])
            self.spacing_slider.set(s["icon_spacing"])
            for key in tc.FONT_KEYS:
                if key in s["fonts"]:
                    self.font_vars[key].set(str(s["fonts"][key]))
            self.var_bold.set(bool(s["bold"]))
            self.rendering_toggle.set(self.cfg["text_rendering_enabled"])
            self.size_toggle.set(self.cfg["text_size_enabled"])
        finally:
            self._text_loading = False
        self._sync_text_labels()

    def _sync_text_labels(self):
        self.contrast_value.configure(text=str(self.contrast_slider.value))
        self.scale_value.configure(text=f"{self.scale_slider.value} %")
        self.spacing_value.configure(text=str(self.spacing_slider.value))
        r_on = self.cfg["text_rendering_enabled"]
        s_on = self.cfg["text_size_enabled"]
        # Subpixel order is only meaningful in ClearType mode, so that one is
        # genuinely disabled.  Everything else stays usable while the section
        # is off; it just reads as inactive until the switch goes on.
        self.orient_seg.set_enabled(self.smoothing_seg.index == 2)
        self.orient_seg.set_muted(not r_on)
        self.smoothing_seg.set_muted(not r_on)
        try:
            size = max(7, min(16, int(round(float(self.font_vars["message"].get() or 9)))))
        except ValueError:
            size = 9
        self.preview.configure(
            font=("Segoe UI", size, "bold" if self.var_bold.get() else "normal"),
            fg=TEXT if (r_on and self.smoothing_seg.index > 0) else MUTED)
        self.contrast_value.configure(fg=ACCENT if r_on else MUTED)
        self.contrast_slider.set_fill(ACCENT if r_on else PANEL3)
        for lbl in (self.scale_value, self.spacing_value):
            lbl.configure(fg=ACCENT if s_on else MUTED)
        for sl in (self.scale_slider, self.spacing_slider):
            sl.set_fill(ACCENT if s_on else PANEL3)
        self.rendering_toggle.set(r_on)
        self.size_toggle.set(s_on)
        if self._text_busy:
            self.text_backup_label.configure(
                text="Applying to Windows... every open window has to be told, "
                     "so this takes a moment.", fg=WARN)
        elif getattr(self, "text_error", ""):
            self.text_backup_label.configure(text=self.text_error, fg=DANGER)
        elif tc.backup_exists():
            live = [n for n, o in (("Rendering", r_on), ("Size", s_on)) if o]
            state = " and ".join(live) + (" active." if live else "")
            self.text_backup_label.configure(
                text=(f"{state} " if live else "Both sections are off, so Windows is using its "
                                               "own settings. ")
                     + "Your originals were saved before the first change and Restore puts "
                       "every value back.", fg=GOOD if live else MUTED)
        else:
            self.text_backup_label.configure(
                text="Nothing changed yet. The current Windows values are saved "
                     "automatically before the first change you make here.", fg=MUTED)

    def _set_all_fonts(self, pt):
        self._text_loading = True
        for key in tc.FONT_KEYS:
            self.font_vars[key].set(str(pt))
        self._text_loading = False
        self._text_change("fonts")

    def _bump_fonts(self, delta):
        self._text_loading = True
        for key in tc.FONT_KEYS:
            try:
                cur = float(self.font_vars[key].get())
            except ValueError:
                cur = 9.0
            self.font_vars[key].set(str(round(max(tc.PT_MIN, min(tc.PT_MAX, cur + delta)), 1)))
        self._text_loading = False
        self._text_change("fonts")

    # -- per-section switches ---------------------------------------------
    RENDER_KINDS = ("smoothing", "contrast", "orientation")

    def _collect_rendering(self):
        idx = self.smoothing_seg.index
        return {
            "cleartype": idx > 0,
            "smoothing_type": tc.SMOOTHING_CLEARTYPE if idx == 2 else tc.SMOOTHING_STANDARD,
            "contrast": self.contrast_slider.value,
            "orientation": tc.ORIENTATION_RGB if self.orient_seg.index == 0 else tc.ORIENTATION_BGR,
        }

    def _collect_size(self):
        fonts = {}
        for key in tc.FONT_KEYS:
            try:
                fonts[key] = float(self.font_vars[key].get())
            except ValueError:
                pass
        return {"fonts": fonts, "bold": self.var_bold.get(),
                "text_scale": self.scale_slider.value,
                "icon_spacing": self.spacing_slider.value}

    def _submit_text(self, section, label, fn):
        """Hand a Windows call to the worker thread and return at once."""
        self._text_busy[section] = label
        self.app.text_worker.submit(section, label, fn)
        self._sync_text_labels()

    def on_text_result(self, section, label, ok, err):
        """Called on the UI thread once the worker finishes a job."""
        if self._text_busy.get(section) == label:
            self._text_busy.pop(section, None)
        if err:
            self.text_error = f"{section}: {err}"
        elif not ok and label == "switch" and section != "all":
            self.text_error = "No saved copy of your Windows settings yet."
        elif not ok:
            self.text_error = f"Windows refused the {label} change."
        if label == "restore" and ok:
            self._reload_text_state(True)
        else:
            self._sync_text_labels()

    def _text_section_toggle(self, section, on):
        """On applies this section's saved values; off puts Windows' own back."""
        pending = self._text_jobs.get(section)
        if pending is not None:      # the switch supersedes a queued edit
            self.root.after_cancel(pending)
            self._text_jobs[section] = None
        self.cfg[f"text_{section}_enabled"] = bool(on)
        self.text_error = ""
        if section == "rendering":
            if on:
                values = self._collect_rendering()
                self.cfg["text_rendering"] = values
                job = lambda: tc.apply_rendering(**values)
            else:
                job = tc.restore_rendering
        else:
            if on:
                values = self._collect_size()
                self.cfg["text_size"] = values
                job = lambda: tc.apply_size(values)
            else:
                job = tc.restore_size
        self.app.schedule_save()
        self._submit_text(section, "switch", job)

    def _text_change(self, what):
        """
        Debounce: a system-wide font change repaints every window, so it must
        not fire on each slider pixel or each spinbox keystroke.

        Rendering and size get their own pending slot.  With one shared slot a
        font tweak right after a contrast tweak cancelled the contrast before
        it was ever recorded.
        """
        if self._text_loading:
            return
        self._sync_text_labels()
        section = "rendering" if what in self.RENDER_KINDS else "size"
        pending = self._text_jobs.get(section)
        if pending is not None:
            self.root.after_cancel(pending)
        delay = 500 if what == "fonts" else 250
        self._text_jobs[section] = self.root.after(delay, lambda: self._text_apply(what))

    def _text_apply(self, what):
        """
        Moving a control always records the value.  It only reaches Windows
        when that section's own switch is on, so the two halves of this tab
        never touch each other's settings.
        """
        rendering = what in self.RENDER_KINDS
        section = "rendering" if rendering else "size"
        self._text_jobs[section] = None
        self.text_error = ""

        if rendering:
            values = self._collect_rendering()
            self.cfg["text_rendering"] = values
        else:
            values = self._collect_size()
            self.cfg["text_size"] = values
        self.app.schedule_save()

        if not self.cfg[f"text_{section}_enabled"]:
            self._sync_text_labels()       # recorded, deliberately not applied
            return

        if rendering:
            job = lambda: tc.apply_rendering(**values)
        elif what == "fonts":
            job = lambda: tc.apply_fonts(values["fonts"], bold=values["bold"]) \
                if values["fonts"] else True
        elif what == "scale":
            job = lambda: tc.apply_text_scale(values["text_scale"])
        elif what == "spacing":
            job = lambda: tc.apply_icon_spacing(values["icon_spacing"])
        else:
            self._sync_text_labels()
            return
        self._submit_text(section, what, job)

    def _text_restore(self):
        for section, job in list(self._text_jobs.items()):
            if job is not None:
                self.root.after_cancel(job)
                self._text_jobs[section] = None
        if not tc.backup_exists():
            self.text_backup_label.configure(
                text="No saved copy found, so nothing was changed.", fg=WARN)
            return
        # Restoring means "stop managing this", so both switches go off.
        self.cfg["text_rendering_enabled"] = False
        self.cfg["text_size_enabled"] = False
        self.app.schedule_save()
        self._text_busy.clear()
        self._submit_text("all", "restore", tc.restore)

    # ---------------------------------------------------------- settings tab
    def _build_settings_tab(self):
        f = self.settings_scroll.inner
        pad = dict(padx=(px(18), px(10)), pady=(px(8), 0))
        cfg = self.cfg

        # hotkeys
        c, body = card(f, "Hotkeys")
        c.pack(fill="x", **pad)
        self.hotkey_vars = {}
        names = [("warmer", "Warmer"), ("cooler", "Cooler"), ("brighter", "Brighter"),
                 ("dimmer", "Dimmer"), ("toggle", "Pause"), ("show", "Window")]
        grid = tk.Frame(body, bg=PANEL)
        grid.pack(fill="x")
        for n, (key, label) in enumerate(names):
            r, col = n % 3, (n // 3) * 2
            tk.Label(grid, text=label, bg=PANEL, fg=MUTED, font=FONT_S, anchor="w", width=9).grid(row=r, column=col, sticky="w", pady=px(2))
            var = tk.StringVar(value=cfg["hotkeys"].get(key, ""))
            self.hotkey_vars[key] = var
            dark_entry(grid, var, width=14).grid(row=r, column=col + 1, sticky="ew", padx=(0, px(14)), pady=px(2))
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(px(6), 0))
        self.hotkey_status = tk.Label(row, text="", bg=PANEL, fg=MUTED, font=FONT_S, anchor="w", wraplength=px(260), justify="left")
        self.hotkey_status.pack(side="left", fill="x", expand=True)
        flat_button(row, "Apply hotkeys", self._apply_hotkeys, small=True).pack(side="right")

        # steps and ranges
        c, body = card(f, "Steps & ranges")
        c.pack(fill="x", **pad)
        grid = tk.Frame(body, bg=PANEL)
        grid.pack(fill="x")
        self.setting_vars = {}
        fields = [
            ("step_temperature", "Warmth step (K)", 10, 1000, 10),
            ("step_brightness", "Brightness step (%)", 1, 50, 1),
            ("temperature_min", "Warmest (K)", 1000, 6400, 100),
            ("temperature_max", "Coolest (K)", 1500, 10000, 100),
            ("brightness_min", "Darkest (%)", 1, 90, 1),
            ("transition_ms", "Fade (ms)", 0, 3000, 50),
        ]
        for n, (key, label, lo, hi, inc) in enumerate(fields):
            r, col = n % 3, (n // 3) * 2
            tk.Label(grid, text=label, bg=PANEL, fg=MUTED, font=FONT_S, anchor="w", width=16).grid(row=r, column=col, sticky="w", pady=px(2))
            var = tk.StringVar(value=str(cfg[key]))
            self.setting_vars[key] = var
            dark_spin(grid, var, lo, hi, inc, width=6).grid(row=r, column=col + 1, sticky="w", padx=(0, px(12)), pady=px(2))
        flat_button(body, "Apply", self._apply_settings, small=True).pack(anchor="e", pady=(px(6), 0))

        # behaviour
        c, body = card(f, "Behaviour")
        c.pack(fill="x", **pad)
        from .app import get_autostart
        self.var_autostart = tk.BooleanVar(value=get_autostart())
        self.var_minimized = tk.BooleanVar(value=cfg["start_minimized"])
        self.var_close_tray = tk.BooleanVar(value=cfg["close_to_tray"])
        dark_check(body, "Start with Windows", self.var_autostart,
                   lambda: self.app.update_settings(autostart=self.var_autostart.get())).pack(anchor="w")
        dark_check(body, "Start minimized to tray", self.var_minimized,
                   lambda: self.app.update_settings(start_minimized=self.var_minimized.get())).pack(anchor="w")
        dark_check(body, "Close button hides to tray (Exit from tray menu)", self.var_close_tray,
                   lambda: self.app.update_settings(close_to_tray=self.var_close_tray.get())).pack(anchor="w")

        # displays + advanced
        c, body = card(f, "Displays & advanced")
        c.pack(fill="x", **pad)
        self.monitor_box = tk.Frame(body, bg=PANEL)
        self.monitor_box.pack(fill="x")
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(px(8), 0))
        self.unlock_btn = flat_button(row, "Unlock full gamma range", self._unlock, small=True)
        self.unlock_btn.pack(side="left")
        flat_button(row, "Reset display", self.app.reset_display, small=True).pack(side="left", padx=(px(6), 0))
        flat_button(row, "Config folder", lambda: os.startfile(APP_DIR), small=True).pack(side="left", padx=(px(6), 0))
        self.advanced_status = tk.Label(body, text="", bg=PANEL, fg=MUTED, font=FONT_S, anchor="w",
                                        wraplength=px(380), justify="left")
        self.advanced_status.pack(fill="x", pady=(px(6), 0))
        tk.Frame(f, bg=BG, height=px(12)).pack()

    def _refresh_monitor_list(self):
        for w in self.monitor_box.winfo_children():
            w.destroy()
        self.monitor_vars = {}
        for m in self.app.gamma.monitors:
            var = tk.BooleanVar(value=m.device in self.app.gamma.enabled_devices)
            self.monitor_vars[m.device] = var
            w, h = m.rect[2], m.rect[3]
            dark_check(self.monitor_box, f"{m.label}  {w}x{h}", var,
                       lambda d=m.device, v=var: self.app.set_monitor_enabled(d, v.get())).pack(anchor="w")

    def _apply_hotkeys(self):
        failures = self.app.update_hotkeys({k: v.get().strip() for k, v in self.hotkey_vars.items()})
        self._show_hotkey_status(failures)

    def _show_hotkey_status(self, failures):
        if failures:
            txt = "Not registered: " + ", ".join(f"{k} ({v})" for k, v in failures.items())
            self.hotkey_status.configure(text=txt, fg=DANGER)
        else:
            self.hotkey_status.configure(text="All hotkeys active.", fg=GOOD)

    def _apply_settings(self):
        vals = {}
        for k, var in self.setting_vars.items():
            try:
                vals[k] = int(float(var.get()))
            except ValueError:
                pass
        self.app.update_settings(**vals)
        for k, var in self.setting_vars.items():
            var.set(str(self.cfg[k]))

    def _unlock(self):
        ok = self.app.unlock_gamma_range()
        if ok:
            self.advanced_status.configure(
                text="Registry updated. Sign out and back in (or restart) for the full range to take effect.", fg=WARN)
        else:
            self.advanced_status.configure(text="Unlock cancelled (needs administrator approval).", fg=DANGER)

    # ---------------------------------------------------------- events
    def on_event(self, kind):
        app = self.app
        if kind == "state":
            warm_on = app.enabled and self.cfg["warmth_enabled"]
            dim_on = app.enabled and self.cfg["dimmer_enabled"]
            self.temp_value.configure(text=f"{app.temperature} K",
                                      fg=temperature_hex(app.temperature) if warm_on else MUTED)
            self.bright_value.configure(text=f"{app.brightness} %", fg=BRIGHT if dim_on else MUTED)
            self.temp_slider.set(app.temperature)
            self.temp_slider.set_fill(temperature_hex(app.temperature) if warm_on else PANEL3)
            self.bright_slider.set(app.brightness)
            self.bright_slider.set_fill(BRIGHT if dim_on else PANEL3)
            self.warmth_toggle.set(self.cfg["warmth_enabled"])
            self.dimmer_toggle.set(self.cfg["dimmer_enabled"])
            self.toggle.set(app.enabled)
            self.toggle_label.configure(text="ON" if app.enabled else "PAUSED", fg=ACCENT if app.enabled else MUTED)
            self._highlight_presets()
            self._update_status()
        elif kind == "presets":
            self._rebuild_presets()
        elif kind == "monitors":
            self._refresh_monitor_list()
            self._update_status()
        elif kind == "limited":
            self._update_status()
            self._update_unlock_button()
        elif kind in ("break", "break_tick"):
            if hasattr(self, "break_countdown"):
                self._refresh_break()
        elif kind == "settings":
            self.temp_slider.set_range(self.cfg["temperature_max"], self.cfg["temperature_min"])
            self.bright_slider.set_range(self.cfg["brightness_min"], 100)
            self._show_hotkey_status(app.hotkey_failures)
            self._update_unlock_button()
            self._update_status()

    def _update_unlock_button(self):
        if self.app.gamma_unlocked:
            self.unlock_btn.configure(text="Gamma range: unlocked", state="disabled",
                                      disabledforeground=GOOD)
        else:
            self.unlock_btn.configure(text="Unlock full gamma range (admin)", state="normal")

    def _update_status(self):
        app = self.app
        n = len(app.gamma.monitors)
        active = len(app.gamma.enabled_devices)
        parts = [f"{active}/{n} display{'s' if n != 1 else ''} active"]
        if not app.enabled:
            parts.append("paused")
        if app.limited:
            parts.append("driver limits range: overlay used for extra dimming, unlock in Settings")
        if app.hotkey_failures:
            parts.append("some hotkeys failed (see Settings)")
        self.status.configure(text=" · ".join(parts), fg=WARN if (app.limited or app.hotkey_failures) else MUTED)
