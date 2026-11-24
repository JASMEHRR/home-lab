"""
The posture coach's engine: reads the laptop webcam (front) and the EyeToy (side), finds your body and
face with MediaPipe, judges posture and focus once a second, and appends numbers (never video) to
data\\posture-YYYY-MM-DD.csv. It runs in its own thread; app.py shows what it reports.

Run it alone for a test:  py engine.py --seconds 20
"""
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
sys.path.insert(0, PARENT)                                   # eyetoy.py lives one folder up
POSE_LIBS = r"C:\Users\jasme\posture-pylibs"                 # mediapipe etc. (pip install --target)
if os.path.isdir(POSE_LIBS):
    sys.path.insert(0, POSE_LIBS)

import cv2
import mediapipe as mp
import numpy as np

import eyetoy
import metrics
from metrics import FaceReading, PoseReading

import usb1

MODELS = {
    "pose_landmarker_lite.task": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
    "efficientdet_lite0.tflite": "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/float16/latest/efficientdet_lite0.tflite",
}
MODEL_DIR = os.path.join(HERE, "models")
DATA_DIR = os.path.join(HERE, "data")
CAPTURE_DIR = os.path.join(PARENT, "captures")
CAL_PATH = os.path.join(HERE, "calibration.json")

INFER_EVERY = 0.5          # seconds between body/face checks per camera
READING_MAX_AGE = 2.0      # a reading counts for this long
CALIBRATE_SECONDS = 4.0
PREVIEW_EVERY = 0.1        # the preview picture is rebuilt at most this often
PANEL = (480, 360)
CSV_HEADER = "time,state,reasons,focus,head_front,head_side,roll_front,roll_side,lean_ratio,yaw,pitch_down,blink,studying\n"
DROWSY_COOLDOWN = 600.0
PHONE_SCORE = 0.30         # how sure the object finder must be that it sees a phone
PHONE_MAX_AGE = 1.5        # a phone sighting counts for this long
HAND_POINTS = (15, 16, 17, 18, 19, 20, 21, 22)       # wrists, pinkies, index fingers, thumbs
AUTO_BASELINE_SAMPLES = 40  # face sizes used to learn the screen distance when you have not calibrated

# glow_after: seconds of bad posture before the glow comes on (it stays until you fix it)
DEFAULT_SETTINGS = {"sensitivity": "medium", "glow_after": 3, "sound": True, "glow": True}


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


# ---------------------------------------------------------------- models

def ensure_models():
    os.makedirs(MODEL_DIR, exist_ok=True)
    for name, url in MODELS.items():
        path = os.path.join(MODEL_DIR, name)
        if os.path.isfile(path) and os.path.getsize(path) > 1_000_000:
            continue
        log("downloading", name)
        tmp = path + ".part"
        try:
            urllib.request.urlretrieve(url, tmp)
            if os.path.getsize(tmp) < 1_000_000:
                raise OSError("the downloaded file is too small")
            os.replace(tmp, path)
        except (OSError, urllib.error.URLError) as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError(f"Could not download {name}: {e}. Save it from {url} into {MODEL_DIR}")


def make_models():
    ensure_models()
    vision, base = mp.tasks.vision, mp.tasks.BaseOptions
    pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=base(model_asset_path=os.path.join(MODEL_DIR, "pose_landmarker_lite.task")),
        running_mode=vision.RunningMode.IMAGE, num_poses=1))
    face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=base(model_asset_path=os.path.join(MODEL_DIR, "face_landmarker.task")),
        running_mode=vision.RunningMode.IMAGE, num_faces=1,
        output_face_blendshapes=True, output_facial_transformation_matrixes=True))
    objects = vision.ObjectDetector.create_from_options(vision.ObjectDetectorOptions(
        base_options=base(model_asset_path=os.path.join(MODEL_DIR, "efficientdet_lite0.tflite")),
        running_mode=vision.RunningMode.IMAGE, max_results=5, score_threshold=PHONE_SCORE,
        category_allowlist=["cell phone"]))
    return pose, face, objects


