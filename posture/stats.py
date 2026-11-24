"""
Numbers for the app's Today tab, read from the engine's daily CSV files. Pure Python
(uses posture_report for the posture part) so test_stats.py can check it anywhere.
"""
import csv
import os
from datetime import date, datetime

import posture_report

FOCUS_LABELS = ("focused", "writing", "phone", "leaning_back", "looking_away", "drowsy", "unknown")
STUDY_LABELS = ("focused", "writing")
# Labels written by older versions, folded into the current ones
OLD_LABELS = {"looking_down": "writing", "eyes_closed": "drowsy"}


def read_rows(path):
    """[(datetime, state, reasons, focus)] from one day's CSV; damaged rows are skipped."""
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                when = datetime.fromisoformat(r["time"])
                state = r["state"]
                if state not in ("good", "bad", "unsure", "away"):
                    raise ValueError(state)
                rows.append((when, state, [x for x in (r.get("reasons") or "").split(";") if x], r.get("focus") or "unknown"))
            except (KeyError, ValueError, TypeError):
                continue
    rows.sort(key=lambda r: r[0])
    return rows


def summarize(rows):
    """
    Posture numbers (posture_report.summarize) plus activity: seconds per label while at the desk.
    Studying = looking at the screen or writing; phone, sitting back, looking away and sleepy are not.
    """
    s = posture_report.summarize([(w, st, rs) for w, st, rs, _ in rows])
    focus = {label: 0 for label in FOCUS_LABELS}
    for _, state, _, label in rows:
        if state != "away":
            label = OLD_LABELS.get(label, label)
            focus[label if label in focus else "unknown"] += 1
    judged = s["at_desk"] - focus["unknown"]
    studying = sum(focus[k] for k in STUDY_LABELS)
    s["focus"] = focus
    s["studying"] = studying
    s["focus_pct"] = (100.0 * studying / judged) if judged > 0 else None
    return s


def today(data_dir, day=None):
    day = day or date.today()
    path = os.path.join(data_dir, f"posture-{day.isoformat()}.csv")
    rows = read_rows(path)
    return rows, summarize(rows)
