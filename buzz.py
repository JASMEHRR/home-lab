"""
Buzz! controllers (receiver 054C:0002) on Windows using only built-in HID APIs.

  py buzz.py          light test, then show button presses for 30 s
  py buzz.py 60       same, listening for 60 s
  pyw buzz.py --lights  Buzz Lights window: any press lights that player's buzzer
                        for 2 s (works alongside other apps). Close the window, or
                        run it again, to stop it.

Pressing a button lights that player's red buzzer while held.
"""
import ctypes
import ctypes.wintypes as w
import sys
import threading
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
hid = ctypes.WinDLL("hid")
k32.CreateFileW.restype = w.HANDLE
k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
for _name in ("CreateMutexW", "CreateEventW", "OpenEventW"):
    getattr(k32, _name).restype = w.HANDLE
k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k32.SetEvent.argtypes = [w.HANDLE]
k32.CloseHandle.argtypes = [w.HANDLE]
INVALID = w.HANDLE(-1).value
HOLD = 2.0  # seconds a buzzer stays lit after a press in --lights mode

COLORS = ["RED (buzz)", "YELLOW", "GREEN", "ORANGE", "BLUE"]  # bit order per player


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", w.USHORT), ("UsagePage", w.USHORT),
                ("InputReportByteLength", w.USHORT), ("OutputReportByteLength", w.USHORT),
                ("FeatureReportByteLength", w.USHORT), ("Reserved", w.USHORT * 17),
                ("Counts", w.USHORT * 10)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD), ("Data4", ctypes.c_ubyte * 8)]


GUID_DEVINTERFACE_HID = GUID(0x4D1E55B2, 0xF16F, 0x11CF, (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))
cfg = ctypes.WinDLL("cfgmgr32")


def find_path():
    """Ask Windows for present HID interfaces and return the Buzz receiver's path."""
    size = w.ULONG()
    cfg.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(GUID_DEVINTERFACE_HID), None, 0)
    buf = ctypes.create_unicode_buffer(size.value)
    cfg.CM_Get_Device_Interface_ListW(ctypes.byref(GUID_DEVINTERFACE_HID), None, buf, size.value, 0)
    for path in buf[:size.value].split("\0"):
        if "vid_054c&pid_0002" in path.lower():
            return path
    raise SystemExit("Buzz receiver not found as a HID game controller.")


class Buzz:
    def __init__(self):
        path = find_path()
        # Two handles: synchronous I/O on one handle is serialized, so a blocked
        # ReadFile would otherwise stall every light write.
        self.h = self._open(path)   # writes (lights)
        self.rh = self._open(path)  # reads (buttons)
        pp = ctypes.c_void_p()
        hid.HidD_GetPreparsedData(self.h, ctypes.byref(pp))
        caps = HIDP_CAPS()
        hid.HidP_GetCaps(pp, ctypes.byref(caps))
        hid.HidD_FreePreparsedData(pp)
        self.in_len, self.out_len = caps.InputReportByteLength, caps.OutputReportByteLength
        self.lock = threading.Lock()

    @staticmethod
    def _open(path):
        h = k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)  # R/W, share R/W, OPEN_EXISTING
        if h in (None, INVALID):
            raise SystemExit(f"Could not open Buzz receiver (error {ctypes.get_last_error()}). "
                             "Is another program using it?")
        return h

    def lights(self, on):
        """on: 4 bools, one per player."""
        buf = bytearray(self.out_len)  # [report id 0, 0, p1, p2, p3, p4, 0, 0]
        for i, v in enumerate(on):
            buf[2 + i] = 0xFF if v else 0x00
        n = w.DWORD()
        with self.lock:
            ok = k32.WriteFile(self.h, bytes(buf), len(buf), ctypes.byref(n), None)
        if not ok:
            print("light write failed, error", ctypes.get_last_error())

    def read_buttons(self):
        """Blocks until a report arrives; returns a 20-bit button mask."""
        buf = ctypes.create_string_buffer(self.in_len)
        n = w.DWORD()
        if not k32.ReadFile(self.rh, buf, self.in_len, ctypes.byref(n), None):
            return None
        d = buf.raw[:n.value]  # [report id, 0x7f, 0x7f, b0, b1, b2]
        return d[3] | d[4] << 8 | (d[5] & 0x0F) << 16

    def close(self):
        k32.CloseHandle(self.h)
        k32.CloseHandle(self.rh)


def serve():
    """Buzz Lights with a small taskbar window. A second launch tells the running one to stop."""
    k32.CreateMutexW(None, False, "Local\\BuzzLightsRunning")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS -> already running, ask it to stop
        ev = k32.OpenEventW(0x0002, False, "Local\\BuzzLightsStop")  # EVENT_MODIFY_STATE
        if ev:
            k32.SetEvent(ev)
        print("Buzz Lights stopped.")
        return
    stop = k32.CreateEventW(None, True, False, "Local\\BuzzLightsStop")
    state = {"connected": False, "lit": [False] * 4}
    engine = threading.Thread(target=lights_engine, args=(stop, state), daemon=True)
    engine.start()
    window(stop, state, engine)


