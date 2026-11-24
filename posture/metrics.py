"""
Posture and focus maths. Pure Python (no cameras, no OpenCV, no MediaPipe) so test_metrics.py
can check it anywhere.

Posture comes from MediaPipe's 3D "world" body points (metres). Using 3D means the head-forward
angle is the same wherever the camera stands: front, side or diagonal. Each camera is measured
against its own calibrated "sitting up straight" value.

Focus comes from the face: eyes closed, head turned away, head tilted down (phone or desk).
Calibration also records how you hold your head while looking at the screen, because a webcam
sits above the screen and looking at the screen already means looking slightly down.
"""
import math
import statistics
from collections import deque
from dataclasses import dataclass

# MediaPipe BlazePose landmark numbers
NOSE, L_EAR, R_EAR, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 0, 7, 8, 11, 12, 23, 24

MIN_VISIBILITY = 0.5
CAMERAS = ("front", "side")

# How far from your calibrated posture counts as bad. Higher sensitivity = stricter.
SENSITIVITY = {
    "low": {"head": 18.0, "roll": 12.0, "lean": 1.35},
    "medium": {"head": 12.0, "roll": 8.0, "lean": 1.25},
    "high": {"head": 8.0, "roll": 6.0, "lean": 1.15},
}

# Focus limits, in degrees from how you sit when looking at the screen
PITCH_DOWN_LIMIT = 22.0
PITCH_UP_LIMIT = 30.0
YAW_LIMIT = 32.0
GAZE_DOWN_LIMIT = 0.35
EYES_CLOSED_BLINK = 0.55
LEAN_BACK = 0.82              # face this much smaller than when calibrated means sitting back from the screen
WRITING_EXTRA = 15.0          # extra degrees of forward head allowed while writing (bending over a notebook is normal)

# What counts as studying. Everything else while at the desk (phone, leaning back, looking away, sleepy) does not.
STUDY_LABELS = ("focused", "writing")

DEFAULT_CALIBRATION = {
    "head_front": 15.0, "head_side": 15.0,      # forward head angle when sitting straight, per camera
    "roll_front": 0.0, "roll_side": 0.0,        # shoulder roll
    "yaw": 0.0, "pitch": 0.0,                   # head turn / tilt while looking at the screen
    "gaze_down": 0.3,                           # eyes-down amount while looking at the screen
    "face_size": None,                          # face width in the picture (distance to the screen)
    "yaw2": None, "pitch2": None,               # head direction when studying on a second screen/laptop (optional)
}
SECOND_SCREEN_TOLERANCE = 20.0                  # degrees around the second screen that still count as looking at it


@dataclass(frozen=True)
class PoseReading:
    head: float   # forward head angle in degrees (0 = ear straight above the shoulder)
    roll: float   # shoulder roll in degrees (0 = level)


@dataclass(frozen=True)
class FaceReading:
    blink: float        # 0 eyes open .. 1 closed (average of both eyes)
    yaw: float          # degrees, head turned left or right
    pitch_down: float   # degrees, positive = head tilted down
    gaze_down: float    # 0..1, eyes looking down
    size: float         # eye-corner distance as a fraction of the picture width


@dataclass(frozen=True)
class Assessment:
    state: str                  # "good", "bad", "unsure" (at the desk but cannot judge) or "away"
    reasons: tuple = ()         # forward_head, shoulder_tilt, leaning_in
    head_delta: float = None    # degrees beyond your straight posture (negative = better)


# ---------------------------------------------------------------- posture

def head_and_roll(world, vis):
    """
    (forward head angle, shoulder roll), both in degrees, from 3D body points; (None, None)
    when the ears and shoulders are not clearly seen. `world` maps landmark number to (x, y, z)
    in metres with y pointing down; `vis` maps landmark number to visibility 0..1.
    """
    need = (L_EAR, R_EAR, L_SHOULDER, R_SHOULDER)
    if any(i not in world or vis.get(i, 0.0) < MIN_VISIBILITY for i in need):
        return None, None
    ls, rs = world[L_SHOULDER], world[R_SHOULDER]
    v = (rs[0] - ls[0], rs[1] - ls[1], rs[2] - ls[2])
    width = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    flat = math.hypot(v[0], v[2])
    if width < 0.15 or flat < 0.05:          # shoulders implausibly close together: the 3D guess is bad
        return None, None
    roll = abs(math.degrees(math.asin(max(-1.0, min(1.0, v[1] / width)))))
    # Front-to-back axis: horizontal and perpendicular to the shoulder line.
    sx, sz = v[2] / flat, -v[0] / flat
    ear = [(world[L_EAR][k] + world[R_EAR][k]) / 2 for k in range(3)]
    sh = [(ls[k] + rs[k]) / 2 for k in range(3)]
    forward = (ear[0] - sh[0]) * sx + (ear[2] - sh[2]) * sz
    rise = sh[1] - ear[1]                    # y points down, so the ear is higher when this is positive
    if rise < 0.03:
        return None, None
    return math.degrees(math.atan2(abs(forward), rise)), roll


