"""Checks for metrics.py. Run: py -3.12 test_metrics.py   (prints a summary; exits 1 on any failure)"""
import math
import sys

import metrics as m
from metrics import FaceReading, PoseReading

passed, failures = 0, []


def check(name, ok):
    global passed
    if ok:
        passed += 1
    else:
        failures.append(name)


def near(a, b, tol=0.2):
    return a is not None and abs(a - b) <= tol


ALL = {m.L_EAR: 0.9, m.R_EAR: 0.9, m.L_SHOULDER: 0.9, m.R_SHOULDER: 0.9}


def body(ear_forward=0.0, ear_up=0.17, roll_dy=0.0, yaw_deg=0.0, shoulder_half=0.18):
    """3D points for a person turned `yaw_deg` away from facing the camera. Metres, y down."""
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))

    def turn(x, z):
        return x * c + z * s, -x * s + z * c

    pts = {}
    lx, lz = turn(-shoulder_half, 0.0)
    rx, rz = turn(shoulder_half, 0.0)
    pts[m.L_SHOULDER] = (lx, 0.0, lz)
    pts[m.R_SHOULDER] = (rx, roll_dy, rz)
    fx, fz = turn(0.0, -ear_forward)            # forward = toward the camera when yaw is 0
    for ear, side in ((m.L_EAR, -0.07), (m.R_EAR, 0.07)):
        ex, ez = turn(side, 0.0)
        pts[ear] = (ex + fx, -ear_up, ez + fz)
    return pts


# ---- 3D head and shoulders ----
h, r = m.head_and_roll(body(), ALL)
check("upright: 0 degrees forward, level", near(h, 0) and near(r, 0))
h, r = m.head_and_roll(body(ear_forward=0.085), ALL)
check("8.5 cm forward over a 17 cm rise = 26.6 degrees", near(h, 26.565, 0.3))
for yaw in (0, 30, 60, 90, -45):
    h, _ = m.head_and_roll(body(ear_forward=0.085, yaw_deg=yaw), ALL)
    check(f"same answer from any camera angle (turned {yaw})", near(h, 26.565, 0.5))
h, r = m.head_and_roll(body(roll_dy=0.036), ALL)
check("shoulder roll about 11.5 degrees", near(r, math.degrees(math.asin(0.036 / math.hypot(0.36, 0.036))), 0.3))
bad_vis = dict(ALL)
bad_vis[m.L_EAR] = 0.2
check("a poorly seen ear gives no reading", m.head_and_roll(body(), bad_vis) == (None, None))
check("missing landmarks", m.head_and_roll({}, {}) == (None, None))
check("ear below shoulder is unusable", m.head_and_roll(body(ear_up=-0.05), ALL) == (None, None))
check("shoulders nearly on top of each other", m.head_and_roll(body(shoulder_half=0.02), ALL) == (None, None))

