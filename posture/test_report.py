"""Checks for posture_report.summarize. Run: py -3.12 test_report.py   (exits 1 on any failure)"""
import sys
from datetime import datetime, timedelta

import posture_report as r

passed, failures = 0, []


def check(name, ok):
    global passed
    if ok:
        passed += 1
    else:
        failures.append(name)


def make(blocks, start=datetime(2026, 11, 12, 10, 0, 0)):
    """blocks: [(state, seconds, reasons)] laid end to end, one row per second."""
    rows, t = [], start
    for state, seconds, reasons in blocks:
        for _ in range(seconds):
            rows.append((t, state, list(reasons)))
            t += timedelta(seconds=1)
    return rows


s = r.summarize(make([("good", 600, ()), ("bad", 300, ("forward_head",)), ("away", 360, ()), ("good", 1200, ())]))
check("tracked", s["tracked"] == 2460)
check("at desk", s["at_desk"] == 2100)
check("good and bad", s["good"] == 1800 and s["bad"] == 300)
check("good percent", abs(s["good_pct"] - 100 * 1800 / 2100) < 0.01)
check("one break of 6 min", s["breaks"] == 1)
check("longest stretch is the last 20 min", abs(s["longest"] - 1200) < 2)
check("reason seconds", s["reasons"]["forward_head"] == 300)
check("hourly buckets", s["hourly"][10][0] > 0)

s = r.summarize(make([("good", 600, ()), ("away", 120, ()), ("good", 600, ())]))
check("a 2 min away is not a break", s["breaks"] == 0)
check("one long stretch across it", abs(s["longest"] - 1320) < 2)

s = r.summarize(make([("away", 600, ())]))
check("only away: nothing at the desk", s["at_desk"] == 0 and s["good_pct"] is None and s["breaks"] == 0 and s["longest"] == 0)
s = r.summarize([])
check("empty day", s["tracked"] == 0 and s["first"] is None and s["worst_hour"] is None)

s = r.summarize(make([("bad", 400, ("leaning_in", "shoulder_tilt")), ("good", 50, ())]))
check("worst hour needs 5 minutes of data", s["worst_hour"] is not None and s["worst_hour"][0] == 10)
check("every reason counted", s["reasons"]["leaning_in"] == 400 and s["reasons"]["shoulder_tilt"] == 400)
s = r.summarize(make([("bad", 200, ("forward_head",))]))
check("too little data for a worst hour", s["worst_hour"] is None)

check("12-hour clock", r.clock(datetime(2026, 1, 1, 15, 40)) == "3:40 PM" and r.clock(datetime(2026, 1, 1, 0, 5)) == "12:05 AM")
check("durations", r.duration(3720) == "1 h 02 min" and r.duration(540) == "9 min")
check("under a minute is shown in seconds", r.duration(45) == "45 s" and r.duration(0) == "0 s" and r.duration(60) == "1 min")

print(f"test_report: {passed} passed, {len(failures)} failed")
for name in failures:
    print("  FAIL", name)
sys.exit(1 if failures else 0)
