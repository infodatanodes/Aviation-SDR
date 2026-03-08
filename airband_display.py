#!/usr/bin/env python3
"""
airband_display.py — RTLSDR-Airband companion display + file manager

Two-panel layout:
  LEFT:  All monitored frequencies with names
  RIGHT: Scrolling activity log (frequency, time, duration)

Watches ~/closecall/recordings/ for new MP3 files from rtl_airband,
renames them, logs to CSV, updates status JSON.
Runs as systemd service on tty1.
"""

import os
import sys
import re
import csv
import json
import time
import signal
import curses
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# ── Paths ────────────────────────────────────────────────────────────────
HOME = Path(os.environ.get("HOME", "/home/pi"))
RECORDINGS_DIR = HOME / "closecall" / "recordings"
CSV_LOG = HOME / "closecall" / "aviation_scan_log.csv"
STATUS_JSON = HOME / "closecall" / "listener_status.json"
EVENT_LOG = HOME / "closecall" / "airband_events.log"
STATS_FILE = HOME / "closecall" / "airband_stats.txt"

# ── Channel map ──────────────────────────────────────────────────────────
CHANNELS = [    (118050000, "DFW Tower West",    "118.050"),    (118092000, "DFW Tower Area",    "118.092"),    (119050000, "DFW Tower East",    "119.050"),    (120150000, "Alliance Approach", "120.150"),    (121500000, "Emergency Guard",   "121.500"),    (121650000, "DFW Ground",        "121.650"),    (122750000, "Heli Advisory",     "122.750"),    (122900000, "Multicom",          "122.900"),    (123025000, "Unicom",            "123.025"),    (123050000, "Heli Air-to-Air",   "123.050"),    (123875000, "DFW Approach South","123.875"),    (124150000, "DFW Approach North","124.150"),    (125025000, "DFW Departure",     "125.025"),    (125350000, "Dallas Approach",   "125.350"),    (126550000, "DFW Clearance",     "126.550"),    (127000000, "Dallas Love ATIS",  "127.000"),    (128250000, "Fort Worth Center", "128.250"),    (132450000, "Meacham Tower",     "132.450"),    (132922000, "DFW Approach",      "132.922"),    (132963000, "DFW Approach 2",    "132.963"),    (132984000, "DFW Approach 3",    "132.984"),    (134900000, "Love Field Tower",  "134.900"),    (135050000, "DFW ATIS",          "135.050"),    (135575000, "Alliance Tower",    "135.575"),]

FREQ_TO_LABEL = {hz: label for hz, label, _ in CHANNELS}
FREQ_TO_MHZ = {hz: mhz for hz, _, mhz in CHANNELS}

# ── State ────────────────────────────────────────────────────────────────
channel_stats = defaultdict(lambda: {"hits": 0, "total_secs": 0.0, "last": None, "recordings": 0})
activity_log = []    # Scrolling log: (datetime, label, freq_mhz, duration_secs)
known_files = set()
start_time = time.time()
total_recordings = 0
running = True


def safe_label(label):
    return re.sub(r'[^A-Za-z0-9_]', '_', label)


def parse_airband_filename(fname):
    m = re.match(r'^SDR_(\d{8})_(\d{6})_(\d+)\.mp3$', fname)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S")
        freq_hz = int(m.group(3))
        return (dt, freq_hz)
    except (ValueError, OverflowError):
        return None


def rename_for_pipeline(fpath, dt, freq_hz):
    label = FREQ_TO_LABEL.get(freq_hz, f"Unknown_{freq_hz}")
    mhz = FREQ_TO_MHZ.get(freq_hz, f"{freq_hz/1e6:.3f}")
    safe = safe_label(label)
    ts = dt.strftime("%Y%m%d_%H%M%S")
    new_name = f"SDR_{safe}_{mhz}MHz_{ts}.mp3"
    new_path = fpath.parent / new_name
    if new_path != fpath:
        shutil.move(str(fpath), str(new_path))
    return new_path, label, mhz


def get_mp3_duration(fpath):
    try:
        size = fpath.stat().st_size
        return max(0.5, size / 2000.0)
    except OSError:
        return 1.0


def log_to_csv(dt, freq_mhz, label, duration, recording_file):
    write_header = not CSV_LOG.exists()
    with open(CSV_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp", "frequency_mhz", "channel_name", "mode",
                         "duration_secs", "peak_audio_level", "recording_file"])
        w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), freq_mhz, label, "am",
                     f"{duration:.1f}", "", recording_file])


def log_event(msg):
    try:
        with open(EVENT_LOG, "a") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except OSError:
        pass


