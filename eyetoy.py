"""
EyeToy (PS2 USB camera, Sony 054C:0155, OV519 bridge + OV7648 sensor) viewer for Windows.

Needs: WinUSB installed on "EyeToy USB camera Namtai (Interface 0)" via Zadig,
       pip install libusb1 numpy opencv-python

Register sequences follow the Linux gspca ov519 driver (drivers/media/usb/gspca/ov519.c).

Usage:
  py eyetoy.py            live window (q / Esc to quit, s snapshot, r start/stop recording an mp4)
  py eyetoy.py --record 10   record the first 10 s to an mp4 and exit (video only, no sound)
                          (recordings and snapshots go in the captures folder next to this script)
  py eyetoy.py --snap 5   grab 5 frames into ./captures and exit (for testing)
  py eyetoy.py --qvga     320x240 instead of 640x480
  py eyetoy.py --simulate-stall   cut the stream once after the first frame (tests auto-recovery)

If no picture arrives for 5 s the stream restarts by itself (up to 3 times in a row), showing
"Reconnecting camera..." on the last picture; after that it stops with a message.
"""
import os
import sys
import time

# The camera libraries live here (pip install --target), where every launch can find them,
# including clicks on the taskbar pin.
LIBS = r"C:\Users\jasme\eyetoy-pylibs"
if os.path.isdir(LIBS) and LIBS not in sys.path:
    sys.path.insert(0, LIBS)

import cv2
import numpy as np
import usb1

VID, PID = 0x054C, 0x0155
IFACE = 0

# OV519 bridge registers
R51x_I2C_W_SID = 0x41
R51x_I2C_SADDR_3 = 0x42
R51x_I2C_SADDR_2 = 0x43
R51x_I2C_R_SID = 0x44
R51x_I2C_DATA = 0x45
R518_I2C_CTL = 0x47
R51x_SYS_RESET = 0x50
OV519_R51_RESET1 = 0x51
OV519_R54_EN_CLK1 = 0x54
OV519_GPIO_DATA_OUT0 = 0x71
OV519_GPIO_IO_CTRL0 = 0x72
OV519_R10_H_SIZE = 0x10
OV519_R11_V_SIZE = 0x11
OV519_R12_X_OFFSETL = 0x12
OV519_R13_X_OFFSETH = 0x13
OV519_R14_Y_OFFSETL = 0x14
OV519_R15_Y_OFFSETH = 0x15
OV519_R16_DIVIDER = 0x16
OV519_R20_DFR = 0x20
OV519_R25_FORMAT = 0x25

OV7xx0_SID = 0x42  # sensor I2C address

N_PKTS = 32   # iso packets per transfer (full-speed: 1 per ms)
N_XFERS = 10  # transfers kept in flight