def window(stop, state, engine):
    import tkinter as tk
    # own taskbar identity, so it shows as "Buzz Lights" with its own icon instead of Python's
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BuzzLights.Helper")
    root = tk.Tk()
    root.title("Buzz Lights")
    root.resizable(False, False)
    icon = tk.PhotoImage(width=32, height=32)  # red buzzer dot
    for y in range(32):
        for x in range(32):
            d = (x - 15.5) ** 2 + (y - 15.5) ** 2
            if d <= 14 ** 2:
                icon.put("#e01010" if d <= 11 ** 2 else "#7a0000", (x, y))
    root.iconphoto(True, icon)

    status = tk.Label(root, font=("Segoe UI", 11, "bold"))
    status.pack(padx=16, pady=(12, 6))
    cv = tk.Canvas(root, width=280, height=76, highlightthickness=0)
    cv.pack(padx=16)
    dots = []
    for p in range(4):
        x = 35 + p * 70
        dots.append(cv.create_oval(x - 22, 6, x + 22, 50, fill="#3a0000", outline="#222", width=2))
        cv.create_text(x, 64, text=f"Player {p + 1}", font=("Segoe UI", 9))
    tk.Label(root, text="Close this window to turn Buzz Lights off.",
             font=("Segoe UI", 9), fg="#666").pack(pady=(4, 12))

    def close():
        k32.SetEvent(stop)
        engine.join(1.0)  # lets the engine switch the real lights off
        root.destroy()

    def tick():
        if not engine.is_alive():  # stopped by clicking the shortcut again
            root.destroy()
            return
        if state["connected"]:
            status.config(text="ON: press a button to light it", fg="#1a7f37")
        else:
            status.config(text="Waiting for the Buzz receiver... plug it in", fg="#888888")
        for p, dot in enumerate(dots):
            cv.itemconfig(dot, fill="#ff2020" if state["lit"][p] else "#3a0000")
        root.after(100, tick)

    root.protocol("WM_DELETE_WINDOW", close)
    tick()
    root.mainloop()


def lights_engine(stop, state):
    def stopped(secs):
        return k32.WaitForSingleObject(stop, int(secs * 1000)) == 0

    print("Buzz Lights running.")
    waiting_logged = False
    while True:
        try:
            b = Buzz()
        except SystemExit as e:  # receiver unplugged: keep waiting for it
            if not waiting_logged:
                print("waiting:", e)
                waiting_logged = True
            if stopped(2):
                return
            continue
        waiting_logged = False
        state["connected"] = True
        print(f"opened receiver (in {b.in_len}, out {b.out_len})")
        for on in (True, False, True, False):  # "I'm on" double flash
            b.lights([on] * 4)
            time.sleep(0.2)

        held, last_press, alive = [0], [0.0] * 4, [True]

        def reader():
            prev = 0
            while True:
                m = b.read_buttons()
                if m is None:
                    alive[0] = False
                    return
                now = time.monotonic()
                for p in range(4):
                    if (m >> 5 * p) & 0x1F and not (prev >> 5 * p) & 0x1F:
                        last_press[p] = now
                        print(f"press player {p + 1} (mask {m:05x})")
                held[0] = prev = m

        threading.Thread(target=reader, daemon=True).start()
        shown = None
        while alive[0]:
            if stopped(0.05):
                # no b.close() here: closing a handle with a blocked ReadFile waits for the
                # next button press; the process is exiting anyway.
                b.lights([False] * 4)
                return
            now = time.monotonic()
            want = [bool((held[0] >> 5 * p) & 0x1F) or now - last_press[p] < HOLD for p in range(4)]
            if want != shown:
                b.lights(want)
                state["lit"] = shown = want
        b.close()
        state["connected"], state["lit"] = False, [False] * 4
        print("Receiver disconnected, waiting for it...")


def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    b = Buzz()
    print(f"Opened Buzz receiver (input {b.in_len} bytes, output {b.out_len} bytes)")

    print("Light test: players 1-4 in turn, then all")
    for p in range(4):
        b.lights([i == p for i in range(4)])
        time.sleep(0.6)
    b.lights([True] * 4)
    time.sleep(0.8)
    b.lights([False] * 4)

    print(f"Press buttons now (listening {secs} s)...")
    state = {"mask": 0, "presses": 0}

    def reader():
        last = 0
        while True:
            m = b.read_buttons()
            if m is None:
                return
            for bit in range(20):
                if m & (1 << bit) and not last & (1 << bit):
                    state["presses"] += 1
                    print(f"  Player {bit // 5 + 1}: {COLORS[bit % 5]}", flush=True)
            state["mask"] = last = m
            b.lights([bool(m >> (5 * p) & 0x1F) for p in range(4)])

    threading.Thread(target=reader, daemon=True).start()
    time.sleep(secs)
    b.lights([False] * 4)
    print(f"Done. {state['presses']} presses seen.")


if __name__ == "__main__":
    if sys.stdout is None:  # started from a shortcut (pythonw): log next to the script
        import os
        logpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buzz.log")
        big = os.path.exists(logpath) and os.path.getsize(logpath) > 200_000  # keep the log small
        sys.stdout = sys.stderr = open(logpath, "w" if big else "a", buffering=1)
        print(time.strftime("--- %Y-%m-%d %H:%M:%S"), sys.argv[1:])
    serve() if "--lights" in sys.argv else main()