def update_status_json():
    top = sorted(channel_stats.items(), key=lambda x: x[1]["hits"], reverse=True)
    top_channels = []
    for name, s in top[:10]:
        top_channels.append({
            "name": name,
            "hits": s["hits"],
            "total_secs": round(s["total_secs"], 1),
            "recordings": s["recordings"],
            "last": s["last"].isoformat() if s["last"] else None,
        })
    status = {
        "channel": "scanning",
        "frequency": 0,
        "state": "scanning",
        "info": f"RTLSDR-Airband scan mode, {len(CHANNELS)} freqs",
        "recordings_this_hour": sum(1 for n, s in channel_stats.items()
                                     if s["last"] and s["last"] > datetime.now() - timedelta(hours=1)),
        "total_recordings": total_recordings,
        "uptime_secs": round(time.time() - start_time, 1),
        "top_channels": top_channels,
        "stream_clients": 0,
        "timestamp": datetime.now().isoformat(),
    }
    tmp = STATUS_JSON.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(status, f, indent=2)
    tmp.rename(STATUS_JSON)


def load_existing_csv():
    """Load previous activity from CSV on startup."""
    if not CSV_LOG.exists():
        return
    try:
        with open(CSV_LOG) as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    dt = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                    label = row.get("channel_name", "Unknown")
                    freq = row.get("frequency_mhz", "?")
                    dur = float(row.get("duration_secs", 0))
                    activity_log.append((dt, label, freq, dur))
                    channel_stats[label]["hits"] += 1
                    channel_stats[label]["total_secs"] += dur
                    channel_stats[label]["last"] = dt
                    channel_stats[label]["recordings"] += 1
                except (ValueError, KeyError):
                    continue
        # Keep last 200 entries
        if len(activity_log) > 200:
            del activity_log[:-200]
    except OSError:
        pass


def process_new_files():
    global total_recordings
    if not RECORDINGS_DIR.exists():
        return

    for fname in sorted(os.listdir(RECORDINGS_DIR)):
        if fname in known_files or not fname.endswith(".mp3"):
            continue

        fpath = RECORDINGS_DIR / fname
        try:
            age = time.time() - fpath.stat().st_mtime
            if age < 3:
                continue
        except OSError:
            continue

        parsed = parse_airband_filename(fname)
        if not parsed:
            known_files.add(fname)
            continue

        dt, freq_hz = parsed
        known_files.add(fname)

        new_path, label, mhz = rename_for_pipeline(fpath, dt, freq_hz)
        duration = get_mp3_duration(new_path)

        channel_stats[label]["hits"] += 1
        channel_stats[label]["total_secs"] += duration
        channel_stats[label]["last"] = dt
        channel_stats[label]["recordings"] += 1
        total_recordings += 1

        known_files.add(new_path.name)

        activity_log.append((dt, label, mhz, duration))
        if len(activity_log) > 500:
            del activity_log[:100]

        log_to_csv(dt, mhz, label, duration, new_path.name)
        log_event(f"RX {label} ({mhz} MHz) {duration:.1f}s")
        update_status_json()


def signal_handler(sig, frame):
    global running
    running = False


def safe_addstr(win, y, x, text, attr=0, max_x=None):
    """Write string to curses window, clipping to bounds."""
    try:
        h, w = win.getmaxyx()
        if max_x:
            w = min(w, max_x)
        if y < 0 or y >= h or x >= w:
            return
        available = w - x - 1
        if available <= 0:
            return
        win.addstr(y, x, text[:available], attr)
    except curses.error:
        pass