def assess_posture(poses, face, cal, sensitivity="medium", writing=False):
    """
    One verdict from both cameras. `poses` maps "front"/"side" to a PoseReading or None;
    `face` is a FaceReading or None. While `writing` (head down over a notebook) the head may
    come further forward before it counts, and getting closer to the desk is not "leaning in".
    """
    limits = SENSITIVITY[sensitivity]
    seen = [c for c in CAMERAS if poses.get(c) is not None]
    if not seen and face is None:
        return Assessment("away")
    reasons = []
    head_delta = None
    if seen:
        head_delta = sum(poses[c].head - cal["head_" + c] for c in seen) / len(seen)
        if head_delta > limits["head"] + (WRITING_EXTRA if writing else 0.0):
            reasons.append("forward_head")
    # Shoulder tilt only from the front camera. From the side the two shoulders overlap and the
    # 3D guess for their tilt is wrong (seen live: 20+ degrees while the front camera saw 3).
    front = poses.get("front")
    if front is not None and front.roll - cal["roll_front"] > limits["roll"]:
        reasons.append("shoulder_tilt")
    if not writing and face is not None and cal.get("face_size") and face.size > cal["face_size"] * limits["lean"]:
        reasons.append("leaning_in")
    if not seen and not reasons:
        return Assessment("unsure")           # only a face is visible: cannot judge the back
    return Assessment("bad" if reasons else "good", tuple(reasons), head_delta)


# ---------------------------------------------------------------- face and focus

def head_angles(matrix):
    """
    (yaw, pitch_down) in degrees from MediaPipe's 4x4 face transformation matrix (nested lists).
    ASSUMPTION: the matrix uses y up and z toward the camera, so a head tilted down makes the
    third column's y negative. Calibration removes any fixed offset; only the sign matters.
    """
    r = [row[:3] for row in matrix[:3]]
    norm = math.sqrt(sum(r[i][2] ** 2 for i in range(3))) or 1.0
    yaw = math.degrees(math.atan2(r[0][2], r[2][2]))
    pitch_down = math.degrees(math.asin(max(-1.0, min(1.0, -r[1][2] / norm))))
    return yaw, pitch_down


def classify_focus(face, cal, phone=False):
    """
    One instant's activity label:
      phone         a phone is in your hand (seen by a camera, with a hand next to it)
      no_face       the face is not visible
      eyes_closed   eyes shut
      writing       head down with no phone: writing in a notebook or reading something on the desk
      looking_away  head turned or tipped back
      leaning_back  sitting back from the screen
      focused       looking at the screen
    Studying is "focused" or "writing" (see STUDY_LABELS).
    """
    if phone:
        return "phone"
    if face is None:
        return "no_face"
    if face.blink > EYES_CLOSED_BLINK:
        return "eyes_closed"
    if cal.get("yaw2") is not None and cal.get("pitch2") is not None \
            and abs(face.yaw - cal["yaw2"]) <= SECOND_SCREEN_TOLERANCE:
        # turned toward the second screen: that is studying, and head-down there is writing
        return "writing" if face.pitch_down - cal["pitch2"] > PITCH_DOWN_LIMIT else "focused"
    pitch = face.pitch_down - cal["pitch"]
    if pitch > PITCH_DOWN_LIMIT or face.gaze_down - cal["gaze_down"] > GAZE_DOWN_LIMIT and pitch > PITCH_DOWN_LIMIT / 2:
        return "writing"
    if abs(face.yaw - cal["yaw"]) > YAW_LIMIT or pitch < -PITCH_UP_LIMIT:
        return "looking_away"
    if cal.get("face_size") and face.size < cal["face_size"] * LEAN_BACK:
        return "leaning_back"
    return "focused"