# ---- posture verdict ----
cal = dict(m.DEFAULT_CALIBRATION)
cal["face_size"] = 0.20
good = PoseReading(head=16.0, roll=1.0)
face_ok = FaceReading(blink=0.1, yaw=0.0, pitch_down=0.0, gaze_down=0.3, size=0.20)
a = m.assess_posture({"front": good, "side": good}, face_ok, cal)
check("good with both cameras", a.state == "good" and not a.reasons)
a = m.assess_posture({"front": PoseReading(40, 1), "side": PoseReading(44, 1)}, face_ok, cal)
check("forward head is bad", a.state == "bad" and a.reasons == ("forward_head",) and a.head_delta > 20)
a = m.assess_posture({"front": PoseReading(40, 1), "side": PoseReading(16, 1)}, face_ok, cal)
check("one camera bad, one fine: the average decides", a.state == "good" or a.reasons == ("forward_head",))
a = m.assess_posture({"front": PoseReading(16, 14), "side": None}, face_ok, cal)
check("shoulder tilt is bad", a.state == "bad" and a.reasons == ("shoulder_tilt",))
a = m.assess_posture({"front": good, "side": PoseReading(16, 30)}, face_ok, cal)
check("the side camera's shoulder tilt is ignored (it overlaps from the side)", a.state == "good")
a = m.assess_posture({"front": None, "side": PoseReading(16, 30)}, None, cal)
check("side camera alone never reports shoulder tilt", a.state == "good" and not a.reasons)
a = m.assess_posture({"front": good, "side": None}, FaceReading(0.1, 0, 0, 0.3, 0.27), cal)
check("leaning toward the screen is bad", a.state == "bad" and a.reasons == ("leaning_in",))
a = m.assess_posture({"front": PoseReading(40, 14), "side": None}, FaceReading(0.1, 0, 0, 0.3, 0.27), cal)
check("all three reasons", set(a.reasons) == {"forward_head", "shoulder_tilt", "leaning_in"})
check("front only", m.assess_posture({"front": good, "side": None}, None, cal).state == "good")
check("side only", m.assess_posture({"front": None, "side": good}, None, cal).state == "good")
check("nobody seen", m.assess_posture({"front": None, "side": None}, None, cal).state == "away")
check("only a face: cannot judge the back", m.assess_posture({"front": None, "side": None}, face_ok, cal).state == "unsure")
check("no lean check without a calibrated face size",
      m.assess_posture({"front": good, "side": None}, FaceReading(0.1, 0, 0, 0.3, 0.9), dict(m.DEFAULT_CALIBRATION)).state == "good")
check("a naturally forward-sitter is judged against their own calibration",
      m.assess_posture({"front": PoseReading(32, 1), "side": None}, None, dict(cal, head_front=30.0)).state == "good")
check("strict mode flags 9 degrees", m.assess_posture({"front": PoseReading(25, 1), "side": None}, None, cal, "high").state == "bad")
check("relaxed mode allows 15 degrees", m.assess_posture({"front": PoseReading(30, 1), "side": None}, None, cal, "low").state == "good")
check("medium flags 15 degrees", m.assess_posture({"front": PoseReading(30, 1), "side": None}, None, cal, "medium").state == "bad")

# ---- head angles from the face matrix ----
def rot_y(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]]


def rot_x(deg):   # the face's forward axis (third column) becomes (0, -sin, cos): y up, so positive deg = pointing DOWN
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]]


yaw, pitch = m.head_angles(rot_y(0))
check("facing the camera", near(yaw, 0) and near(pitch, 0))
yaw, pitch = m.head_angles(rot_y(30))
check("turned 30 degrees", near(yaw, 30) and near(pitch, 0))
yaw, pitch = m.head_angles(rot_y(-38))
check("turned the other way", near(yaw, -38))
yaw, pitch = m.head_angles(rot_x(20))
check("forward axis pointing 20 degrees down is looking down (positive pitch_down)", near(pitch, 20) and near(yaw, 0))
yaw, pitch = m.head_angles(rot_x(-25))
check("forward axis pointing up is looking up (negative pitch_down)", near(pitch, -25))

# ---- focus labels ----
fcal = dict(m.DEFAULT_CALIBRATION)
def F(blink=0.1, yaw=0.0, pitch=0.0, gaze=0.3):
    return FaceReading(blink, yaw, pitch, gaze, 0.2)

check("no face", m.classify_focus(None, fcal) == "no_face")
check("looking at the screen", m.classify_focus(F(), fcal) == "focused")
check("eyes closed", m.classify_focus(F(blink=0.8), fcal) == "eyes_closed")
check("head down with no phone is writing", m.classify_focus(F(pitch=35), fcal) == "writing")
check("head turned away", m.classify_focus(F(yaw=50), fcal) == "looking_away")
check("head turned the other way", m.classify_focus(F(yaw=-50), fcal) == "looking_away")
check("head tipped back", m.classify_focus(F(pitch=-40), fcal) == "looking_away")
check("eyes down with head slightly down is writing", m.classify_focus(F(pitch=14, gaze=0.8), fcal) == "writing")
check("eyes down but head level is still reading the screen", m.classify_focus(F(pitch=0, gaze=0.8), fcal) == "focused")
check("a head turned 38 degrees is normal when that is how you sit", m.classify_focus(F(yaw=-38), dict(fcal, yaw=-38.0)) == "focused")
check("looking at the screen from below the webcam", m.classify_focus(F(gaze=0.5), dict(fcal, gaze_down=0.5)) == "focused")