class EyeToy:
    BUSY_MESSAGE = ("Could not open the camera ({}).\n\n"
                    "Most likely another EyeToy window is already open: close it and try again.\n"
                    "If none is open, unplug the EyeToy, plug it back in, and try again.")

    def __init__(self, ctx):
        # Windows lets only one program open the camera, so a second window fails here or at claim.
        try:
            self.h = ctx.openByVendorIDAndProductID(VID, PID)
        except usb1.USBError as e:
            raise SystemExit(self.BUSY_MESSAGE.format(e))
        if self.h is None:
            raise SystemExit("EyeToy not found. Is it plugged in?")
        try:
            self.h.claimInterface(IFACE)
        except usb1.USBError as e:
            raise SystemExit(self.BUSY_MESSAGE.format(e))
        self.h.setInterfaceAltSetting(IFACE, 0)
        self.sensor = None

    # ---- bridge register access (vendor request 1) ----
    def reg_w(self, reg, val):
        self.h.controlWrite(0x40, 1, 0, reg, bytes([val & 0xFF]), 500)

    def reg_r(self, reg, length=1):
        return self.h.controlRead(0xC0, 1, 0, reg, length, 500)[0]

    def reg_w_mask(self, reg, val, mask):
        old = self.reg_r(reg)
        self.reg_w(reg, (old & ~mask) | (val & mask))

    def write_regvals(self, pairs):
        for reg, val in pairs:
            self.reg_w(reg, val)

    # ---- sensor I2C through the bridge (OV518-style) ----
    def i2c_w(self, reg, val):
        self.reg_w(R51x_I2C_SADDR_3, reg)
        self.reg_w(R51x_I2C_DATA, val)
        self.reg_w(R518_I2C_CTL, 0x01)
        time.sleep(0.004)
        self.reg_r(R518_I2C_CTL, 8)

    def i2c_r(self, reg):
        self.reg_w(R51x_I2C_SADDR_2, reg)
        self.reg_w(R518_I2C_CTL, 0x03)
        self.reg_r(R518_I2C_CTL, 8)
        self.reg_w(R518_I2C_CTL, 0x05)
        self.reg_r(R518_I2C_CTL, 8)
        return self.reg_r(R51x_I2C_DATA)

    def i2c_w_mask(self, reg, val, mask):
        old = self.i2c_r(reg)
        self.i2c_w(reg, (old & ~mask) | (val & mask))

    def led(self, on):
        self.reg_w_mask(OV519_GPIO_DATA_OUT0, 1 if on else 0, 1)

    # ---- init ----
    def configure_bridge(self):
        self.write_regvals([
            (0x5A, 0x6D),               # enable system
            (0x53, 0x9B),               # don't enable the microcontroller
            (OV519_R54_EN_CLK1, 0xFF),  # enable jpeg
            (0x5D, 0x03),
            (0x49, 0x01),
            (0x48, 0x00),
            (OV519_GPIO_IO_CTRL0, 0xEE),  # LED pin output; bit 4 clear or detection fails
            (OV519_R51_RESET1, 0x0F),
            (OV519_R51_RESET1, 0x00),
            (0x22, 0x00),
        ])

    def detect_sensor(self):
        self.reg_w(R51x_I2C_W_SID, OV7xx0_SID)
        self.reg_w(R51x_I2C_R_SID, OV7xx0_SID + 1)
        self.i2c_w(0x12, 0x80)  # sensor reset
        time.sleep(0.15)
        for _ in range(10):
            hi, lo = self.i2c_r(0x1C), self.i2c_r(0x1D)
            if (hi, lo) == (0x7F, 0xA2):
                break
            self.i2c_w(0x12, 0x80)
            time.sleep(0.15)
            self.i2c_r(0x00)  # dummy read to sync I2C
        else:
            raise SystemExit(f"Sensor did not answer (manufacturer id {hi:02x}{lo:02x}, expected 7fa2).")
        com_i = self.i2c_r(0x29)
        pid_hi, pid_lo = self.i2c_r(0x0A), self.i2c_r(0x0B)
        print(f"Sensor found: OmniVision, product id {pid_hi:02x}{pid_lo:02x} (COM_I={com_i:02x})")
        if pid_hi != 0x76 or pid_lo not in (0x40, 0x48):
            print("  Warning: not an OV7640/OV7648; continuing with OV7648 settings anyway.")
        self.sensor = "OV7648"
        # norm_7640 init table
        self.i2c_w(0x12, 0x80)
        time.sleep(0.15)
        self.i2c_w(0x12, 0x14)

    def pick_alt(self):
        best = (0, 0, None)
        for s in self.h.getDevice().iterSettings():
            if s.getNumber() != IFACE:
                continue
            for ep in s:
                if ep.getAddress() & 0x80 and (ep.getAttributes() & 3) == 1:
                    mps = ep.getMaxPacketSize() & 0x7FF
                    if mps > best[1]:
                        best = (s.getAlternateSetting(), mps, ep.getAddress())
        if best[2] is None:
            raise SystemExit("No isochronous video endpoint found on interface 0.")
        return best

    def start(self, width, height, fps=15):
        qvga = width == 320
        self.write_regvals([
            (0x5D, 0x03), (0x53, 0x9F), (OV519_R54_EN_CLK1, 0x0F),
            (0xA2, 0x20), (0xA3, 0x18), (0xA4, 0x04), (0xA5, 0x28),
            (0x37, 0x00), (0x55, 0x02), (0x22, 0x1D), (0x17, 0x50),
            (0x37, 0x00), (0x40, 0xFF), (0x46, 0x00), (0x59, 0x04),
            (0xFF, 0x00),
        ])
        self.reg_w_mask(OV519_R20_DFR, 0x10, 0x10)  # 8-bit input for OV7640/7648
        self.reg_w(OV519_R10_H_SIZE, width >> 4)
        self.reg_w(OV519_R11_V_SIZE, height >> 3)
        self.reg_w(OV519_R12_X_OFFSETL, 0x01 if qvga else 0x00)
        for r in (OV519_R13_X_OFFSETH, OV519_R14_Y_OFFSETL, OV519_R15_Y_OFFSETH, OV519_R16_DIVIDER):
            self.reg_w(r, 0x00)
        self.reg_w(OV519_R25_FORMAT, 0x03)  # YUV422 -> JPEG
        self.reg_w(0x26, 0x00)

        # frame rate (gspca table for OV7640/7648)
        rates = {30: (0x0C, 0xFF, 0), 25: (0x0C, 0x1F, 0), 20: (0x0C, 0x1B, 0),
                 15: (0x04, 0xFF, 1), 10: (0x04, 0x1F, 1), 5: (0x04, 0x1B, 1)}
        a4, r23, clockdiv = rates.get(fps, rates[15])
        self.reg_w(0xA4, a4)
        self.reg_w(0x23, r23)

        # sensor mode regs
        self.i2c_w_mask(0x14, 0x20 if qvga else 0x00, 0x20)
        self.i2c_w_mask(0x28, 0x00 if qvga else 0x20, 0x20)
        self.i2c_w_mask(0x2D, 0x40 if qvga else 0x00, 0x40)
        self.i2c_w_mask(0x67, 0xF0 if qvga else 0x90, 0xF0)
        self.i2c_w_mask(0x74, 0x20 if qvga else 0x00, 0x20)
        self.i2c_w_mask(0x12, 0x04, 0x04)  # auto white balance
        self.i2c_w(0x11, clockdiv)

        # sensor window
        hwsbase = hwebase = 0x1A
        vwsbase = vwebase = 0x03
        hwscale, vwscale = (1, 0) if qvga else (2, 1)
        self.i2c_w(0x17, hwsbase)
        self.i2c_w(0x18, hwebase + (width >> hwscale))
        self.i2c_w(0x19, vwsbase)
        self.i2c_w(0x1A, vwebase + (height >> vwscale))

        # reset snapshot button state, then restart the bridge
        self.reg_w(R51x_SYS_RESET, 0x40)
        self.reg_w(R51x_SYS_RESET, 0x00)
        self.reg_w(OV519_R51_RESET1, 0x0F)
        self.reg_w(OV519_R51_RESET1, 0x00)
        self.reg_w(0x22, 0x1D)
        self.led(True)

    def halt(self):
        """Stops the bridge streaming and drops to the zero-bandwidth alt setting. Best effort."""
        try:
            self.reg_w(OV519_R51_RESET1, 0x0F)
            self.reg_w(OV519_R51_RESET1, 0x00)
            self.reg_w(0x22, 0x00)
            self.led(False)
            self.h.setInterfaceAltSetting(IFACE, 0)
        except usb1.USBError:
            pass  # the camera may already be gone; nothing more to undo

    def stop(self):
        self.halt()
        try:
            self.h.releaseInterface(IFACE)
        except usb1.USBError:
            pass

    def close(self):
        """Stops streaming and closes the connection (stop the Stream first)."""
        self.stop()
        try:
            self.h.close()
        except usb1.USBError:
            pass


