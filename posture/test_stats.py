"""Checks for stats.py. Run: py -3.12 test_stats.py   (exits 1 on any failure)"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

import stats

passed, failures = 0, []


def check(name, ok):
    global passed
    if ok:
        passed += 1
    else:
        failures.append(name)


def make(blocks, start=datetime(2026, 11, 12, 10, 0, 0)):
    rows, t = [], start
    for state, focus, seconds in blocks:
        for _ in range(seconds):
            rows.append((t, state, [], focus))
            t += timedelta(seconds=1)
    return rows


s = stats.summarize(make([("good", "focused", 600), ("bad", "writing", 120), ("good", "drowsy", 60), ("away", "no_face", 300)]))
check("at desk counts every non-away second", s["at_desk"] == 780)
check("focus seconds", s["focus"]["focused"] == 600 and s["focus"]["writing"] == 120 and s["focus"]["drowsy"] == 60)
check("writing counts as studying", s["studying"] == 720 and abs(s["focus_pct"] - 100 * 720 / 780) < 0.01)
s2 = stats.summarize(make([("good", "focused", 100), ("good", "phone", 50), ("good", "leaning_back", 30), ("good", "looking_away", 20)]))
check("phone, leaning back and looking away are not studying", s2["studying"] == 100 and abs(s2["focus_pct"] - 50.0) < 0.01)
check("phone and leaning back are counted", s2["focus"]["phone"] == 50 and s2["focus"]["leaning_back"] == 30)
s3 = stats.summarize(make([("good", "looking_down", 40), ("good", "eyes_closed", 10)]))
check("old labels fold into the new ones", s3["focus"]["writing"] == 40 and s3["focus"]["drowsy"] == 10)
check("away seconds are not counted as focus", s["focus"]["unknown"] == 0)

s = stats.summarize(make([("good", "unknown", 100)]))
check("no face information gives no focus percent", s["focus_pct"] is None and s["focus"]["unknown"] == 100)
check("an unrecognised label counts as unknown", stats.summarize(make([("good", "weird", 10)]))["focus"]["unknown"] == 10)
s = stats.summarize([])
check("empty day", s["at_desk"] == 0 and s["focus_pct"] is None)

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "posture-2026-11-12.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("time,state,reasons,focus,head_front\n")
        f.write("2026-11-12T10:00:00+05:30,good,,focused,12.0\n")
        f.write("2026-11-12T10:00:01+05:30,bad,forward_head;shoulder_tilt,looking_away,30.0\n")
        f.write("garbage line\n")
        f.write("2026-11-12T10:00:02+05:30,martian,,focused,1\n")
        f.write("2026-11-12T10:00:03+05:30,good,,,12.0\n")
    rows = stats.read_rows(path)
    check("damaged rows are skipped", len(rows) == 3)
    check("reasons are split", rows[1][2] == ["forward_head", "shoulder_tilt"])
    check("a missing focus value reads as unknown", rows[2][3] == "unknown")
    check("a missing file gives no rows", stats.read_rows(os.path.join(d, "nope.csv")) == [])

print(f"test_stats: {passed} passed, {len(failures)} failed")
for name in failures:
    print("  FAIL", name)
sys.exit(1 if failures else 0)