# ---- phone, leaning back, writing ----
ccal = dict(m.DEFAULT_CALIBRATION, face_size=0.20)
def FS(size, **kw):
    return FaceReading(kw.get("blink", 0.1), kw.get("yaw", 0.0), kw.get("pitch", 0.0), kw.get("gaze", 0.3), size)

check("a phone in hand beats everything", m.classify_focus(F(), fcal, phone=True) == "phone" and m.classify_focus(None, fcal, phone=True) == "phone")
check("phone with the head down is a phone, not writing", m.classify_focus(F(pitch=35), fcal, phone=True) == "phone")
check("sitting back from the screen", m.classify_focus(FS(0.15), ccal) == "leaning_back")
check("a slightly smaller face is still focused", m.classify_focus(FS(0.18), ccal) == "focused")
check("leaning back cannot be judged without a calibrated size", m.classify_focus(FS(0.05), fcal) == "focused")
check("writing is not leaning back", m.classify_focus(FS(0.15, pitch=35), ccal) == "writing")
check("studying means focused or writing", set(m.STUDY_LABELS) == {"focused", "writing"})
check("a hand holding a phone", m.phone_in_hand([(100, 100, 40, 80)], [(130, 190)]))
check("a hand far from the phone", not m.phone_in_hand([(100, 100, 40, 80)], [(400, 400)]))
check("a phone alone on the desk", not m.phone_in_hand([(100, 100, 40, 80)], []))
check("no phone", not m.phone_in_hand([], [(5, 5)]))
wcal = dict(m.DEFAULT_CALIBRATION, face_size=0.20)
hunched = {"front": PoseReading(35, 1), "side": None}
check("bent over a notebook is not bad posture", m.assess_posture(hunched, FS(0.2), wcal, "medium", writing=True).state == "good")
check("the same head angle at the screen is bad", m.assess_posture(hunched, FS(0.2), wcal, "medium", writing=False).state == "bad")
check("extreme hunching over a notebook is still bad", m.assess_posture({"front": PoseReading(50, 1), "side": None}, FS(0.2), wcal, "medium", writing=True).state == "bad")
check("closer to the desk while writing is not leaning in", m.assess_posture({"front": PoseReading(15, 1), "side": None}, FS(0.3), wcal, "medium", writing=True).state == "good")

# ---- eye closure (drowsiness) ----
ec = m.EyeClosure(window=20, min_history=10)
for t in range(5):
    ec.add(t, True)
check("not enough history to call it drowsy", not ec.drowsy())
ec = m.EyeClosure(window=20, min_history=10)
for t in range(20):
    ec.add(t, t % 2 == 0)
check("half the time closed is drowsy", ec.drowsy() and abs(ec.fraction() - 0.5) < 0.06)
ec = m.EyeClosure(window=20, min_history=10)
for t in range(30):
    ec.add(t, t % 10 == 0)
check("normal blinking is not drowsy", not ec.drowsy())

# ---- hold filter ----
hf = m.HoldFilter(hold=3.0, quick={"focused": 1.0})
check("starts focused", hf.state == "focused")
check("a short glance away does nothing", hf.update(0, "looking_away") == "focused" and hf.update(2.9, "looking_away") == "focused")
check("3 s away counts", hf.update(3.1, "looking_away") == "looking_away")
check("coming back is quick", hf.update(4.0, "focused") == "looking_away" and hf.update(5.1, "focused") == "focused")
hf = m.HoldFilter(hold=3.0)
hf.update(0, "looking_down")
hf.update(2, "focused")
check("an interruption restarts the clock", hf.update(3.5, "looking_down") == "focused" and hf.update(6.6, "looking_down") == "looking_down")