def to_mp_image(frame):
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def detect_pose(pose_model, mp_image, size):
    """
    (PoseReading or None, image points {n: (x, y)} in pixels or None, hand points [(x, y)]) for the
    one person seen. Hand points are wrists and fingers, used to tell a phone in the hand from one on the desk.
    """
    result = pose_model.detect(mp_image)
    if not result.pose_landmarks or not result.pose_world_landmarks:
        return None, None, []
    w, h = size
    img, world, vis = result.pose_landmarks[0], result.pose_world_landmarks[0], {}
    world_pts, image_pts = {}, {}
    for i in (metrics.NOSE, metrics.L_EAR, metrics.R_EAR, metrics.L_SHOULDER, metrics.R_SHOULDER):
        vis[i] = img[i].visibility if img[i].visibility is not None else 0.0
        world_pts[i] = (world[i].x, world[i].y, world[i].z)
        if vis[i] >= metrics.MIN_VISIBILITY:
            image_pts[i] = (img[i].x * w, img[i].y * h)
    hands = [(img[i].x * w, img[i].y * h) for i in HAND_POINTS
             if (img[i].visibility if img[i].visibility is not None else 0.0) >= 0.3]
    head, roll = metrics.head_and_roll(world_pts, vis)
    return (PoseReading(head, roll) if head is not None else None), image_pts, hands


def detect_phones(object_model, mp_image):
    """Boxes (x, y, width, height) in pixels of every phone the object finder sees."""
    result = object_model.detect(mp_image)
    boxes = []
    for d in result.detections:
        b = d.bounding_box
        boxes.append((b.origin_x, b.origin_y, b.width, b.height))
    return boxes


def detect_face(face_model, mp_image, size):
    """FaceReading or None."""
    result = face_model.detect(mp_image)
    if not result.face_landmarks or not result.facial_transformation_matrixes:
        return None
    w, h = size
    marks = result.face_landmarks[0]
    shapes = {b.category_name: b.score for b in result.face_blendshapes[0]} if result.face_blendshapes else {}
    yaw, pitch_down = metrics.head_angles(np.array(result.facial_transformation_matrixes[0]).reshape(4, 4).tolist())
    a, b = marks[33], marks[263]                              # outer eye corners
    size_frac = float(np.hypot((a.x - b.x) * w, (a.y - b.y) * h)) / w
    blink = (shapes.get("eyeBlinkLeft", 0.0) + shapes.get("eyeBlinkRight", 0.0)) / 2
    gaze_down = (shapes.get("eyeLookDownLeft", 0.0) + shapes.get("eyeLookDownRight", 0.0)) / 2
    return FaceReading(blink, yaw, pitch_down, gaze_down, size_frac)


# ---------------------------------------------------------------- cameras

class FrontCamera:
    """The laptop webcam through DirectShow."""

    def __init__(self, index=None):
        self.cap = None
        for i in (range(4) if index is None else [index]):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                ok, _ = cap.read()
                if ok:
                    self.cap, self.index = cap, i
                    return
            cap.release()
        raise OSError("no webcam found")

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        self.cap.release()


class SideCamera:
    """The EyeToy, through the same USB code as eyetoy.py. Reconnects itself if the picture stalls."""
    STALL_SECONDS = 6.0
    MAX_RESTARTS = 3

    def __init__(self, ctx):
        self.ctx = ctx
        self.asm = eyetoy.FrameAssembler()
        self.cam = self.stream = None
        self.restarts = 0
        self._open()

    def _open(self):
        self.cam, self.stream = eyetoy.open_stream(self.ctx, self.asm, 640, 480)
        self.last_frame = time.time()

    def poll(self, wait=0.001):
        """Newest picture since the last call, or None. Raises OSError once the camera is lost."""
        self.ctx.handleEventsTimeout(wait)
        self.stream.retry_failed()
        frame = None
        if self.asm.frames:
            raw = self.asm.frames.pop()
            self.asm.frames.clear()
            frame = eyetoy.decode(raw)
        now = time.time()
        if frame is not None:
            self.last_frame = now
            self.restarts = 0
        elif now - self.last_frame > self.STALL_SECONDS:
            if self.restarts >= self.MAX_RESTARTS:
                raise OSError("the EyeToy stopped sending video")
            self.restarts += 1
            log(f"EyeToy stalled; reconnecting ({self.restarts}/{self.MAX_RESTARTS})")
            self.close()
            self.asm.buf = None
            try:
                self._open()
            except (usb1.USBError, SystemExit) as e:
                raise OSError(f"could not reconnect the EyeToy: {e}")
        return frame

    def close(self):
        if self.stream is not None:
            self.stream.stop(self.ctx)
            self.stream = None
        if self.cam is not None:
            self.cam.close()
            self.cam = None


# ---------------------------------------------------------------- saved data