class FrameAssembler:
    """Joins iso packets into JPEG frames using the OV519 16-byte SOF/EOF headers."""

    def __init__(self):
        self.buf = None
        self.frames = []    # completed raw JPEG byte strings
        self.packets = 0
        self.bytes = 0

    def feed(self, data):
        if not data:
            return
        self.packets += 1
        self.bytes += len(data)
        if len(data) >= 16 and data[0] == data[1] == data[2] == 0xFF:
            if data[3] == 0x50:    # start of frame
                body = data[16:]
                self.buf = bytearray(body) if body[:1] == b"\xff" or body[1:2] == b"\xd8" else None
                return
            if data[3] == 0x51:    # end of frame
                if self.buf and data[9] == 0:
                    self.frames.append(bytes(self.buf))
                self.buf = None
                return
        if self.buf is not None:
            self.buf += data


def decode(raw):
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


STALL_SECONDS = 5.0   # no new picture for this long means the stream has stalled
MAX_RESTARTS = 3      # stream restarts in a row without a picture before giving up
STALLED_MESSAGE = ("The EyeToy stopped sending video.\n\n"
                   "Unplug it, plug it back in, then open EyeToy Camera again.")
DISCONNECTED_MESSAGE = ("Lost the connection to the EyeToy ({}).\n\n"
                        "Check it is plugged in, then open EyeToy Camera again.")


