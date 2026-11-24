"""
The alert glow: a see-through, click-through layer over the whole screen with a pulsing red glow around
the edges, HUD corner brackets and a short message. It never takes focus and you can click through it.
Windows only (uses the layered-window styles).
"""
import ctypes
import math
import tkinter as tk

KEY = "#010101"                       # this exact colour is see-through
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80000, 0x80, 0x08000000
FRAME_MS = 40
RINGS = 26                            # steps in the glow
HUD_FONT = ("Consolas", 30, "bold")
SUB_FONT = ("Consolas", 16)


def _rgb(r, g, b):
    return "#%02x%02x%02x" % (max(2, min(255, int(r))), max(2, min(255, int(g))), max(2, min(255, int(b))))


class GlowAlert:
    def __init__(self, root):
        self.root = root
        self.win = None
        self.canvas = None
        self._job = None
        self._title = self._detail = ""
        self._seconds = None
        self._released_at = None
        self._elapsed = 0.0

    def show(self, title, detail="", seconds=4.0):
        """
        Shows the glow for `seconds`; with seconds=None it stays until release() or hide() is
        called. A new call replaces one already showing.
        """
        self.hide()
        self._title, self._detail, self._seconds, self._released_at = title, detail, seconds, None
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=KEY)
        win.attributes("-transparentcolor", KEY)
        w, h = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{w}x{h}+0+0")
        canvas = tk.Canvas(win, width=w, height=h, bg=KEY, highlightthickness=0, bd=0)
        canvas.pack()
        win.update_idletasks()
        self._make_click_through(win)
        self.win, self.canvas = win, canvas
        self._animate(0.0, w, h)

    @property
    def staying(self):
        """True while a stay-until-released glow is on screen and not yet fading out."""
        return self.win is not None and self._seconds is None and self._released_at is None

    def set_detail(self, detail):
        self._detail = detail

    def release(self):
        """Fades a stay-until-released glow out (the posture was corrected)."""
        if self.staying:
            self._released_at = self._elapsed

    def hide(self):
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
        if self.win is not None:
            self.win.destroy()
            self.win = self.canvas = None

    @staticmethod
    def _make_click_through(win):
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)

    def _animate(self, elapsed, w, h):
        canvas = self.canvas
        if canvas is None:
            return
        self._elapsed = elapsed
        seconds, title, detail = self._seconds, self._title, self._detail
        if seconds is not None and elapsed >= seconds:
            self.hide()
            return
        if seconds is not None:
            fade = min(1.0, elapsed / 0.25, (seconds - elapsed) / 0.6)        # quick fade in, slower fade out
        else:
            fade = min(1.0, elapsed / 0.25)
            if self._released_at is not None:
                fade *= max(0.0, 1.0 - (elapsed - self._released_at) / 0.6)
                if fade <= 0.0:
                    self.hide()
                    return
        pulse = (0.6 + 0.4 * math.sin(elapsed * 2 * math.pi / (0.9 if seconds is not None else 1.3))) * fade
        canvas.delete("all")
        for i in range(RINGS):                                                  # glow, bright at the edge
            f = (1 - i / RINGS) ** 1.7 * pulse
            canvas.create_rectangle(i * 2, i * 2, w - i * 2, h - i * 2, outline=_rgb(255 * f, 40 * f, 30 * f), width=2)
        accent = _rgb(255 * fade, 150 * fade, 40 * fade)
        arm, inset = 110, 34
        for x, y, dx, dy in ((inset, inset, 1, 1), (w - inset, inset, -1, 1), (inset, h - inset, 1, -1), (w - inset, h - inset, -1, -1)):
            canvas.create_line(x, y, x + dx * arm, y, fill=accent, width=3)
            canvas.create_line(x, y, x, y + dy * arm, fill=accent, width=3)
        cx = w // 2
        canvas.create_text(cx, 110, text=title.upper(), font=HUD_FONT, fill=accent)
        canvas.create_line(cx - 260, 142, cx + 260, 142, fill=accent, width=2)
        if detail:
            canvas.create_text(cx, 172, text=detail, font=SUB_FONT, fill=_rgb(255 * fade, 220 * fade, 200 * fade))
        self._job = self.root.after(FRAME_MS, self._animate, elapsed + FRAME_MS / 1000, w, h)