def draw_display(stdscr):
    global running

    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)     # Header/title
    curses.init_pair(2, curses.COLOR_CYAN, -1)      # Normal info
    curses.init_pair(3, curses.COLOR_YELLOW, -1)    # Recent activity
    curses.init_pair(4, curses.COLOR_RED, -1)       # Active now
    curses.init_pair(5, curses.COLOR_WHITE, -1)     # Default
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)   # Borders
    curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_GREEN)   # Header bar
    curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_CYAN)    # Column headers

    last_status_write = 0
    LEFT_WIDTH = 42  # Width of left panel

    while running:
        try:
            process_new_files()

            now_ts = time.time()
            if now_ts - last_status_write > 10:
                update_status_json()
                last_status_write = now_ts

            stdscr.erase()
            height, width = stdscr.getmaxyx()
            now = datetime.now()
            uptime = timedelta(seconds=int(now_ts - start_time))

            # ── Top bar ──────────────────────────────────────────────
            title = f" AVIATION SDR  DFW AIRBAND MONITOR  {now.strftime('%H:%M:%S')}  Up:{uptime}  Rx:{total_recordings} "
            safe_addstr(stdscr, 0, 0, " " * width, curses.color_pair(7))
            safe_addstr(stdscr, 0, max(0, (width - len(title)) // 2), title, curses.color_pair(7) | curses.A_BOLD)

            # ── Left panel: Frequencies ──────────────────────────────
            row = 2
            safe_addstr(stdscr, row, 1, "FREQ", curses.color_pair(8) | curses.A_BOLD, LEFT_WIDTH)
            safe_addstr(stdscr, row, 10, "CHANNEL", curses.color_pair(8) | curses.A_BOLD, LEFT_WIDTH)
            safe_addstr(stdscr, row, 30, "HITS", curses.color_pair(8) | curses.A_BOLD, LEFT_WIDTH)
            safe_addstr(stdscr, row, 36, "LAST", curses.color_pair(8) | curses.A_BOLD, LEFT_WIDTH)
            row += 1

            # Draw separator line
            safe_addstr(stdscr, row, 0, "-" * (LEFT_WIDTH - 1), curses.color_pair(6))
            row += 1

            for freq_hz, label, mhz in CHANNELS:
                if row >= height - 1:
                    break
                s = channel_stats.get(label, {"hits": 0, "total_secs": 0, "last": None})

                # Color based on recency
                if s["last"] and (now - s["last"]).total_seconds() < 120:
                    color = curses.color_pair(4) | curses.A_BOLD   # Red = last 2 min
                elif s["last"] and (now - s["last"]).total_seconds() < 600:
                    color = curses.color_pair(3)   # Yellow = last 10 min
                elif s["hits"] > 0:
                    color = curses.color_pair(2)   # Cyan = has history
                else:
                    color = curses.color_pair(5)   # White = quiet

                last_str = ""
                if s["last"]:
                    age = (now - s["last"]).total_seconds()
                    if age < 60:
                        last_str = f"{int(age)}s"
                    elif age < 3600:
                        last_str = f"{int(age/60)}m"
                    else:
                        last_str = s["last"].strftime("%H:%M")

                safe_addstr(stdscr, row, 1, mhz, color, LEFT_WIDTH)
                safe_addstr(stdscr, row, 10, label[:18], color, LEFT_WIDTH)
                safe_addstr(stdscr, row, 30, str(s["hits"]), color, LEFT_WIDTH)
                safe_addstr(stdscr, row, 36, last_str, color, LEFT_WIDTH)
                row += 1

            # ── Vertical divider ─────────────────────────────────────
            for r in range(1, height - 1):
                safe_addstr(stdscr, r, LEFT_WIDTH, "|", curses.color_pair(6))

            # ── Right panel: Activity log ────────────────────────────
            right_x = LEFT_WIDTH + 2
            right_w = width - right_x - 1

            safe_addstr(stdscr, 2, right_x, "TIME", curses.color_pair(8) | curses.A_BOLD)
            safe_addstr(stdscr, 2, right_x + 10, "FREQ", curses.color_pair(8) | curses.A_BOLD)
            safe_addstr(stdscr, 2, right_x + 19, "CHANNEL", curses.color_pair(8) | curses.A_BOLD)
            safe_addstr(stdscr, 2, right_x + 40, "DUR", curses.color_pair(8) | curses.A_BOLD)
            safe_addstr(stdscr, 3, LEFT_WIDTH + 1, "-" * (right_w + 1), curses.color_pair(6))

            # Show most recent activity, newest at top
            log_rows = height - 5  # Available rows for log entries
            visible = activity_log[-log_rows:] if activity_log else []
            visible.reverse()  # Newest first

            for i, (dt, label, freq, dur) in enumerate(visible):
                r = 4 + i
                if r >= height - 1:
                    break

                # Format duration
                if dur >= 60:
                    dur_str = f"{int(dur//60)}:{int(dur%60):02d}"
                else:
                    dur_str = f"{dur:.1f}s"

                # Color: today's entries brighter
                if dt.date() == now.date():
                    age = (now - dt).total_seconds()
                    if age < 120:
                        color = curses.color_pair(4) | curses.A_BOLD
                    elif age < 600:
                        color = curses.color_pair(3)
                    else:
                        color = curses.color_pair(2)
                else:
                    color = curses.color_pair(5)

                time_str = dt.strftime("%H:%M:%S")
                if dt.date() != now.date():
                    time_str = dt.strftime("%m/%d %H:%M")

                safe_addstr(stdscr, r, right_x, time_str, color)
                safe_addstr(stdscr, r, right_x + 10, freq, color)
                safe_addstr(stdscr, r, right_x + 19, label[:19], color)
                safe_addstr(stdscr, r, right_x + 40, dur_str, color)

            if not activity_log:
                safe_addstr(stdscr, 5, right_x + 2, "Waiting for transmissions...", curses.color_pair(5))

            # ── Bottom bar ───────────────────────────────────────────
            footer = f" rtl_airband v5 | 24 AM freqs | {now.strftime('%Y-%m-%d')} | Recordings: ~/closecall/recordings/ "
            safe_addstr(stdscr, height - 1, 0, " " * width, curses.color_pair(7))
            safe_addstr(stdscr, height - 1, max(0, (width - len(footer)) // 2), footer, curses.color_pair(7) | curses.A_BOLD)

            stdscr.refresh()
            time.sleep(1)

        except KeyboardInterrupt:
            running = False
            break


def main():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    # Pre-scan existing files
    if RECORDINGS_DIR.exists():
        for f in os.listdir(RECORDINGS_DIR):
            known_files.add(f)

    # Load historical activity from CSV
    load_existing_csv()

    log_event("Display started")
    update_status_json()

    if not os.environ.get("TERM") or os.environ.get("HEADLESS"):
        log_event("Running headless (no curses)")
        while running:
            process_new_files()
            time.sleep(1)
    else:
        curses.wrapper(draw_display)

    log_event("Display stopped")
    update_status_json()


if __name__ == "__main__":
    main()