class DayLog:
    """Appends one CSV row per second to data\\posture-YYYY-MM-DD.csv (a new file after midnight)."""

    def __init__(self, folder):
        self.folder = folder
        self.day = None
        self.file = None
        os.makedirs(folder, exist_ok=True)

    def write(self, when, row):
        day = when.strftime("%Y-%m-%d")
        if day != self.day:
            self.close()
            path = os.path.join(self.folder, f"posture-{day}.csv")
            new = not os.path.exists(path) or os.path.getsize(path) == 0
            if not new:
                self._match_header(path)
            self.file = open(path, "a", encoding="utf-8", newline="")
            if new:
                self.file.write(CSV_HEADER)
            self.day = day
        self.file.write(row + "\n")
        self.file.flush()

    @staticmethod
    def _match_header(path):
        """
        A file from an older version has fewer columns. Rows would no longer line up, so extend
        the header (old rows simply have no value for the new columns) or, if the columns differ
        in some other way, move the old file aside.
        """
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        first, _, rest = text.partition("\n")
        if first.strip() == CSV_HEADER.strip():
            return
        if CSV_HEADER.strip().startswith(first.strip()):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(CSV_HEADER + rest)
            return
        n = 1
        while os.path.exists(f"{path}.old{n}"):
            n += 1
        os.replace(path, f"{path}.old{n}")

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


