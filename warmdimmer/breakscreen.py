"""
The full-screen prompt shown while a break is running.

Deliberately calm: a dark ground, one line of text, a countdown ring, and two
ways out.  It covers every monitor so the message is wherever you are looking,
and it never takes focus away from what you were typing into - it is a
reminder, not a lock screen.
"""
import tkinter as tk

from .breaks import format_left

BG = "#0d0f13"
TEXT = "#eceef2"
MUTED = "#8d93a1"
ACCENT = "#ffa94d"
RING_BG = "#252a35"


class BreakScreen:
    def __init__(self, root, on_skip, on_postpone, scale=1.0):
        self.root = root
        self.on_skip = on_skip
        self.on_postpone = on_postpone
        self.s = scale
        self.windows = []
        self.canvas = None
        self.visible = False

    def px(self, n):
        return int(round(n * self.s))

    # ------------------------------------------------------------- showing
    def show(self, monitors, message, total):
        if self.visible:
            return
        self.visible = True
        self.total = max(1, int(total))
        for i, m in enumerate(monitors):
            x, y, w, h = m.rect
            top = tk.Toplevel(self.root)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            top.configure(bg=BG)
            top.geometry(f"{w}x{h}+{x}+{y}")
            top.attributes("-alpha", 0.0)
            if i == 0:
                self._build(top, w, h, message)
            top.bind("<Escape>", lambda e: self.on_skip())
            self.windows.append(top)
        # Without focus the Escape binding can never fire, and the prompt would
        # advertise a key that does nothing.  A break is the one moment the app
        # is meant to be in front, and it only ever appears when nothing is
        # running full screen, so taking focus here is safe.
        if self.windows:
            first = self.windows[0]
            first.focus_force()
            first.after(50, lambda: first.focus_set() if first.winfo_exists() else None)
        self._fade(0.0, 0.94)

    def _build(self, top, w, h, message):
        c = tk.Canvas(top, bg=BG, highlightthickness=0, width=w, height=h)
        c.pack(fill="both", expand=True)
        self.canvas = c
        cx, cy = w / 2, h / 2 - self.px(30)
        r = self.px(78)
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=RING_BG, width=self.px(9))
        self.arc = c.create_arc(cx - r, cy - r, cx + r, cy + r, start=90, extent=-359.9,
                                style="arc", outline=ACCENT, width=self.px(9))
        self.count = c.create_text(cx, cy, text="", fill=TEXT,
                                   font=("Segoe UI", self.px(30), "bold"))
        c.create_text(cx, cy + r + self.px(46), text=message, fill=TEXT,
                      font=("Segoe UI", self.px(17)))
        c.create_text(cx, cy + r + self.px(78),
                      text="Look at something far away and let your eyes relax",
                      fill=MUTED, font=("Segoe UI", self.px(11)))

        by = cy + r + self.px(126)
        self._button(c, cx - self.px(88), by, "Skip", self.on_skip)
        self._button(c, cx + self.px(88), by, "Postpone 5 min", self.on_postpone)
        c.create_text(cx, by + self.px(52), text="Esc skips",
                      fill=MUTED, font=("Segoe UI", self.px(9)))

    def _button(self, c, x, y, label, command):
        w, h = self.px(150), self.px(38)
        rect = c.create_rectangle(x - w / 2, y - h / 2, x + w / 2, y + h / 2,
                                  fill=RING_BG, outline=RING_BG)
        text = c.create_text(x, y, text=label, fill=TEXT, font=("Segoe UI", self.px(11), "bold"))
        for item in (rect, text):
            c.tag_bind(item, "<Button-1>", lambda e: command())
            c.tag_bind(item, "<Enter>", lambda e: (c.itemconfigure(rect, fill=ACCENT, outline=ACCENT),
                                                   c.itemconfigure(text, fill="#1a1206"),
                                                   c.configure(cursor="hand2")))
            c.tag_bind(item, "<Leave>", lambda e: (c.itemconfigure(rect, fill=RING_BG, outline=RING_BG),
                                                   c.itemconfigure(text, fill=TEXT),
                                                   c.configure(cursor="")))

    def _fade(self, start, end, step=0):
        steps = 12
        if step > steps or not self.windows:
            return
        a = start + (end - start) * (step / steps)
        for top in self.windows:
            try:
                top.attributes("-alpha", a)
            except tk.TclError:
                return
        self.root.after(16, lambda: self._fade(start, end, step + 1))

    # ------------------------------------------------------------ updating
    def update(self, seconds_left):
        if not self.visible or self.canvas is None:
            return
        try:
            self.canvas.itemconfigure(self.count, text=format_left(seconds_left))
            frac = max(0.0, min(1.0, seconds_left / self.total))
            self.canvas.itemconfigure(self.arc, extent=-359.9 * frac)
            for top in self.windows:
                top.attributes("-topmost", True)
        except tk.TclError:
            pass

    def hide(self):
        self.visible = False
        for top in self.windows:
            try:
                top.destroy()
            except tk.TclError:
                pass
        self.windows = []
        self.canvas = None