class Stream:
    """
    The isochronous transfers kept in flight. Each completed transfer is fed to the frame
    assembler and resubmitted. On Windows a resubmit occasionally fails (LIBUSB_ERROR_NO_MEM);
    such transfers are retried from the main loop instead of being lost, which used to starve
    the stream until it stalled.
    """

    def __init__(self, cam, ep, mps, asm):
        self.asm = asm
        self.running = False
        self.retry = []
        self.xfers = []
        for _ in range(N_XFERS):
            t = cam.h.getTransfer(iso_packets=N_PKTS)
            t.setIsochronous(ep, mps * N_PKTS, callback=self._done)
            self.xfers.append(t)

    def _done(self, t):
        if t.getStatus() == usb1.TRANSFER_COMPLETED:
            for status, data in t.iterISO():
                if status == usb1.TRANSFER_COMPLETED:
                    self.asm.feed(data)
        if self.running and t.getStatus() != usb1.TRANSFER_CANCELLED:
            self._submit(t, report=True)

    def _submit(self, t, report=False):
        try:
            t.submit()
        except usb1.USBError as e:
            if report:
                print("resubmit failed, will retry:", e)
            if t not in self.retry:
                self.retry.append(t)

    def start(self):
        self.running = True
        self.retry = []
        for t in self.xfers:
            self._submit(t, report=True)

    def retry_failed(self):
        if not self.running or not self.retry:
            return
        pending, self.retry = self.retry, []
        for t in pending:
            self._submit(t)

    def stop(self, ctx):
        self.running = False
        self.retry = []
        for t in self.xfers:
            try:
                t.cancel()
            except usb1.USBError:
                pass  # not in flight
        end = time.time() + 1
        while any(t.isSubmitted() for t in self.xfers) and time.time() < end:
            ctx.handleEventsTimeout(0.05)


def with_banner(img, text):
    """A copy of img with a dark strip and text along the bottom."""
    out = img.copy()
    h, w = out.shape[:2]
    cv2.rectangle(out, (0, h - 34), (w, h), (0, 0, 0), -1)
    cv2.putText(out, text, (12, h - 11), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return out


class Recorder:
    """
    Writes the picture to an mp4 at a steady 15 frames per second in real time. The camera's own
    frame rate wobbles (and stops during a stall), so frames are repeated or skipped to match the
    clock: a 60 s recording plays back for 60 s. Video only: OpenCV cannot capture sound.
    """
    FPS = 15

    def __init__(self, path):
        self.path = path
        self.writer = None
        self.started = None
        self.written = 0

    def add(self, frame, now):
        """Adds the picture seen at time `now`. Raises OSError if the file cannot be created."""
        h, w = frame.shape[:2]
        if self.writer is None:
            self.writer = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*"mp4v"), self.FPS, (w, h))
            if not self.writer.isOpened():
                self.writer = None
                raise OSError(f"cannot create {self.path}")
            self.started = now
            self.size = (w, h)
        if (w, h) != self.size:
            frame = cv2.resize(frame, self.size)
        due = int((now - self.started) * self.FPS) + 1
        while self.written < due:
            self.writer.write(frame)
            self.written += 1

    def seconds(self, now):
        return 0.0 if self.started is None else now - self.started

    def stop(self):
        """Finishes the file. Returns (path, frames written)."""
        if self.writer is not None:
            self.writer.release()
            self.writer = None
        return self.path, self.written