def load_calibration():
    try:
        with open(CAL_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        cal = dict(metrics.DEFAULT_CALIBRATION)
        for key in cal:
            if saved.get(key) is not None:
                cal[key] = float(saved[key])
        return cal
    except FileNotFoundError:
        return dict(metrics.DEFAULT_CALIBRATION)
    except (OSError, ValueError, TypeError) as e:
        log("ignoring a damaged calibration file:", e)
        return dict(metrics.DEFAULT_CALIBRATION)


def save_calibration(cal):
    saved = dict(cal, saved=datetime.now().astimezone().isoformat(timespec="seconds"))
    with open(CAL_PATH, "w", encoding="utf-8") as f:
        json.dump(saved, f, indent=2)


def csv_row(when, state, reasons, focus, poses, face, cal, studying=False):
    def num(v, places):
        return "" if v is None else f"{v:.{places}f}"

    front, side = poses.get("front"), poses.get("side")
    lean = (face.size / cal["face_size"]) if face is not None and cal.get("face_size") else None
    return ",".join([
        when.astimezone().isoformat(timespec="seconds"), state, ";".join(reasons), focus,
        num(front.head if front else None, 1), num(side.head if side else None, 1),
        num(front.roll if front else None, 1), num(side.roll if side else None, 1), num(lean, 2),
        num(face.yaw if face else None, 1), num(face.pitch_down if face else None, 1), num(face.blink if face else None, 2),
        "1" if studying else "0",
    ])


# ---------------------------------------------------------------- preview picture

COLORS = {"good": (80, 200, 80), "bad": (60, 60, 230), "away": (150, 150, 150), "unsure": (60, 170, 230)}   # BGR


def draw_pose(frame, points, color, phones=()):
    out = frame.copy()
    for x, y, w, h in phones:
        cv2.rectangle(out, (int(x), int(y)), (int(x + w), int(y + h)), (0, 140, 255), 3)
        cv2.putText(out, "phone", (int(x), max(15, int(y) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 140, 255), 2, cv2.LINE_AA)
    if not points:
        return out

    def line(a, b):
        if a in points and b in points:
            cv2.line(out, tuple(int(c) for c in points[a]), tuple(int(c) for c in points[b]), color, 3, cv2.LINE_AA)

    line(metrics.L_SHOULDER, metrics.R_SHOULDER)
    line(metrics.L_EAR, metrics.L_SHOULDER)
    line(metrics.R_EAR, metrics.R_SHOULDER)
    for p in points.values():
        cv2.circle(out, (int(p[0]), int(p[1])), 6, (255, 255, 255), -1, cv2.LINE_AA)
    return out


def panel(frame, points, title, note, color, phones=()):
    if frame is None:
        img = np.zeros((PANEL[1], PANEL[0], 3), np.uint8)
        cv2.putText(img, note or "no picture", (20, PANEL[1] // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2, cv2.LINE_AA)
    else:
        img = cv2.resize(draw_pose(frame, points, color, phones) if (points is not None or phones) else frame, PANEL)
    cv2.rectangle(img, (0, 0), (PANEL[0], 28), (0, 0, 0), -1)
    cv2.putText(img, title, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return img


# ---------------------------------------------------------------- the engine

class Engine(threading.Thread):
    """Everything that runs in the background. Read `snapshot()` often; call the request methods from the app."""

    def __init__(self, settings=None, front_index=None, use_side=True):
        super().__init__(name="posture-engine", daemon=True)
        self.settings = dict(DEFAULT_SETTINGS, **(settings or {}))
        self.front_index = front_index
        self.use_side = use_side
        self._stop_flag = threading.Event()
        self._lock = threading.Lock()
        self._snap = {"status": "starting", "notes": [], "state": "away", "focus": "no_face", "reasons": (), "preview": None}
        self._alert_id = 0
        self._want_calibrate = False
        self._want_record = None          # True/False to start/stop, None for no request
        self.paused = False
        self.cal = load_calibration()

    # ----- the app's side
    def snapshot(self):
        with self._lock:
            return dict(self._snap)

    def stop(self):
        self._stop_flag.set()

    def calibrate(self):
        self._want_calibrate = "main"

    def calibrate_second(self):
        """Look at the other laptop/screen you study on; looking that way then counts as studying."""
        self._want_calibrate = "second"

    def forget_second(self):
        self.cal = dict(self.cal, yaw2=None, pitch2=None)
        save_calibration(self.cal)

    def set_recording(self, on):
        self._want_record = bool(on)

    def _publish(self, **fields):
        with self._lock:
            self._snap.update(fields)

    # ----- the engine's side
    def run(self):
        try:
            self._run()
        except Exception as e:     # the app shows this; a silent dead thread would look like "no cameras"
            import traceback
            traceback.print_exc()
            self._publish(status="error", error=f"{type(e).__name__}: {e}")

    def _run(self):
        pose_model, face_model, object_model = make_models()
        front = side = ctx = None
        notes = []
        try:
            front = FrontCamera(self.front_index)
            log(f"front camera: webcam {front.index}")
        except OSError as e:
            notes.append(f"No laptop webcam ({e})")
        if self.use_side:
            try:
                ctx = usb1.USBContext()
                side = SideCamera(ctx)
                log("side camera: EyeToy")
            except (OSError, usb1.USBError, SystemExit) as e:
                notes.append("EyeToy not available (close its window or replug it)")
                log("EyeToy not available:", e)
        if front is None and side is None:
            self._publish(status="error", error="No camera could be opened. Check the laptop webcam or plug in the EyeToy.")
            return

        day_log = DayLog(DATA_DIR)
        posture_filter = metrics.StateFilter()
        focus_filter = metrics.HoldFilter(hold=3.0, quick={"focused": 1.0, "eyes_closed": 1.5})
        closure = metrics.EyeClosure()
        frames = {"front": None, "side": None}
        points = {"front": None, "side": None}
        phone_boxes = {"front": [], "side": []}
        phone_at = {"front": 0.0, "side": 0.0}       # when a phone was last seen in a hand, per camera
        size_samples = []                            # face sizes, to learn the screen distance without calibration
        bad_since = None
        poses = {"front": None, "side": None}
        seen_at = {"front": 0.0, "side": 0.0}
        last_infer = {"front": 0.0, "side": 0.0}
        face, face_seen_at = None, 0.0
        last_row = last_preview = 0.0
        calibrating_until, pose_samples, face_samples = 0.0, {"front": [], "side": []}, []
        calibrating_mode = "main"
        recorder, record_started = None, 0.0
        message, message_until = "", 0.0
        last_drowsy_alert = -1e9
        black_since = None
        state, reasons, focus = "away", (), "no_face"
        self._publish(status="running", notes=notes, error=None)

        try:
            while not self._stop_flag.is_set():
                now = time.time()
                cal = self.cal

                try:
                    if front is not None:
                        f = front.read()
                        if f is not None:
                            frames["front"] = f
                            black_since = (black_since or now) if f.mean() < 2.0 else None
                        else:
                            time.sleep(0.01)
                    if side is not None:
                        s = side.poll(0.001 if front is not None else 0.02)
                        if s is not None:
                            frames["side"] = s
                except OSError as e:
                    log("camera lost:", e)
                    if side is not None and "EyeToy" in str(e):
                        side.close()
                        side, frames["side"], poses["side"], points["side"] = None, None, None, None
                        notes.append("EyeToy lost")
                        if front is None:
                            self._publish(status="error", error="The EyeToy stopped and no other camera is available.")
                            return
                    else:
                        raise

                for name, cam in (("front", front), ("side", side)):
                    frame = frames[name]
                    if cam is None or frame is None or now - last_infer[name] < INFER_EVERY:
                        continue
                    last_infer[name] = now
                    size = (frame.shape[1], frame.shape[0])
                    try:
                        mp_image = to_mp_image(frame)
                        reading, pts, hands = detect_pose(pose_model, mp_image, size)
                        boxes = detect_phones(object_model, mp_image)
                        phone_boxes[name] = boxes
                        if metrics.phone_in_hand(boxes, hands):
                            phone_at[name] = now
                        if name == "front":
                            new_face = detect_face(face_model, mp_image, size)
                            face = new_face
                            if new_face is not None:
                                face_seen_at = now
                    except (RuntimeError, ValueError) as e:
                        log("body/face check failed:", e)
                        continue
                    points[name] = pts
                    poses[name] = reading
                    if reading is not None:
                        seen_at[name] = now
                        if now < calibrating_until:
                            pose_samples[name].append(reading)
                    if name == "front" and face is not None and now < calibrating_until:
                        face_samples.append(face)

                if self._want_calibrate and now >= calibrating_until:
                    calibrating_mode, self._want_calibrate = self._want_calibrate, False
                    calibrating_until, pose_samples, face_samples = now + CALIBRATE_SECONDS, {"front": [], "side": []}, []
                if calibrating_until and now >= calibrating_until:
                    calibrating_until = 0.0
                    if calibrating_mode == "second":
                        if len(face_samples) >= 3:
                            self.cal = cal = dict(cal, yaw2=statistics.median(f.yaw for f in face_samples),
                                                  pitch2=statistics.median(f.pitch_down for f in face_samples))
                            save_calibration(cal)
                            message, message_until = "Second screen saved. Looking at it now counts as studying.", now + 6
                            log("second screen:", cal["yaw2"], cal["pitch2"])
                        else:
                            message, message_until = "Could not see your face. Look at the other laptop and try again.", now + 5
                    elif any(len(v) >= 3 for v in pose_samples.values()) or len(face_samples) >= 3:
                        keep = {k: cal.get(k) for k in ("yaw2", "pitch2")}
                        self.cal = cal = dict(metrics.calibrate(pose_samples, face_samples), **keep)
                        save_calibration(cal)
                        message, message_until = "Calibrated. I measure against how you sit now.", now + 5
                        log("calibrated:", cal)
                    else:
                        message, message_until = "Could not see you. Sit in view and calibrate again.", now + 5

                if self._want_record is not None:
                    want, self._want_record = self._want_record, None
                    if want and recorder is None:
                        os.makedirs(CAPTURE_DIR, exist_ok=True)
                        recorder = eyetoy.Recorder(os.path.join(CAPTURE_DIR, time.strftime("coach_%Y%m%d_%H%M%S.mp4")))
                        record_started = now
                        log("recording to", recorder.path)
                    elif not want and recorder is not None:
                        path, frame_count = recorder.stop()
                        message, message_until = f"Saved {os.path.basename(path)}", now + 5
                        log(f"saved {path} ({frame_count} frames)")
                        recorder = None

                fresh_poses = {k: (poses[k] if now - seen_at[k] <= READING_MAX_AGE else None) for k in poses}
                fresh_face = face if now - face_seen_at <= READING_MAX_AGE else None

                if now - last_row >= 1.0:
                    last_row = now
                    phone = any(now - t <= PHONE_MAX_AGE for t in phone_at.values())
                    judge_cal = cal
                    if not cal.get("face_size"):
                        # not calibrated: learn how far you sit from the screen from the first faces seen
                        if fresh_face is not None and len(size_samples) < AUTO_BASELINE_SAMPLES and not phone:
                            size_samples.append(fresh_face.size)
                        if len(size_samples) >= 10:
                            judge_cal = dict(cal, face_size=sorted(size_samples)[len(size_samples) // 2])
                    if fresh_face is not None:
                        closure.add(now, fresh_face.blink > metrics.EYES_CLOSED_BLINK)
                    if phone:
                        raw_focus = "phone"
                    elif front is None:
                        raw_focus = "unknown"                      # the face is only judged from the laptop webcam
                    elif closure.drowsy():
                        raw_focus = "drowsy"
                    else:
                        raw_focus = metrics.classify_focus(fresh_face, judge_cal)
                        if raw_focus == "no_face" and state != "away":
                            raw_focus = "looking_away"             # body is here but the face is hidden: turned or head down
                    focus = focus_filter.update(now, raw_focus)
                    # Activity is decided first: bending over a notebook is allowed more forward head than staring at the screen.
                    assessment = metrics.assess_posture(fresh_poses, fresh_face, judge_cal, self.settings["sensitivity"],
                                                        writing=(focus == "writing"))
                    state = posture_filter.update(now, assessment.state)
                    reasons = assessment.reasons
                    if state == "away":
                        focus = "no_face"
                    studying = focus in metrics.STUDY_LABELS
                    if state == "bad" and not self.paused:
                        bad_since = bad_since or now
                    else:
                        bad_since = None
                    if not self.paused:
                        when = datetime.now()
                        day_log.write(when, csv_row(when, state, reasons, focus, fresh_poses, fresh_face, judge_cal, studying))
                        if focus == "drowsy" and now - last_drowsy_alert > DROWSY_COOLDOWN:
                            last_drowsy_alert = now
                            self._alert_id += 1
                            self._publish(alert_id=self._alert_id, alert_text="You look sleepy. Take a short break.")

                if now - last_preview >= PREVIEW_EVERY:
                    last_preview = now
                    color = COLORS.get(state, COLORS["away"])
                    p1 = panel(frames["front"], points["front"], "FRONT  (laptop webcam)", "no webcam" if front is None else "", color, phone_boxes["front"])
                    p2 = panel(frames["side"], points["side"], "SIDE  (EyeToy)", "EyeToy off" if side is None else "", color, phone_boxes["side"])
                    preview = np.hstack([p1, p2])
                    if recorder is not None:
                        clean = np.hstack([panel(frames["front"], None, "FRONT", "", color), panel(frames["side"], None, "SIDE", "", color)])
                        try:
                            recorder.add(clean, now)
                        except OSError as e:
                            log("recording failed:", e)
                            recorder = None
                            message, message_until = "Recording stopped: could not write the file", now + 5
                    show_notes = list(notes)
                    if black_since and now - black_since > 5:
                        show_notes.append("Laptop webcam is black: close other apps using it, or open its privacy shutter")
                    self._publish(
                        t=now, state=state, reasons=reasons, focus=focus, preview=preview, notes=show_notes,
                        studying=focus in metrics.STUDY_LABELS, bad_for=(now - bad_since) if bad_since else 0.0,
                        front_ok=front is not None, side_ok=side is not None, paused=self.paused,
                        calibrating_left=max(0.0, calibrating_until - now) if calibrating_until else 0.0,
                        message=message if now < message_until else "",
                        recording=recorder is not None, record_seconds=(now - record_started) if recorder else 0.0,
                        cal=dict(cal), head_front=poses["front"].head if fresh_poses["front"] else None,
                        head_side=poses["side"].head if fresh_poses["side"] else None,
                        yaw=fresh_face.yaw if fresh_face else None, pitch_down=fresh_face.pitch_down if fresh_face else None,
                        blink=fresh_face.blink if fresh_face else None,
                    )
        finally:
            if recorder is not None:
                recorder.stop()
            day_log.close()
            if side is not None:
                side.close()
            if front is not None:
                front.close()
            if ctx is not None:
                ctx.close()
            pose_model.close()
            face_model.close()
            object_model.close()
            self._publish(status="stopped")
            log("engine stopped")


REASON_TEXT = {"forward_head": "head too far forward", "shoulder_tilt": "shoulders uneven", "leaning_in": "leaning toward the screen"}


if __name__ == "__main__":
    seconds = 20.0
    if "--seconds" in sys.argv:
        seconds = float(sys.argv[sys.argv.index("--seconds") + 1])
    eng = Engine(use_side="--no-side" not in sys.argv)
    eng.start()
    end = time.time() + seconds
    while time.time() < end and eng.is_alive():
        time.sleep(2)
        s = eng.snapshot()
        log(f"status={s['status']} state={s.get('state')} focus={s.get('focus')} reasons={s.get('reasons')} "
            f"head_front={s.get('head_front')} head_side={s.get('head_side')} yaw={s.get('yaw')} pitch_down={s.get('pitch_down')} "
            f"blink={s.get('blink')} notes={s.get('notes')} error={s.get('error')}")
    eng.stop()
    eng.join(10)
