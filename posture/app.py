"""
Posture Coach: the desktop app. Live camera view with posture and focus, today's numbers and charts,
settings, a Record button, and the glowing screen alert.

  py app.py                 open the app
  py app.py --no-side       laptop webcam only
  py app.py --seconds 30    close itself after 30 s (for testing)
  py app.py --demo-alert    show the glow alert 4 s after opening (for testing)
  py app.py --tab today     open on the Today (or settings) tab
"""
import json
import os
import sys
import time
import tkinter as tk
import webbrowser
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SETTINGS_PATH = os.path.join(HERE, "settings.json")
APP_ID = "PostureTracker.App"        # matches the taskbar shortcut, so the window groups under its icon

BG, CARD, LINE, TEXT, MUTED = "#0b1020", "#151d36", "#26305a", "#eef2ff", "#9fb0da"
ACCENT, GOOD, BAD, WARN, AWAY = "#4d8dff", "#4fd18b", "#ff6b6b", "#ffd166", "#6b7390"
FONT, FONT_BOLD = ("Segoe UI", 10), ("Segoe UI", 10, "bold")

POSTURE_COLOR = {"good": GOOD, "bad": BAD, "unsure": WARN, "away": AWAY, "starting": AWAY}
FOCUS_COLOR = {"focused": GOOD, "writing": GOOD, "phone": BAD, "leaning_back": BAD, "looking_away": WARN, "drowsy": BAD,
               "no_face": AWAY, "unknown": AWAY}
FOCUS_TEXT = {"focused": "Studying", "writing": "Studying (writing)", "phone": "On the phone: not studying",
              "leaning_back": "Sitting back: not studying", "looking_away": "Looking away: not studying", "drowsy": "Sleepy",
              "no_face": "No face seen", "unknown": "Activity needs the laptop webcam"}
GLOW_REPEAT = 8.0            # seconds between beeps while the glow stays on
SENSITIVITY_LABELS = (("low", "Relaxed"), ("medium", "Normal"), ("high", "Strict"))


def load_settings(defaults):
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        return dict(defaults, **{k: saved[k] for k in defaults if k in saved})
    except FileNotFoundError:
        return dict(defaults)
    except (OSError, ValueError, TypeError) as e:
        print("ignoring a damaged settings file:", e)
        return dict(defaults)


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError as e:
        print("could not save settings:", e)


def clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def hour_label(h):
    return f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}"


def make_icon():
    icon = tk.PhotoImage(width=32, height=32)
    for y in range(32):
        for x in range(32):
            d = (x - 15.5) ** 2 + (y - 15.5) ** 2
            if d <= 15 ** 2:
                icon.put("#4d8dff", (x, y))
            if (x - 15.5) ** 2 + (y - 11) ** 2 <= 4.5 ** 2 or (y >= 19 and (x - 15.5) ** 2 / 100 + (y - 27) ** 2 / 64 <= 1 and d <= 15 ** 2):
                icon.put("#ffffff", (x, y))
    return icon