# ---- calibration ----
fs = [F(yaw=-30 + i, pitch=5 + i, gaze=0.4) for i in range(5)]
c = m.calibrate({"front": [PoseReading(10, 1), PoseReading(14, 3), PoseReading(12, 2)], "side": []}, fs)
check("posture calibration is the median", near(c["head_front"], 12) and near(c["roll_front"], 2))
check("a camera without samples keeps its default", c["head_side"] == m.DEFAULT_CALIBRATION["head_side"])
check("face calibration is the median", near(c["yaw"], -28) and near(c["pitch"], 7) and near(c["gaze_down"], 0.4, 0.01) and near(c["face_size"], 0.2, 0.001))
check("calibrating on nothing gives the defaults", m.calibrate({}, []) == m.DEFAULT_CALIBRATION)
check("two samples are not enough", m.calibrate({"front": [PoseReading(10, 1), PoseReading(14, 3)]}, [])["head_front"] == 15.0)

# ---- state filter ----
f = m.StateFilter(confirm=3, away_after=10)
check("starts away", f.state == "away")
check("returning to the desk counts at once", f.update(0, "good") == "good")
check("one bad sample does not flip it", f.update(1, "bad") == "good")
check("a good sample resets the count", f.update(2, "good") == "good" and f.update(3, "bad") == "good" and f.update(4, "bad") == "good")
check("three in a row flips it", f.update(5, "bad") == "bad")
check("brief away stays bad", f.update(6, "away") == "bad" and f.update(10, "away") == "bad")
check("away after the grace period", f.update(16, "away") == "away")
check("returning from away is immediate", f.update(17, "bad") == "bad")
check("unsure also needs confirming", f.update(18, "unsure") == "bad" and f.update(19, "unsure") == "bad" and f.update(20, "unsure") == "unsure")
f2 = m.StateFilter(confirm=3, away_after=10)
check("never seen stays away", f2.update(0, "away") == "away")

# ---- alert timer ----
t = m.AlertTimer(after=60, cooldown=300)
check("no alert while good", not t.update(0, "good"))
check("no alert before 60 s", not t.update(10, "bad") and not t.update(69, "bad"))
check("alert at 60 s", t.update(70, "bad"))
check("no repeat inside the cooldown", not t.update(100, "bad") and not t.update(369, "bad"))
check("repeat after the cooldown", t.update(371, "bad"))
t.update(400, "good")
check("good resets the timer", not t.update(410, "bad") and not t.update(450, "bad"))
t = m.AlertTimer(after=60, cooldown=300)
t.update(0, "bad")
check("unsure resets the timer", not t.update(30, "unsure") and not t.update(61, "bad"))

# second screen: studying on another laptop off to the side
_cal2 = dict(m.DEFAULT_CALIBRATION, yaw=0.0, pitch=0.0, gaze_down=0.3, face_size=0.2, yaw2=50.0, pitch2=5.0)
_f = lambda yaw, pitch: FaceReading(0.1, yaw, pitch, 0.3, 0.2)
check("turned toward the second screen is studying", m.classify_focus(_f(52, 6), _cal2) == "focused")
check("head down at the second screen is writing", m.classify_focus(_f(48, 40), _cal2) == "writing")
check("turned the other way is still looking away", m.classify_focus(_f(-50, 5), _cal2) == "looking_away")
check("without a second screen the same turn is looking away", m.classify_focus(_f(52, 6), dict(_cal2, yaw2=None, pitch2=None)) == "looking_away")

print(f"test_metrics: {passed} passed, {len(failures)} failed")
for name in failures:
    print("  FAIL", name)
sys.exit(1 if failures else 0)