def phone_in_hand(phones, hands, margin=0.4):
    """
    True when a hand is at a phone. `phones` are (x, y, width, height) boxes, `hands` are (x, y)
    points (wrists and fingers) in the same picture. A phone on its own on the desk does not count.
    """
    for px, py, pw, ph in phones:
        grow_x, grow_y = pw * margin + 15, ph * margin + 15
        for hx, hy in hands:
            if px - grow_x <= hx <= px + pw + grow_x and py - grow_y <= hy <= py + ph + grow_y:
                return True
    return False


class EyeClosure:
    """Share of the last `window` seconds the eyes were closed (drowsiness). Needs enough history to trust."""

    def __init__(self, window=20.0, min_history=10.0):
        self.window = window
        self.min_history = min_history
        self._samples = deque()

    def add(self, now, closed):
        self._samples.append((now, bool(closed)))
        while self._samples and now - self._samples[0][0] > self.window:
            self._samples.popleft()

    def fraction(self):
        if len(self._samples) < 2 or self._samples[-1][0] - self._samples[0][0] < self.min_history:
            return 0.0
        return sum(1 for _, c in self._samples if c) / len(self._samples)

    def drowsy(self, threshold=0.4):
        return self.fraction() >= threshold


class HoldFilter:
    """
    A label only changes after the new value has lasted `hold` seconds (`quick` gives shorter
    times for particular labels), so a glance or a blink does not count.
    """

    def __init__(self, hold=3.0, quick=None, initial="focused"):
        self.hold = hold
        self.quick = quick or {}
        self.state = initial
        self._candidate = None
        self._since = 0.0

    def update(self, now, raw):
        if raw == self.state:
            self._candidate = None
            return self.state
        if raw != self._candidate:
            self._candidate, self._since = raw, now
        if now - self._since >= self.quick.get(raw, self.hold):
            self.state, self._candidate = raw, None
        return self.state


# ---------------------------------------------------------------- calibration, smoothing, alerts

def calibrate(pose_samples, face_samples):
    """
    The calibration after sitting up straight and looking at the screen. `pose_samples` maps
    camera name to a list of PoseReading; `face_samples` is a list of FaceReading. Each value is
    the median; anything with fewer than 3 samples keeps its default.
    """
    cal = dict(DEFAULT_CALIBRATION)
    for cam in CAMERAS:
        samples = pose_samples.get(cam, [])
        if len(samples) >= 3:
            cal["head_" + cam] = statistics.median(s.head for s in samples)
            cal["roll_" + cam] = statistics.median(s.roll for s in samples)
    if len(face_samples) >= 3:
        cal["yaw"] = statistics.median(f.yaw for f in face_samples)
        cal["pitch"] = statistics.median(f.pitch_down for f in face_samples)
        cal["gaze_down"] = statistics.median(f.gaze_down for f in face_samples)
        cal["face_size"] = statistics.median(f.size for f in face_samples)
    return cal


class StateFilter:
    """
    Stops the posture verdict flickering. A change between good, bad and unsure needs `confirm`
    samples in a row; coming back to the desk counts at once; "away" only starts after
    `away_after` seconds with nobody seen.
    """

    def __init__(self, confirm=4, away_after=10.0):
        self.confirm = confirm
        self.away_after = away_after
        self.state = "away"
        self._candidate = None
        self._count = 0
        self._last_seen = None

    def update(self, now, raw):
        if raw != "away":
            self._last_seen = now
        if raw == "away":
            self._candidate, self._count = None, 0
            if self.state != "away" and self._last_seen is not None and now - self._last_seen >= self.away_after:
                self.state = "away"
            return self.state
        if raw == self.state:
            self._candidate, self._count = None, 0
            return self.state
        if raw == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = raw, 1
        needed = 1 if self.state == "away" else self.confirm
        if self._count >= needed:
            self.state = raw
            self._candidate, self._count = None, 0
        return self.state


class AlertTimer:
    """True once when the state has been "bad" for `after` seconds, then not again for `cooldown` seconds."""

    def __init__(self, after=60.0, cooldown=300.0):
        self.after = after
        self.cooldown = cooldown
        self._bad_since = None
        self._last_alert = None

    def update(self, now, state):
        if state != "bad":
            self._bad_since = None
            return False
        if self._bad_since is None:
            self._bad_since = now
        if now - self._bad_since < self.after:
            return False
        if self._last_alert is not None and now - self._last_alert < self.cooldown:
            return False
        self._last_alert = now
        return True