class App:
    def __init__(self, root, engine, glow, opts):
        import metrics
        import posture_report
        import stats
        self.metrics, self.posture_report, self.stats = metrics, posture_report, stats
        self.root, self.engine, self.glow, self.opts = root, engine, glow, opts
        self.settings = engine.settings
        self.photo = None
        self.last_t = None
        self.seen_alert = 0
        self.last_beep = 0.0
        self.today_at = 0.0
        self.started = time.time()
        self.demo_done = False

        for child in root.winfo_children():
            child.destroy()
        root.configure(bg=BG)
        self._header()
        self._tabs()
        self.show(opts["tab"])
        self.tick()

    # ------------------------------------------------------------ layout
    def _label(self, parent, text="", **kw):
        kw.setdefault("bg", parent["bg"])
        kw.setdefault("fg", TEXT)
        kw.setdefault("font", FONT)
        return tk.Label(parent, text=text, **kw)

    def _button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, font=FONT_BOLD, relief="flat", cursor="hand2",
                         bg=ACCENT if primary else "#222c4f", fg="#ffffff", activebackground="#2f6ae0",
                         activeforeground="#ffffff", padx=14, pady=8, bd=0)

    def _header(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=18, pady=(14, 6))
        self._label(bar, "Posture Coach", font=("Segoe UI", 18, "bold")).pack(side="left")
        self.focus_pill = tk.Label(bar, text="Focus", font=FONT_BOLD, fg="#06101f", bg=AWAY, padx=12, pady=4)
        self.focus_pill.pack(side="right", padx=(8, 0))
        self.posture_pill = tk.Label(bar, text="Starting...", font=FONT_BOLD, fg="#06101f", bg=AWAY, padx=12, pady=4)
        self.posture_pill.pack(side="right")

    def _tabs(self):
        self.tabbar = tk.Frame(self.root, bg=BG)
        self.tabbar.pack(fill="x", padx=18)
        self.tab_buttons, self.pages = {}, {}
        self.body = tk.Frame(self.root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=(6, 14))
        for key, title in (("live", "Live"), ("today", "Today"), ("settings", "Settings")):
            b = tk.Button(self.tabbar, text=title, font=FONT_BOLD, relief="flat", bd=0, padx=16, pady=7, cursor="hand2",
                          bg=BG, fg=MUTED, activebackground=CARD, activeforeground=TEXT, command=lambda k=key: self.show(k))
            b.pack(side="left")
            self.tab_buttons[key] = b
            self.pages[key] = tk.Frame(self.body, bg=BG)
        self._live_page(self.pages["live"])
        self._today_page(self.pages["today"])
        self._settings_page(self.pages["settings"])

    def show(self, key):
        for k, page in self.pages.items():
            page.pack_forget()
            self.tab_buttons[k].configure(bg=BG, fg=MUTED)
        self.pages[key].pack(fill="both", expand=True)
        self.tab_buttons[key].configure(bg=CARD, fg=TEXT)
        if key == "today":
            self.refresh_today()

    def _live_page(self, page):
        self.preview = tk.Label(page, bg="#000000", width=960, height=360)
        self.preview.pack(pady=(8, 8))
        row = tk.Frame(page, bg=BG)
        row.pack(fill="x")
        self.cal_btn = self._button(row, "Calibrate   (sit up straight, look at the screen)", self.engine.calibrate, primary=True)
        self.cal_btn.pack(side="left")
        self.rec_btn = self._button(row, "Record video", self.toggle_record)
        self.rec_btn.pack(side="left", padx=8)
        self.pause_btn = self._button(row, "Pause tracking", self.toggle_pause)
        self.pause_btn.pack(side="left")
        self.second_btn = self._button(row, "Set other laptop as study screen", self.engine.calibrate_second)
        self.second_btn.pack(side="left", padx=8)
        self._button(row, "Test alert", lambda: self.fire_alert("Posture alert (test)")).pack(side="right")
        self.info = self._label(page, "", fg=MUTED, justify="left", anchor="w", wraplength=940)
        self.info.pack(fill="x", pady=(10, 0))
        self.values = self._label(page, "", fg=MUTED, justify="left", anchor="w", font=("Consolas", 9))
        self.values.pack(fill="x", pady=(6, 0))

    def _today_page(self, page):
        tiles = tk.Frame(page, bg=BG)
        tiles.pack(fill="x", pady=(8, 8))
        self.tile_vars = {}
        for i, (key, title) in enumerate((("desk", "AT THE DESK"), ("posture", "GOOD POSTURE"), ("focus", "STUDYING"), ("breaks", "BREAKS OF 5+ MIN"))):
            card = tk.Frame(tiles, bg=CARD, highlightbackground=LINE, highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            tiles.columnconfigure(i, weight=1)
            big, note = tk.StringVar(value="n/a"), tk.StringVar(value="")
            tk.Label(card, textvariable=big, bg=CARD, fg=TEXT, font=("Segoe UI", 20, "bold"), anchor="w").pack(fill="x", padx=12, pady=(10, 0))
            tk.Label(card, text=title, bg=CARD, fg=ACCENT, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=12)
            tk.Label(card, textvariable=note, bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=190).pack(fill="x", padx=12, pady=(0, 10))
            self.tile_vars[key] = (big, note)
        self._label(page, "Timeline (green good, red slouching, amber not sure, grey away)", fg=MUTED).pack(anchor="w")
        self.timeline = tk.Canvas(page, width=940, height=26, bg=BG, highlightthickness=0)
        self.timeline.pack(pady=(2, 8))
        self._label(page, "Hour by hour (minutes at the desk)", fg=MUTED).pack(anchor="w")
        self.hourly = tk.Canvas(page, width=940, height=170, bg=BG, highlightthickness=0)
        self.hourly.pack(pady=(2, 8))
        bottom = tk.Frame(page, bg=BG)
        bottom.pack(fill="x")
        self.detail = self._label(bottom, "", fg=MUTED, justify="left", anchor="w")
        self.detail.pack(side="left", fill="x", expand=True)
        self._button(bottom, "Open full report", self.open_report).pack(side="right")

    def _settings_page(self, page):
        s = self.settings
        card = tk.Frame(page, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x", pady=(10, 0))
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=18, pady=16)
        self._label(inner, "How strict should posture checking be?", font=FONT_BOLD, bg=CARD).grid(row=0, column=0, sticky="w")
        self.sens_var = tk.StringVar(value=s["sensitivity"])
        row = tk.Frame(inner, bg=CARD)
        row.grid(row=1, column=0, sticky="w", pady=(4, 14))
        for value, label in SENSITIVITY_LABELS:
            tk.Radiobutton(row, text=label, value=value, variable=self.sens_var, command=self.on_settings, bg=CARD, fg=TEXT,
                           selectcolor=BG, activebackground=CARD, activeforeground=TEXT, font=FONT).pack(side="left", padx=(0, 16))
        self._label(inner, "Start the glow after this many seconds of bad posture (it stays until you sit right)", font=FONT_BOLD, bg=CARD).grid(row=2, column=0, sticky="w")
        self.after_var = tk.IntVar(value=int(s["glow_after"]))
        tk.Spinbox(inner, from_=1, to=120, increment=1, width=6, textvariable=self.after_var, command=self.on_settings,
                   bg=BG, fg=TEXT, buttonbackground=CARD, insertbackground=TEXT, font=FONT).grid(row=3, column=0, sticky="w", pady=(4, 14))
        self.glow_var, self.sound_var = tk.BooleanVar(value=bool(s["glow"])), tk.BooleanVar(value=bool(s["sound"]))
        for var, text, r in ((self.glow_var, "Glowing alert on the screen", 4), (self.sound_var, "Beep every few seconds while the glow is on", 5)):
            tk.Checkbutton(inner, text=text, variable=var, command=self.on_settings, bg=CARD, fg=TEXT, selectcolor=BG,
                           activebackground=CARD, activeforeground=TEXT, font=FONT).grid(row=r, column=0, sticky="w")
        self._label(page, "Your numbers (no video) are saved in:\n" + self.engine.DATA_DIR + "\nRecorded videos go in:\n" + self.engine.CAPTURE_DIR,
                    fg=MUTED, justify="left", anchor="w").pack(fill="x", pady=(14, 0))

    # ------------------------------------------------------------ actions
    def on_settings(self):
        try:
            after = max(1, min(120, int(self.after_var.get())))
        except (tk.TclError, ValueError):
            after = int(self.settings["glow_after"])
        self.settings.update(sensitivity=self.sens_var.get(), glow_after=after, glow=self.glow_var.get(), sound=self.sound_var.get())
        save_settings(self.settings)

    def toggle_record(self):
        self.engine.set_recording(not self.engine.snapshot().get("recording", False))

    def toggle_pause(self):
        self.engine.paused = not self.engine.paused

    def beep(self):
        if self.settings["sound"]:
            try:
                import winsound
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
            except (ImportError, RuntimeError):
                self.root.bell()

    def fire_alert(self, text):
        """A short glow (4 s): the test button and the sleepy warning."""
        if self.settings["glow"]:
            self.glow.show("Posture alert", text, 4.0)
        self.beep()

    def follow_posture(self, snap):
        """
        The glow stays on for as long as the posture is bad and fades when it is fixed. It starts
        after `glow_after` seconds of bad posture, then beeps every few seconds.
        """
        if not self.settings["glow"]:
            if self.glow.staying:
                self.glow.release()
            return
        bad = snap.get("state") == "bad" and not snap.get("paused")
        bad_for = snap.get("bad_for", 0.0)
        detail = ", ".join(self._reason(r) for r in snap.get("reasons", ()))
        if bad and bad_for >= float(self.settings["glow_after"]):
            if not self.glow.staying:
                self.glow.show("Sit up", detail, None)
                self.beep()
                self.last_beep = time.time()
            else:
                self.glow.set_detail(detail)
                if time.time() - self.last_beep >= GLOW_REPEAT:
                    self.beep()
                    self.last_beep = time.time()
        elif not bad and self.glow.staying:
            self.glow.release()

    def open_report(self):
        try:
            self.posture_report.main(["--days", "7"])
        except SystemExit as e:
            self.detail.configure(text=str(e))

    # ------------------------------------------------------------ live updates
    def tick(self):
        try:
            self.update(self.engine.snapshot())
        except Exception as e:        # a display hiccup must not stop the app
            print("display error:", type(e).__name__, e)
        if self.opts["seconds"] is not None and time.time() - self.started >= self.opts["seconds"]:
            self.close()
            return
        if self.opts["demo_alert"] and not self.demo_done and time.time() - self.started >= 4:
            self.demo_done = True
            self.fire_alert("head too far forward, leaning toward the screen")
        self.root.after(100, self.tick)

    def update(self, snap):
        status = snap.get("status")
        state, focus = snap.get("state", "away"), snap.get("focus", "no_face")
        if status == "error":
            self.posture_pill.configure(text="Error", bg=BAD)
            self.info.configure(text=snap.get("error", "The engine stopped."), fg=BAD)
            return
        reasons = ", ".join(self._reason(r) for r in snap.get("reasons", ()))
        text = {"good": "Good posture", "bad": "Slouching" + (": " + reasons if reasons else ""),
                "unsure": "At desk", "away": "Away from desk"}.get(state, "Starting...")
        if snap.get("paused"):
            text = "Paused"
        self.posture_pill.configure(text=text, bg=POSTURE_COLOR.get(state, AWAY))
        self.focus_pill.configure(text=FOCUS_TEXT.get(focus, focus), bg=FOCUS_COLOR.get(focus, AWAY))

        preview = snap.get("preview")
        if preview is not None and snap.get("t") != self.last_t:
            self.last_t = snap.get("t")
            rgb = preview[:, :, ::-1].copy()
            h, w = rgb.shape[:2]
            self.photo = tk.PhotoImage(data=b"P6 %d %d 255\n" % (w, h) + rgb.tobytes(), format="PPM")
            self.preview.configure(image=self.photo, width=w, height=h)

        recording = snap.get("recording", False)
        self.rec_btn.configure(text=f"Stop recording  {int(snap.get('record_seconds', 0))}s" if recording else "Record video",
                               bg=BAD if recording else "#222c4f")
        self.pause_btn.configure(text="Resume tracking" if snap.get("paused") else "Pause tracking")
        left = snap.get("calibrating_left", 0.0)
        self.cal_btn.configure(text=f"Calibrating... sit up straight and look at the screen  {left:.0f}" if left else
                               "Calibrate   (sit up straight, look at the screen)", state="disabled" if left else "normal")
        self.second_btn.configure(text=f"Look at the other laptop...  {left:.0f}" if left else "Set other laptop as study screen",
                                  state="disabled" if left else "normal")

        lines = []
        if snap.get("message"):
            lines.append(snap["message"])
        if not os.path.exists(self.engine.CAL_PATH):
            lines.append("First time: sit up straight, look at your screen, then press Calibrate. Until then I use average numbers and may be wrong.")
        lines += snap.get("notes", [])
        self.info.configure(text="\n".join(lines), fg=WARN if lines else MUTED)

        def val(v, fmt="{:.0f}"):
            return "-" if v is None else fmt.format(v)

        self.values.configure(text="Head forward   front %s   side %s     Face turn %s     Head down %s     Eyes closed %s" % (
            val(snap.get("head_front")), val(snap.get("head_side")), val(snap.get("yaw")), val(snap.get("pitch_down")),
            val(snap.get("blink"), "{:.0%}")))

        self.follow_posture(snap)
        alert_id = snap.get("alert_id", 0)
        if alert_id != self.seen_alert:
            self.seen_alert = alert_id
            self.fire_alert(snap.get("alert_text", ""))
        if time.time() - self.today_at > 10 and str(self.pages["today"].winfo_manager()):
            self.refresh_today()

    @staticmethod
    def _reason(r):
        return {"forward_head": "head forward", "shoulder_tilt": "shoulders uneven", "leaning_in": "leaning in"}.get(r, r)

    # ------------------------------------------------------------ today tab
    def refresh_today(self):
        self.today_at = time.time()
        rows, s = self.stats.today(self.engine.DATA_DIR)
        dur = self.posture_report.duration
        if not s["tracked"]:
            for big, note in self.tile_vars.values():
                big.set("n/a")
                note.set("")
            self.tile_vars["desk"][1].set("Nothing tracked yet today")
            self.timeline.delete("all")
            self.hourly.delete("all")
            self.detail.configure(text="")
            return
        self.tile_vars["desk"][0].set(dur(s["at_desk"]))
        self.tile_vars["desk"][1].set(f"{clock(s['first'])} to {clock(s['last'])}")
        self.tile_vars["posture"][0].set("n/a" if s["good_pct"] is None else f"{s['good_pct']:.0f}%")
        self.tile_vars["posture"][1].set(f"{dur(s['good'])} good, {dur(s['bad'])} slouching")
        self.tile_vars["focus"][0].set("n/a" if s["focus_pct"] is None else f"{s['focus_pct']:.0f}%")
        f = s["focus"]
        self.tile_vars["focus"][1].set(f"{dur(s['studying'])} studying (screen and writing)")
        self.tile_vars["breaks"][0].set(str(s["breaks"]))
        self.tile_vars["breaks"][1].set(f"longest stretch {dur(s['longest'])}")
        self._draw_timeline(rows)
        self._draw_hourly(s["hourly"])
        parts = [f"{self.posture_report.REASON_TEXT.get(k, k)}: {dur(v)}" for k, v in s["reasons"].most_common()]
        not_studying = [(name, f[key]) for name, key in (("Phone", "phone"), ("Sitting back", "leaning_back"),
                                                         ("Looking away", "looking_away"), ("Sleepy", "drowsy")) if f[key]]
        lines = ["Not studying:  " + ("   ".join(f"{n} {dur(v)}" for n, v in not_studying) or "nothing"),
                 "Writing time counted as studying: " + dur(f["writing"])]
        extra = f"     Worst hour: {hour_label(s['worst_hour'][0])}" if s["worst_hour"] else ""
        lines.append("Slouching caused by: " + (", ".join(parts) or "nothing") + extra)
        self.detail.configure(text="\n".join(lines))

    def _draw_timeline(self, rows):
        c = self.timeline
        c.delete("all")
        code = {"away": 1, "unsure": 2, "good": 3, "bad": 4}
        colors = ["#1d2440", "#6b7390", WARN, GOOD, BAD]
        minute = [0] * 1440
        for when, state, _, _ in rows:
            i = when.hour * 60 + when.minute
            minute[i] = max(minute[i], code[state])
        w, start = 940, 0
        for i in range(1, 1441):
            if i == 1440 or minute[i] != minute[start]:
                c.create_rectangle(start * w / 1440, 2, i * w / 1440, 18, fill=colors[minute[start]], width=0)
                start = i
        for h in range(0, 24, 3):
            c.create_text(h * w / 24 + 2, 22, text=hour_label(h), fill=MUTED, font=("Segoe UI", 7), anchor="w")

    def _draw_hourly(self, hourly):
        c = self.hourly
        c.delete("all")
        w, h, pad_l, pad_b = 940, 170, 30, 20
        step, top = (w - pad_l) / 24, 8
        scale = (h - pad_b - top) / 60
        for minutes in (0, 30, 60):
            y = h - pad_b - minutes * scale
            c.create_line(pad_l, y, w, y, fill=LINE)
            c.create_text(pad_l - 4, y, text=str(minutes), fill=MUTED, font=("Segoe UI", 7), anchor="e")
        for hour in range(24):
            g, b = hourly.get(hour, (0, 0))
            x0, x1 = pad_l + hour * step + 3, pad_l + (hour + 1) * step - 3
            base = h - pad_b
            gm, bm = min(60, g / 60), min(60 - min(60, g / 60), b / 60)
            if gm:
                c.create_rectangle(x0, base - gm * scale, x1, base, fill=GOOD, width=0)
            if bm:
                c.create_rectangle(x0, base - (gm + bm) * scale, x1, base - gm * scale, fill=BAD, width=0)
            if hour % 3 == 0:
                c.create_text(x0, h - 6, text=hour_label(hour), fill=MUTED, font=("Segoe UI", 7), anchor="w")

    # ------------------------------------------------------------ closing
    def close(self):
        self.glow.hide()
        self.engine.stop()
        self.engine.join(8)
        self.root.destroy()


def main():
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    argv = sys.argv[1:]
    opts = {"seconds": None, "demo_alert": "--demo-alert" in argv, "side": "--no-side" not in argv, "tab": "live"}
    if "--tab" in argv:
        opts["tab"] = argv[argv.index("--tab") + 1] if argv.index("--tab") + 1 < len(argv) else ""
        if opts["tab"] not in ("live", "today", "settings"):
            raise SystemExit("--tab must be live, today or settings")
    if "--seconds" in argv:
        try:
            opts["seconds"] = float(argv[argv.index("--seconds") + 1])
        except (IndexError, ValueError):
            raise SystemExit("--seconds needs a number")

    root = tk.Tk()
    root.title("Posture Coach")
    root.geometry("1000x790")
    root.minsize(1000, 790)
    root.configure(bg=BG)
    root.iconphoto(True, make_icon())
    tk.Label(root, text="Starting Posture Coach...", bg=BG, fg=TEXT, font=("Segoe UI", 14)).pack(expand=True)
    root.update()

    import engine as engine_module           # slow to load (the pose and face models), so after the window is up
    import overlay
    settings = load_settings(engine_module.DEFAULT_SETTINGS)
    eng = engine_module.Engine(settings, use_side=opts["side"])
    eng.DATA_DIR, eng.CAPTURE_DIR, eng.CAL_PATH = engine_module.DATA_DIR, engine_module.CAPTURE_DIR, engine_module.CAL_PATH
    eng.start()
    app = App(root, eng, overlay.GlowAlert(root), opts)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    no_console = sys.stdout is None
    if no_console:          # started from a shortcut (pythonw): keep a log file and show errors in a box
        sys.stdout = sys.stderr = open(os.path.join(HERE, "coach.log"), "w", buffering=1)
    try:
        main()
    except SystemExit as e:
        if no_console and isinstance(e.code, str):
            __import__("ctypes").windll.user32.MessageBoxW(None, e.code, "Posture Coach", 0x10)
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        if no_console:
            __import__("ctypes").windll.user32.MessageBoxW(None, f"Posture Coach stopped with an error:\n{e}\n\nDetails are in coach.log.", "Posture Coach", 0x10)
        raise
