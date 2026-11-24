"""
Turns the tracker's daily CSV files into one HTML report: hours at the desk, posture, breaks,
an hour-by-hour chart and a timeline of the day.

  py posture_report.py                 last 7 days, opens the report in your browser
  py posture_report.py --days 30       last 30 days
  py posture_report.py --no-open       just write posture-report.html
  py posture_report.py --data-dir DIR  read CSV files from another folder
"""
import base64
import csv
import io
import os
import sys
import webbrowser
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
POSE_LIBS = r"C:\Users\jasme\posture-pylibs"
if os.path.isdir(POSE_LIBS):
    sys.path.insert(0, POSE_LIBS)

BREAK_SECONDS = 300      # away this long counts as a break
GAP_SECONDS = 5          # more than this between rows means the tracker was off

REASON_TEXT = {"forward_head": "Head too far forward", "shoulder_tilt": "Shoulders uneven", "leaning_in": "Leaning toward the screen"}


def clock(dt):
    """12-hour clock without a leading zero: 3:40 PM."""
    return dt.strftime("%I:%M %p").lstrip("0")


def duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h} h {m:02d} min" if h else f"{m} min"


def read_day(path):
    """[(datetime, state, [reasons])] from one CSV file; damaged rows are skipped and counted."""
    rows, skipped = [], 0
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                when = datetime.fromisoformat(r["time"])
                state = r["state"]
                if state not in ("good", "bad", "unsure", "away"):
                    raise ValueError(state)
                rows.append((when, state, [x for x in (r.get("reasons") or "").split(";") if x]))
            except (KeyError, ValueError, TypeError):
                skipped += 1
    rows.sort(key=lambda r: r[0])
    return rows, skipped


def summarize(rows):
    """Numbers for one day from rows of (datetime, state, reasons). Each row stands for one second."""
    good = sum(1 for r in rows if r[1] == "good")
    bad = sum(1 for r in rows if r[1] == "bad")
    unsure = sum(1 for r in rows if r[1] == "unsure")      # at the desk, but posture could not be judged
    away = sum(1 for r in rows if r[1] == "away")
    at_desk = good + bad + unsure
    reasons = Counter(x for r in rows if r[1] == "bad" for x in r[2])

    # Stretches at the desk: presence rows closer together than BREAK_SECONDS belong to one stretch.
    stretches, start, last = [], None, None
    for when, state, _ in rows:
        if state == "away":
            continue
        if start is None:
            start = last = when
        elif (when - last).total_seconds() >= BREAK_SECONDS:
            stretches.append((start, last))
            start = last = when
        else:
            last = when
    if start is not None:
        stretches.append((start, last))
    longest = max(((b - a).total_seconds() + 1 for a, b in stretches), default=0)

    hourly = defaultdict(lambda: [0, 0])     # hour -> [good seconds, bad seconds]
    for when, state, _ in rows:
        if state in ("good", "bad"):
            hourly[when.hour][0 if state == "good" else 1] += 1
    worst = None
    for hour, (g, b) in hourly.items():
        if g + b >= 300 and (worst is None or b / (g + b) > worst[1]):
            worst = (hour, b / (g + b))

    return {
        "tracked": len(rows), "at_desk": at_desk, "good": good, "bad": bad, "unsure": unsure, "away": away,
        "good_pct": (100.0 * good / (good + bad)) if good + bad else None,
        "breaks": max(0, len(stretches) - 1), "stretches": stretches, "longest": longest,
        "reasons": reasons, "hourly": dict(hourly), "worst_hour": worst,
        "first": rows[0][0] if rows else None, "last": rows[-1][0] if rows else None,
    }


# ---------------------------------------------------------------- charts and page

def _png(fig):
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _style(ax, fig):
    fig.patch.set_facecolor("#12172a")
    ax.set_facecolor("#12172a")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#4a5578")
    ax.tick_params(colors="#b9c4e6")
    ax.xaxis.label.set_color("#b9c4e6")
    ax.yaxis.label.set_color("#b9c4e6")
    ax.title.set_color("#eef2ff")


def hour_label(h):
    return f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}"