def with_rec(img, seconds):
    """A copy of img with a red dot and the recording time in the top-left corner."""
    out = img.copy()
    cv2.circle(out, (26, 26), 9, (0, 0, 255), -1, cv2.LINE_AA)
    cv2.putText(out, f"REC {int(seconds) // 60:02d}:{int(seconds) % 60:02d}", (44, 33),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def open_stream(ctx, asm, width, height):
    """Opens the camera, sets it up from scratch and starts streaming into asm. Returns (cam, stream)."""
    cam = EyeToy(ctx)
    try:
        cam.configure_bridge()
        cam.detect_sensor()
        alt, mps, ep = cam.pick_alt()
        print(f"Streaming {width}x{height}, alt setting {alt}, endpoint 0x{ep:02x}, packet {mps} bytes")
        cam.h.setInterfaceAltSetting(IFACE, alt)
        cam.start(width, height)
        stream = Stream(cam, ep, mps, asm)
        stream.start()
    except BaseException:
        cam.close()
        raise
    return cam, stream


def bring_to_front(title):
    """Raises the window so it does not open hidden behind other windows."""
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


APP_ID = "EyeToyCamera.Viewer"  # must match the shortcuts made by make_shortcuts.ps1


def main():
    # Our own taskbar identity (set before any window exists). Without it the window counts as
    # "Python", and pinning it pins bare pythonw.exe, which then opens nothing.
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    args = sys.argv[1:]
    snap = int(args[args.index("--snap") + 1]) if "--snap" in args else 0
    width, height = (320, 240) if "--qvga" in args else (640, 480)
    simulate_stall = "--simulate-stall" in args
    record_for = None  # --record SECONDS: record that long from the first picture, then exit
    if "--record" in args:
        try:
            record_for = float(args[args.index("--record") + 1])
        except (IndexError, ValueError):
            raise SystemExit("--record needs a number of seconds, for example: --record 10")
        if record_for <= 0 or snap:
            raise SystemExit("--record needs a positive number of seconds and cannot be combined with --snap")

    win = "EyeToy"
    if not snap:
        # Show something at once: setting up the camera takes a few seconds, and an empty wait
        # looked like the click did nothing.
        cv2.imshow(win, with_banner(np.zeros((height, width, 3), np.uint8), "Starting camera..."))
        cv2.waitKey(1)
        bring_to_front(win)

    with usb1.USBContext() as ctx:
        asm = FrameAssembler()
        cam, stream = open_stream(ctx, asm, width, height)
        recorder = None
        try:
            os.makedirs("captures", exist_ok=True)
            saved, bad, shown, restarts = 0, 0, 0, 0
            t0 = last_frame = time.time()
            img = None
            stall_at = None  # --simulate-stall: when to cut the stream once, to test recovery
            notice, notice_until = "", 0.0       # a message on the picture, for a few seconds
            last_toggle = 0.0                    # when R last started or stopped a recording
            hint_until = 0.0

            def start_recording():
                name = time.strftime("captures/eyetoy_%Y%m%d_%H%M%S.mp4")
                print("recording to", name)
                return Recorder(name)

            def finish_recording(rec):
                path, frames = rec.stop()
                print(f"saved {path} ({frames} frames, {frames / Recorder.FPS:.1f} s)")
                return f"Saved {os.path.basename(path)}"

            while True:
                ctx.handleEventsTimeout(0.01)
                stream.retry_failed()

                if asm.frames:
                    raw = asm.frames.pop()
                    asm.frames.clear()
                    last_frame = time.time()
                    restarts = 0
                    if simulate_stall and stall_at is None:
                        stall_at = last_frame + 1.0
                    frame = decode(raw)
                    if snap:
                        with open(f"captures/raw_{saved}.jpg", "wb") as f:
                            f.write(raw)
                        if frame is not None:
                            cv2.imwrite(f"captures/frame_{saved}.png", frame)
                        else:
                            bad += 1
                        saved += 1
                        if saved >= snap:
                            print(f"Saved {saved} frames ({bad} undecodable) in {time.time() - t0:.1f}s, "
                                  f"last frame {len(raw)} bytes, packets={asm.packets}")
                            break
                    elif frame is None:
                        bad += 1
                    else:
                        img = frame
                        shown += 1
                        now = time.time()
                        if shown == 1:
                            bring_to_front(win)
                            hint_until = now + 8
                            if record_for is not None:
                                recorder = start_recording()
                        if recorder is not None:
                            try:
                                recorder.add(frame, now)
                            except OSError as e:
                                print("recording failed:", e)
                                if record_for is not None:
                                    raise SystemExit(f"Could not create the video file: {e}")
                                recorder, notice, notice_until = None, "Could not start recording", now + 4
                        view = frame
                        if recorder is not None:
                            view = with_rec(view, recorder.seconds(now))
                        text = notice if now < notice_until else ("R record   S snapshot   Q quit" if now < hint_until else "")
                        cv2.imshow(win, with_banner(view, text) if text else view)

                if shown:
                    # Serve the window on every pass, picture or not, so it never goes
                    # "Not Responding" and its X button always works.
                    key = cv2.waitKey(1) & 0xFF
                    ch = chr(key).lower() if 0 < key < 128 else ""   # accepts Caps Lock too
                    if key == 27 or ch == "q" or cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                        break
                    if ch == "s" and img is not None:
                        name = time.strftime("captures/eyetoy_%Y%m%d_%H%M%S.png")
                        cv2.imwrite(name, img)
                        print("saved", name)
                    # The 1 s gap stops a held-down R (key auto-repeat) from flipping recording on and off.
                    if ch == "r" and img is not None and time.time() - last_toggle > 1.0:
                        last_toggle = time.time()
                        if recorder is None:
                            recorder = start_recording()
                            try:
                                recorder.add(img, time.time())   # the file starts with the picture on screen
                            except OSError as e:
                                print("recording failed:", e)
                                recorder, notice, notice_until = None, "Could not start recording", time.time() + 4
                        else:
                            notice, notice_until = finish_recording(recorder), time.time() + 4
                            recorder = None
                    if recorder is not None and record_for is not None and recorder.seconds(time.time()) >= record_for:
                        break

                if stall_at is not None and time.time() >= stall_at:
                    print("simulating a stall: stopping the stream")
                    stream.stop(ctx)
                    stall_at = float("inf")  # only once

                if time.time() - last_frame > STALL_SECONDS:
                    if restarts >= MAX_RESTARTS:
                        raise SystemExit(STALLED_MESSAGE)
                    restarts += 1
                    print(f"no video for {STALL_SECONDS:.0f}s (packets so far {asm.packets}); "
                          f"restarting the stream ({restarts}/{MAX_RESTARTS})")
                    if img is not None:
                        cv2.imshow(win, with_banner(img, "Reconnecting camera..."))
                        cv2.waitKey(1)
                    # Reconnect from scratch. Windows' USB driver refuses to switch a connection that
                    # has streamed and stopped back into video mode (LIBUSB_ERROR_IO); a fresh one works.
                    stream.stop(ctx)
                    cam.close()
                    cam = stream = None
                    asm.buf = None
                    try:
                        cam, stream = open_stream(ctx, asm, width, height)
                    except usb1.USBError as e:
                        print("reconnect failed:", e)
                        raise SystemExit(DISCONNECTED_MESSAGE.format(e))
                    last_frame = time.time()
        finally:
            if recorder is not None:
                finish_recording(recorder)   # closing the window or an error must not lose the video
            if stream is not None:
                stream.stop(ctx)
            if cam is not None:
                cam.close()
            cv2.destroyAllWindows()


def show_error(message):
    """Pop up a Windows message box. Used when started from the shortcut, where there is no console."""
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, message, "EyeToy Camera", 0x10)  # MB_ICONERROR


if __name__ == "__main__":
    # Started from the shortcut (pythonw) there is no console, so keep a log next to the script
    # and show problems in a message box instead of closing silently.
    no_console = sys.stdout is None
    if no_console:
        log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eyetoy.log"), "w", buffering=1)
        sys.stdout = sys.stderr = log
        print(time.strftime("started %Y-%m-%d %H:%M:%S"))
    try:
        main()
    except SystemExit as e:
        print("exit:", e)
        if no_console and isinstance(e.code, str):
            show_error(e.code)
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        if no_console:
            show_error(f"The camera stopped with an error:\n{e}\n\nDetails are in eyetoy.log.")
        raise
