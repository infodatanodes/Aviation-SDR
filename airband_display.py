#!/usr/bin/env python3
"""
airband_display.py — RTLSDR-Airband companion display + file manager

Layout:
  LEFT:  Dongle status panels with signal bars, activity indicators, stats
  RIGHT: Scrolling activity log with live transmission highlighting

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

# ── Dongle assignments ───────────────────────────────────────────────────
DONGLE1_CHANNELS = [
    (132922000, "DFW Approach",      "132.922"),
]

DONGLE2_CHANNELS = [
    (124300000, "Regional Approach", "124.300"),
    (125025000, "DFW Departure",     "125.025"),
    (126550000, "DFW Clearance",     "126.550"),
]

ALL_CHANNELS = DONGLE1_CHANNELS + DONGLE2_CHANNELS
FREQ_TO_LABEL = {hz: label for hz, label, _ in ALL_CHANNELS}
FREQ_TO_MHZ = {hz: mhz for hz, _, mhz in ALL_CHANNELS}

# ── Visual elements ──────────────────────────────────────────────────────
LOGO = [
    r"     /\      SPACENODES",
    r"    /  \     AVIATION SDR",
    r"   / /\ \    AIRBAND MONITOR",
    r"  / ____ \   ~~~~~~~~~~~~~~",
    r" /_/    \_\  Pi-Scanner",
]

BAR_CHARS = " .:=|#@"  # Signal strength bar characters
SPARK_CHARS = " _.-~*"  # Sparkline characters for rate history

# ── State ────────────────────────────────────────────────────────────────
channel_stats = defaultdict(lambda: {
    "hits": 0, "total_secs": 0.0, "last": None, "recordings": 0,
    "hits_1h": 0, "rate_history": [0]*20  # Last 20 intervals for sparkline
})
activity_log = []
known_files = set()
start_time = time.time()
total_recordings = 0
running = True
tick = 0  # Animation counter
last_rate_update = 0
rate_interval_hits = defaultdict(int)  # Hits per channel in current interval


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
        "channel": "multichannel",
        "frequency": 0,
        "state": "monitoring",
        "info": f"2 dongles, {len(ALL_CHANNELS)} freqs multichannel",
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


def update_rate_history():
    """Shift sparkline history and record current interval hits."""
    global last_rate_update, rate_interval_hits
    for label in list(channel_stats.keys()):
        history = channel_stats[label]["rate_history"]
        history.pop(0)
        history.append(rate_interval_hits.get(label, 0))
    rate_interval_hits = defaultdict(int)


def load_existing_csv():
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
        rate_interval_hits[label] += 1

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


def make_activity_bar(hits, max_hits=100, width=10):
    """Create a visual bar showing relative activity."""
    if max_hits == 0:
        return " " * width
    ratio = min(1.0, hits / max_hits)
    filled = int(ratio * width)
    idx = min(len(BAR_CHARS) - 1, int(ratio * (len(BAR_CHARS) - 1)))
    char = BAR_CHARS[idx] if idx > 0 else " "
    bar = BAR_CHARS[-1] * filled + char * (1 if filled < width else 0)
    return bar.ljust(width)[:width]


def make_sparkline(history, width=20):
    """Create a sparkline from rate history."""
    if not history or max(history) == 0:
        return "." * min(width, len(history))
    peak = max(history)
    line = ""
    for val in history[-width:]:
        idx = int((val / peak) * (len(SPARK_CHARS) - 1)) if peak > 0 else 0
        line += SPARK_CHARS[idx]
    return line


def draw_dongle_panel(stdscr, start_row, dongle_num, sn, desc, channels, now, left_width, max_hits):
    """Draw a dongle panel with header, channels, activity bars, and sparklines."""
    row = start_row

    # Dongle header with status indicator
    indicator = ">>>" if tick % 2 == 0 else "   "
    # Check if any channel was active in last 30s
    any_active = False
    for _, label, _ in channels:
        s = channel_stats.get(label, {"last": None})
        if s["last"] and (now - s["last"]).total_seconds() < 30:
            any_active = True
            break

    if any_active:
        hdr_color = curses.color_pair(12) | curses.A_BOLD  # Black on green = LIVE
        indicator = "LIVE"
    else:
        hdr_color = curses.color_pair(10) | curses.A_BOLD   # Dongle header bg

    header = f" SDR #{dongle_num} (SN:{sn}) {desc} "
    safe_addstr(stdscr, row, 1, " " * (left_width - 2), hdr_color)
    safe_addstr(stdscr, row, 1, header, hdr_color)
    if any_active:
        safe_addstr(stdscr, row, left_width - 6, f" {indicator} ", curses.color_pair(13) | curses.A_BOLD | curses.A_BLINK)
    row += 1

    # Column headers
    safe_addstr(stdscr, row, 2, "FREQ", curses.color_pair(8) | curses.A_BOLD, left_width)
    safe_addstr(stdscr, row, 11, "CHANNEL", curses.color_pair(8) | curses.A_BOLD, left_width)
    safe_addstr(stdscr, row, 28, "HITS", curses.color_pair(8) | curses.A_BOLD, left_width)
    safe_addstr(stdscr, row, 34, "AGO", curses.color_pair(8) | curses.A_BOLD, left_width)
    row += 1

    for freq_hz, label, mhz in channels:
        if row >= start_row + 2 + len(channels) + 3:
            break
        s = channel_stats.get(label, {"hits": 0, "total_secs": 0, "last": None, "rate_history": [0]*20})

        # Determine color based on recency
        is_live = False
        if s["last"] and (now - s["last"]).total_seconds() < 30:
            color = curses.color_pair(11) | curses.A_BOLD   # White on red = LIVE NOW
            is_live = True
        elif s["last"] and (now - s["last"]).total_seconds() < 120:
            color = curses.color_pair(4) | curses.A_BOLD    # Red
        elif s["last"] and (now - s["last"]).total_seconds() < 600:
            color = curses.color_pair(3)                     # Yellow
        elif s["hits"] > 0:
            color = curses.color_pair(2)                     # Cyan
        else:
            color = curses.color_pair(5)                     # White dim

        # Age string
        last_str = "--"
        if s["last"]:
            age = (now - s["last"]).total_seconds()
            if age < 60:
                last_str = f"{int(age)}s"
            elif age < 3600:
                last_str = f"{int(age/60)}m"
            else:
                last_str = s["last"].strftime("%H:%M")

        # Live transmission marker
        prefix = ">>" if is_live else "  "
        safe_addstr(stdscr, row, 1, prefix, curses.color_pair(4) | curses.A_BOLD if is_live else curses.color_pair(5))
        safe_addstr(stdscr, row, 3, mhz, color, left_width)
        safe_addstr(stdscr, row, 11, label[:16], color, left_width)
        safe_addstr(stdscr, row, 28, str(s["hits"]), color, left_width)
        safe_addstr(stdscr, row, 34, last_str, color, left_width)

        # Activity bar
        bar = make_activity_bar(s["hits"], max_hits, 5)
        bar_color = curses.color_pair(1) if s["hits"] > 0 else curses.color_pair(5)
        safe_addstr(stdscr, row, 38, bar, bar_color, left_width)
        row += 1

    # Sparkline for this dongle's combined activity
    combined_history = [0] * 20
    for _, label, _ in channels:
        s = channel_stats.get(label, {"rate_history": [0]*20})
        hist = s.get("rate_history", [0]*20)
        for i in range(min(20, len(hist))):
            combined_history[i] += hist[i]

    sparkline = make_sparkline(combined_history, 20)
    safe_addstr(stdscr, row, 2, "Rate:", curses.color_pair(6), left_width)
    safe_addstr(stdscr, row, 8, sparkline, curses.color_pair(1) | curses.A_BOLD, left_width)
    row += 1

    return row


def draw_display(stdscr):
    global running, tick, last_rate_update

    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()

    # Color pairs
    curses.init_pair(1, curses.COLOR_GREEN, -1)      # Green text
    curses.init_pair(2, curses.COLOR_CYAN, -1)       # Cyan — has history
    curses.init_pair(3, curses.COLOR_YELLOW, -1)     # Yellow — recent
    curses.init_pair(4, curses.COLOR_RED, -1)        # Red — very recent
    curses.init_pair(5, curses.COLOR_WHITE, -1)      # White — default
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)    # Magenta — borders/labels
    curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_GREEN)   # Title bar
    curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_CYAN)    # Column headers
    curses.init_pair(9, curses.COLOR_GREEN, -1)      # Dongle headers text
    curses.init_pair(10, curses.COLOR_WHITE, curses.COLOR_BLUE)   # Dongle header bg
    curses.init_pair(11, curses.COLOR_WHITE, curses.COLOR_RED)    # LIVE channel
    curses.init_pair(12, curses.COLOR_BLACK, curses.COLOR_GREEN)  # LIVE dongle header
    curses.init_pair(13, curses.COLOR_RED, -1)       # LIVE indicator blink
    curses.init_pair(14, curses.COLOR_BLACK, curses.COLOR_YELLOW) # Stats bar

    last_status_write = 0
    LEFT_WIDTH = 44

    while running:
        try:
            process_new_files()

            now_ts = time.time()
            tick += 1

            # Update rate history every 30 seconds
            if now_ts - last_rate_update > 30:
                update_rate_history()
                last_rate_update = now_ts

            if now_ts - last_status_write > 10:
                update_status_json()
                last_status_write = now_ts

            stdscr.erase()
            height, width = stdscr.getmaxyx()
            now = datetime.now()
            uptime = timedelta(seconds=int(now_ts - start_time))

            # ── Title bar ────────────────────────────────────────────
            safe_addstr(stdscr, 0, 0, " " * width, curses.color_pair(7))
            title_left = f" SPACENODES // AVIATION SDR AIRBAND MONITOR"
            title_right = f"{now.strftime('%H:%M:%S')}  Up:{uptime} "
            safe_addstr(stdscr, 0, 0, title_left, curses.color_pair(7) | curses.A_BOLD)
            safe_addstr(stdscr, 0, width - len(title_right) - 1, title_right, curses.color_pair(7) | curses.A_BOLD)

            # ── Logo (left panel top) ────────────────────────────────
            row = 2
            for i, line in enumerate(LOGO):
                if row + i < height - 1:
                    logo_color = curses.color_pair(1) | curses.A_BOLD if i < 3 else curses.color_pair(2)
                    safe_addstr(stdscr, row + i, 1, line, logo_color, LEFT_WIDTH)
            row += len(LOGO) + 1

            # ── Stats summary ────────────────────────────────────────
            if row < height - 1:
                safe_addstr(stdscr, row, 1, " " * (LEFT_WIDTH - 2), curses.color_pair(14))
                stats_text = f" Rx:{total_recordings}  Channels:{len(ALL_CHANNELS)}  Dongles:2 "
                safe_addstr(stdscr, row, 1, stats_text, curses.color_pair(14) | curses.A_BOLD)
                row += 1

            row += 1

            # ── Dongle panels ────────────────────────────────────────
            max_hits = max((channel_stats.get(l, {"hits": 0})["hits"] for _, l, _ in ALL_CHANNELS), default=1) or 1

            if row < height - 4:
                row = draw_dongle_panel(stdscr, row, 1, "001", "DFW Approach",
                                        DONGLE1_CHANNELS, now, LEFT_WIDTH, max_hits)
                row += 1

            if row < height - 4:
                row = draw_dongle_panel(stdscr, row, 2, "002", "Depart/Clr",
                                        DONGLE2_CHANNELS, now, LEFT_WIDTH, max_hits)

            # ── Vertical divider (double line) ───────────────────────
            for r in range(1, height - 1):
                safe_addstr(stdscr, r, LEFT_WIDTH, "|", curses.color_pair(6))

            # ── Right panel: Activity log ────────────────────────────
            right_x = LEFT_WIDTH + 2
            right_w = width - right_x - 1

            # Right panel header
            safe_addstr(stdscr, 1, right_x, "TRANSMISSION LOG", curses.color_pair(6) | curses.A_BOLD)
            safe_addstr(stdscr, 2, right_x, "TIME", curses.color_pair(8) | curses.A_BOLD)
            safe_addstr(stdscr, 2, right_x + 10, "FREQ", curses.color_pair(8) | curses.A_BOLD)
            safe_addstr(stdscr, 2, right_x + 19, "CHANNEL", curses.color_pair(8) | curses.A_BOLD)
            safe_addstr(stdscr, 2, right_x + 38, "DUR", curses.color_pair(8) | curses.A_BOLD)

            # Separator with style
            sep = "-" * right_w
            safe_addstr(stdscr, 3, LEFT_WIDTH + 1, sep, curses.color_pair(6))

            # Activity entries
            log_rows = height - 5
            visible = activity_log[-log_rows:] if activity_log else []
            visible.reverse()

            for i, (dt, label, freq, dur) in enumerate(visible):
                r = 4 + i
                if r >= height - 1:
                    break

                if dur >= 60:
                    dur_str = f"{int(dur//60)}:{int(dur%60):02d}"
                else:
                    dur_str = f"{dur:.1f}s"

                if dt.date() == now.date():
                    age = (now - dt).total_seconds()
                    if age < 10:
                        # Brand new — flash effect
                        color = curses.color_pair(11) | curses.A_BOLD  # White on red
                    elif age < 60:
                        color = curses.color_pair(4) | curses.A_BOLD   # Red
                    elif age < 300:
                        color = curses.color_pair(3)                    # Yellow
                    elif age < 900:
                        color = curses.color_pair(2)                    # Cyan
                    else:
                        color = curses.color_pair(5)                    # White
                else:
                    color = curses.color_pair(5)

                time_str = dt.strftime("%H:%M:%S")
                if dt.date() != now.date():
                    time_str = dt.strftime("%m/%d %H:%M")

                # Arrow indicator for very recent
                arrow = ">>" if dt.date() == now.date() and (now - dt).total_seconds() < 30 else "  "
                safe_addstr(stdscr, r, right_x - 1, arrow, curses.color_pair(4) | curses.A_BOLD)
                safe_addstr(stdscr, r, right_x + 1, time_str, color)
                safe_addstr(stdscr, r, right_x + 10, freq, color)
                safe_addstr(stdscr, r, right_x + 19, label[:17], color)
                safe_addstr(stdscr, r, right_x + 38, dur_str, color)

            if not activity_log:
                # Scanning animation
                dots = "." * ((tick % 4) + 1)
                safe_addstr(stdscr, 5, right_x + 2, f"Waiting for transmissions{dots}", curses.color_pair(3))

            # ── Bottom status bar ────────────────────────────────────
            safe_addstr(stdscr, height - 1, 0, " " * width, curses.color_pair(7))

            # Scanning indicator animation
            scan_frames = ["[=     ]", "[ =    ]", "[  =   ]", "[   =  ]", "[    = ]", "[     =]",
                          "[    = ]", "[   =  ]", "[  =   ]", "[ =    ]"]
            scan_anim = scan_frames[tick % len(scan_frames)]

            footer_left = f" {scan_anim} SCANNING"
            footer_right = f" LNA+Splitter | 2x V4 | {now.strftime('%Y-%m-%d')} "
            safe_addstr(stdscr, height - 1, 0, footer_left, curses.color_pair(7) | curses.A_BOLD)
            safe_addstr(stdscr, height - 1, width - len(footer_right) - 1, footer_right, curses.color_pair(7))

            stdscr.refresh()
            time.sleep(0.5)

        except KeyboardInterrupt:
            running = False
            break


def main():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    if RECORDINGS_DIR.exists():
        for f in os.listdir(RECORDINGS_DIR):
            known_files.add(f)

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