def chart_hourly(summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    hours = list(range(24))
    good = [summary["hourly"].get(h, [0, 0])[0] / 60 for h in hours]
    bad = [summary["hourly"].get(h, [0, 0])[1] / 60 for h in hours]
    fig, ax = plt.subplots(figsize=(8.5, 2.8))
    _style(ax, fig)
    ax.bar(hours, good, color="#4fd18b", label="Good posture")
    ax.bar(hours, bad, bottom=good, color="#ff6b6b", label="Slouching")
    ax.set_xticks(range(0, 24, 3))
    ax.set_xticklabels([hour_label(h) for h in range(0, 24, 3)])
    ax.set_ylabel("Minutes at desk")
    ax.set_ylim(0, 60)
    ax.set_title("Hour by hour")
    ax.legend(frameon=False, labelcolor="#eef2ff", loc="upper left", ncol=2)
    return _png(fig)


def chart_timeline(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import ListedColormap
    codes = np.zeros(1440)                       # 0 not tracked, 1 away, 2 good, 3 bad (worst state in that minute)
    code = {"away": 1, "unsure": 2, "good": 2, "bad": 3}
    for when, state, _ in rows:
        i = when.hour * 60 + when.minute
        codes[i] = max(codes[i], code[state])
    fig, ax = plt.subplots(figsize=(8.5, 1.3))
    _style(ax, fig)
    ax.imshow(codes[np.newaxis, :], aspect="auto", interpolation="nearest",
              cmap=ListedColormap(["#1d2440", "#6b7390", "#4fd18b", "#ff6b6b"]), vmin=0, vmax=3)
    ax.set_yticks([])
    ax.set_xticks(range(0, 1440, 180))
    ax.set_xticklabels([hour_label(h) for h in range(0, 24, 3)])
    ax.set_title("Timeline: green good, red slouching, grey away, dark not tracked")
    return _png(fig)


def chart_days(days):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = [d.strftime("%a %d") for d, _ in days]
    hours = [s["at_desk"] / 3600 for _, s in days]
    pct = [s["good_pct"] if s["good_pct"] is not None else 0 for _, s in days]
    fig, ax = plt.subplots(figsize=(8.5, 2.8))
    _style(ax, fig)
    ax.bar(labels, hours, color="#4d8dff")
    ax.set_ylabel("Hours at desk")
    ax2 = ax.twinx()
    ax2.plot(labels, pct, color="#ffd166", marker="o")
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("Good posture %")
    ax2.yaxis.label.set_color("#ffd166")
    ax2.tick_params(colors="#ffd166")
    for side in ("top", "right", "left", "bottom"):
        ax2.spines[side].set_visible(False)
    ax.set_title("Days: hours at desk (blue) and good posture % (yellow)")
    return _png(fig)


def tile(label, value, note=""):
    return f'<div class="tile"><div class="v">{value}</div><div class="l">{label}</div><div class="n">{note}</div></div>'


def day_section(day, rows, s):
    when = day.strftime("%A, %d %B %Y")
    if not s["tracked"]:
        return f"<h2>{when}</h2><p class='muted'>Nothing was tracked.</p>"
    pct = "n/a" if s["good_pct"] is None else f"{s['good_pct']:.0f}%"
    worst = "n/a"
    if s["worst_hour"]:
        worst = f"{hour_label(s['worst_hour'][0])} ({s['worst_hour'][1] * 100:.0f}% slouching)"
    reasons = "".join(f"<li>{REASON_TEXT.get(k, k)}: {duration(v)}</li>" for k, v in s["reasons"].most_common()) or "<li>None</li>"
    tiles = "".join([
        tile("At the desk", duration(s["at_desk"]), f"{clock(s['first'])} to {clock(s['last'])}"),
        tile("Good posture", pct, f"{duration(s['good'])} good, {duration(s['bad'])} slouching"),
        tile("Breaks of 5+ min", str(s["breaks"]), f"longest stretch {duration(s['longest'])}"),
        tile("Worst hour", worst),
    ])
    return (f"<h2>{when}</h2><div class='tiles'>{tiles}</div>"
            f"<img src='data:image/png;base64,{chart_timeline(rows)}'><img src='data:image/png;base64,{chart_hourly(s)}'>"
            f"<h3>What caused the slouching</h3><ul>{reasons}</ul>")


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Posture report</title><style>
body{{margin:0;background:#0b1020;color:#eef2ff;font:16px/1.45 system-ui,Segoe UI,sans-serif;padding:24px}}
main{{max-width:900px;margin:0 auto}}h1{{margin:0 0 4px}}h2{{margin:36px 0 10px;border-top:1px solid #26305a;padding-top:20px}}
h3{{margin:18px 0 6px;color:#b9c4e6;font-size:15px}}.muted{{color:#8a96b8}}ul{{margin:0;padding-left:20px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:12px 0}}
.tile{{background:#151d36;border:1px solid #26305a;border-radius:14px;padding:14px}}
.tile .v{{font-size:24px;font-weight:700}}.tile .l{{color:#8fb3ff;font-size:13px;text-transform:uppercase;letter-spacing:.8px}}
.tile .n{{color:#9fb0da;font-size:13px;margin-top:4px}}img{{max-width:100%;display:block;margin:10px 0}}
</style></head><body><main><h1>Posture report</h1>
<p class="muted">Made {made}. Numbers only; no video is stored.</p>{body}</main></body></html>"""


def main(argv):
    days_back, data_dir, out, do_open = 7, os.path.join(HERE, "data"), os.path.join(HERE, "posture-report.html"), True
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--no-open":
            do_open = False
        elif a in ("--days", "--data-dir", "--out"):
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} needs a value")
            v = argv[i + 1]
            if a == "--days":
                try:
                    days_back = int(v)
                except ValueError:
                    raise SystemExit("--days needs a whole number")
            elif a == "--data-dir":
                data_dir = v
            else:
                out = v
            i += 1
        else:
            raise SystemExit(f"unknown option {a}")
        i += 1

    if not os.path.isdir(data_dir):
        raise SystemExit(f"No data folder at {data_dir}. Run posture_tracker.py first.")
    today = date.today()
    days, skipped = [], 0
    for n in range(days_back - 1, -1, -1):
        day = today - timedelta(days=n)
        path = os.path.join(data_dir, f"posture-{day.isoformat()}.csv")
        if not os.path.isfile(path):
            continue
        rows, bad_rows = read_day(path)
        skipped += bad_rows
        days.append((day, rows, summarize(rows)))
    if not days:
        raise SystemExit(f"No posture files for the last {days_back} days in {data_dir}.")

    body = ""
    if len(days) > 1:
        body += f"<img src='data:image/png;base64,{chart_days([(d, s) for d, _, s in days])}'>"
    for day, rows, s in reversed(days):
        body += day_section(day, rows, s)
    if skipped:
        body += f"<p class='muted'>{skipped} damaged rows were skipped.</p>"
    with open(out, "w", encoding="utf-8") as f:
        f.write(PAGE.format(made=datetime.now().strftime("%d %B %Y, ") + clock(datetime.now()), body=body))
    print("wrote", out)
    if do_open:
        webbrowser.open("file:///" + out.replace("\\", "/"))


if __name__ == "__main__":
    main(sys.argv[1:])
