#!/usr/bin/env python3
"""
dashboard_server.py — Aviation SDR Pi Kiosk Dashboard Server

HTTP server (port 8080) that:
  - Serves dashboard.html at GET /
  - Exposes GET /api/stats with live system + radio data
  - Runs background file management (MP3 rename, CSV log, status JSON)

Replaces the curses display from airband_display.py.
Python 3 stdlib only.
"""

import os
import re
import csv
import json
import time
import signal
import shutil
import threading
import subprocess
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from collections import defaultdict
from urllib.request import urlopen
from urllib.error import URLError

# ── Paths ─────────────────────────────────────────────────────────────────
HOME = Path(os.environ.get("HOME", "/home/pi"))
RECORDINGS_DIR = HOME / "closecall" / "recordings"
CSV_LOG = HOME / "closecall" / "aviation_scan_log.csv"
STATUS_JSON = HOME / "closecall" / "listener_status.json"
EVENT_LOG = HOME / "closecall" / "airband_events.log"
STATS_APPROACH = HOME / "closecall" / "airband_approach_stats.txt"
STATS_SCAN = HOME / "closecall" / "airband_scan_stats.txt"
DASHBOARD_HTML = Path(__file__).resolve().parent / "dashboard.html"

PORT = 8080

# ── Channel config ────────────────────────────────────────────────────────
DONGLE1_CHANNELS = [
    (132922000, "DFW Approach", "132.922"),
]

DONGLE2_CHANNELS = [
    (124300000, "Regional Approach", "124.300"),
    (125025000, "DFW Departure", "125.025"),
    (126550000, "DFW Clearance", "126.550"),
]

ALL_CHANNELS = DONGLE1_CHANNELS + DONGLE2_CHANNELS
FREQ_TO_LABEL = {hz: label for hz, label, _ in ALL_CHANNELS}
FREQ_TO_MHZ = {hz: mhz for hz, _, mhz in ALL_CHANNELS}

# ── Shared state (protected by lock) ─────────────────────────────────────
_lock = threading.Lock()
channel_stats = defaultdict(lambda: {
    "hits": 0, "total_secs": 0.0, "last": None, "recordings": 0,
})
activity_log = []        # list of (datetime, label, freq_mhz, duration)
known_files = set()
total_recordings = 0
start_time = time.time()
running = True


# ── Utility ───────────────────────────────────────────────────────────────

def safe_label(label):
    """Sanitize label for filenames."""
    return re.sub(r'[^A-Za-z0-9_]', '_', label)


def parse_airband_filename(fname):
    """Parse SDR_YYYYMMDD_HHMMSS_FREQHZ.mp3 → (datetime, freq_hz) or None."""
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
    """Rename MP3 with channel label: SDR_DFW_Approach_132.922MHz_20260308_220927.mp3"""
    label = FREQ_TO_LABEL.get(freq_hz, f"Unknown_{freq_hz}")
    mhz = FREQ_TO_MHZ.get(freq_hz, f"{freq_hz / 1e6:.3f}")
    safe = safe_label(label)
    ts = dt.strftime("%Y%m%d_%H%M%S")
    new_name = f"SDR_{safe}_{mhz}MHz_{ts}.mp3"
    new_path = fpath.parent / new_name
    if new_path != fpath:
        shutil.move(str(fpath), str(new_path))
    return new_path, label, mhz


def get_mp3_duration(fpath):
    """Estimate MP3 duration from file size (~2000 bytes/sec at 16 kbps)."""
    try:
        size = fpath.stat().st_size
        return max(0.5, size / 2000.0)
    except OSError:
        return 1.0


def log_to_csv(dt, freq_mhz, label, duration, recording_file):
    """Append a row to the aviation scan CSV log."""
    write_header = not CSV_LOG.exists()
    with open(CSV_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp", "frequency_mhz", "channel_name", "mode",
                         "duration_secs", "peak_audio_level", "recording_file"])
        w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), freq_mhz, label, "am",
                     f"{duration:.1f}", "", recording_file])


def log_event(msg):
    """Write a timestamped line to the event log."""
    try:
        with open(EVENT_LOG, "a") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except OSError:
        pass


def update_status_json():
    """Write listener_status.json (consumed by transfer scripts)."""
    with _lock:
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
            "recordings_this_hour": sum(
                1 for _, s in channel_stats.items()
                if s["last"] and s["last"] > datetime.now() - timedelta(hours=1)
            ),
            "total_recordings": total_recordings,
            "uptime_secs": round(time.time() - start_time, 1),
            "top_channels": top_channels,
            "stream_clients": 0,
            "timestamp": datetime.now().isoformat(),
        }
    tmp = STATUS_JSON.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(status, f, indent=2)
        tmp.rename(STATUS_JSON)
    except OSError:
        pass


# ── Prometheus stats parsing ──────────────────────────────────────────────

_METRIC_RE = re.compile(
    r'^(channel_\w+)\{freq="([^"]+)",label="([^"]+)"\}\s+([-\d.]+)',
)


def parse_stats_file(path):
    """Parse rtl_airband Prometheus text file into dict keyed by (freq, label)."""
    results = {}
    try:
        with open(path) as f:
            for line in f:
                m = _METRIC_RE.match(line.strip())
                if not m:
                    continue
                metric, freq, label, value = m.group(1), m.group(2), m.group(3), m.group(4)
                key = (freq, label)
                if key not in results:
                    results[key] = {}
                try:
                    results[key][metric] = float(value)
                except ValueError:
                    pass
    except OSError:
        pass
    return results


# ── System health ─────────────────────────────────────────────────────────

def get_pi_temp():
    """Read SoC temperature via vcgencmd. Returns float or None."""
    try:
        out = subprocess.check_output(
            ["vcgencmd", "measure_temp"], timeout=2, stderr=subprocess.DEVNULL
        ).decode().strip()
        m = re.search(r'([\d.]+)', out)
        return float(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def get_cpu_load():
    """Read 1-minute load average from /proc/loadavg."""
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def get_ram_info():
    """Read MemTotal and MemAvailable from /proc/meminfo. Returns (total_mb, used_mb)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:"):
                    info[parts[0].rstrip(":")] = int(parts[1])  # kB
        total_kb = info.get("MemTotal", 0)
        avail_kb = info.get("MemAvailable", 0)
        total_mb = round(total_kb / 1024)
        used_mb = round((total_kb - avail_kb) / 1024)
        return total_mb, used_mb
    except (OSError, ValueError, KeyError):
        return 0, 0


def get_disk_percent():
    """Disk usage percentage for root filesystem."""
    try:
        usage = shutil.disk_usage("/")
        return round(usage.used / usage.total * 100)
    except OSError:
        return 0


def get_icecast_mounts():
    """Count active Icecast sources via localhost:8010/status-json.xsl."""
    try:
        with urlopen("http://localhost:8010/status-json.xsl", timeout=2) as resp:
            data = json.loads(resp.read().decode())
        sources = data.get("icestats", {}).get("source", [])
        if isinstance(sources, dict):
            return 1
        if isinstance(sources, list):
            return len(sources)
        return 0
    except (URLError, OSError, json.JSONDecodeError, KeyError):
        return 0


# ── CSV stats helpers ─────────────────────────────────────────────────────

def _read_csv_rows():
    """Read all CSV rows. Returns list of dicts."""
    if not CSV_LOG.exists():
        return []
    try:
        with open(CSV_LOG) as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def compute_csv_stats(rows):
    """Compute today_total, top_channel, top_count, pipeline_total from CSV rows."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_total = 0
    channel_counts = defaultdict(int)
    for row in rows:
        ts = row.get("timestamp", "")
        name = row.get("channel_name", "Unknown")
        if ts.startswith(today_str):
            today_total += 1
        channel_counts[name] += 1

    pipeline_total = len(rows)
    if channel_counts:
        top_channel = max(channel_counts, key=channel_counts.get)
        top_count = channel_counts[top_channel]
    else:
        top_channel = ""
        top_count = 0

    return {
        "today_total": today_total,
        "top_channel": top_channel,
        "top_count": top_count,
        "pipeline_total": pipeline_total,
    }


def get_recent_transmissions(rows, limit=20):
    """Return the last N transmissions from CSV rows."""
    recent = []
    for row in rows[-limit:]:
        try:
            dt = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            freq = row.get("frequency_mhz", "")
            name = row.get("channel_name", "Unknown")
            dur = float(row.get("duration_secs", 0))
            recent.append({
                "timestamp": dt.strftime("%H:%M:%S"),
                "frequency": freq,
                "name": name,
                "duration": round(dur, 1),
            })
        except (ValueError, KeyError):
            continue
    recent.reverse()
    return recent


def get_active_channels(rows):
    """Return set of channel names that had a transmission in the last 30 seconds."""
    cutoff = datetime.now() - timedelta(seconds=30)
    active = set()
    for row in rows[-100:]:  # only check recent tail
        try:
            dt = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            if dt >= cutoff:
                active.add(row.get("channel_name", ""))
        except (ValueError, KeyError):
            continue
    return active


# ── Build /api/stats response ────────────────────────────────────────────

def format_uptime():
    """Format uptime as '4d 3h 12m'."""
    elapsed = int(time.time() - start_time)
    days, rem = divmod(elapsed, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def build_channel_entry(freq_mhz, name, stats_data, active_channels):
    """Build a single channel dict for the API response."""
    key = (freq_mhz, name)
    metrics = stats_data.get(key, {})
    return {
        "freq": freq_mhz,
        "name": name,
        "signal_dbfs": round(metrics.get("channel_dbfs_signal_level", 0.0), 1),
        "noise_dbfs": round(metrics.get("channel_dbfs_noise_level", 0.0), 1),
        "squelch_opens": int(metrics.get("channel_squelch_counter", 0)),
        "active": name in active_channels,
    }


def build_stats_response():
    """Assemble the full /api/stats JSON response."""
    now = datetime.now()

    # Parse Prometheus stats files
    approach_stats = parse_stats_file(STATS_APPROACH)
    scan_stats = parse_stats_file(STATS_SCAN)
    all_stats = {**approach_stats, **scan_stats}

    # Read CSV
    rows = _read_csv_rows()
    active_channels = get_active_channels(rows)

    # Build dongle channel lists
    approach_channels = []
    for _, name, mhz in DONGLE1_CHANNELS:
        approach_channels.append(build_channel_entry(mhz, name, all_stats, active_channels))

    scanner_channels = []
    for _, name, mhz in DONGLE2_CHANNELS:
        scanner_channels.append(build_channel_entry(mhz, name, all_stats, active_channels))

    # Health
    temp = get_pi_temp()
    cpu = get_cpu_load()
    ram_total, ram_used = get_ram_info()
    disk_pct = get_disk_percent()
    icecast = get_icecast_mounts()

    return {
        "clock": now.strftime("%H:%M:%S"),
        "uptime": format_uptime(),
        "dongles": {
            "approach": {
                "serial": "00000001",
                "label": "DFW Approach",
                "centerfreq": "132.922",
                "channels": approach_channels,
            },
            "scanner": {
                "serial": "00000002",
                "label": "Multichannel Scanner",
                "centerfreq": "125.425",
                "channels": scanner_channels,
            },
        },
        "recent_transmissions": get_recent_transmissions(rows),
        "stats": compute_csv_stats(rows),
        "health": {
            "temp_c": round(temp, 1) if temp is not None else None,
            "cpu_load": round(cpu, 2) if cpu is not None else None,
            "ram_used_mb": ram_used,
            "ram_total_mb": ram_total,
            "disk_percent": disk_pct,
            "icecast_mounts": icecast,
        },
    }


# ── File management (background thread) ──────────────────────────────────

def process_new_files():
    """Watch recordings dir, rename new MP3s, log to CSV and events."""
    global total_recordings

    if not RECORDINGS_DIR.exists():
        return

    for fname in sorted(os.listdir(RECORDINGS_DIR)):
        if not fname.endswith(".mp3"):
            continue

        with _lock:
            if fname in known_files:
                continue

        fpath = RECORDINGS_DIR / fname
        try:
            age = time.time() - fpath.stat().st_mtime
            if age < 3:
                continue  # file still being written
        except OSError:
            continue

        parsed = parse_airband_filename(fname)
        if not parsed:
            with _lock:
                known_files.add(fname)
            continue

        dt, freq_hz = parsed

        with _lock:
            known_files.add(fname)

        new_path, label, mhz = rename_for_pipeline(fpath, dt, freq_hz)
        duration = get_mp3_duration(new_path)

        with _lock:
            stats = channel_stats[label]
            stats["hits"] += 1
            stats["total_secs"] += duration
            stats["last"] = dt
            stats["recordings"] += 1
            total_recordings += 1
            known_files.add(new_path.name)
            activity_log.append((dt, label, mhz, duration))
            if len(activity_log) > 500:
                del activity_log[:100]

        log_to_csv(dt, mhz, label, duration, new_path.name)
        log_event(f"RX {label} ({mhz} MHz) {duration:.1f}s")
        update_status_json()


def load_existing_csv():
    """Bootstrap in-memory state from existing CSV log."""
    global total_recordings
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
                    total_recordings += 1
                except (ValueError, KeyError):
                    continue
        if len(activity_log) > 500:
            del activity_log[:-500]
    except OSError:
        pass


def file_manager_loop():
    """Background thread: process new files every 2 seconds."""
    while running:
        try:
            process_new_files()
        except Exception as exc:
            log_event(f"file_manager error: {exc}")
        time.sleep(2)


# ── HTTP server ───────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    """Handles GET / (dashboard HTML) and GET /api/stats (JSON)."""

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/api/stats":
            self._serve_stats()
        else:
            self.send_error(404)

    def _serve_html(self):
        try:
            content = DASHBOARD_HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, "dashboard.html not found")

    def _serve_stats(self):
        try:
            payload = build_stats_response()
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            log_event(f"stats error: {exc}")
            self.send_error(500)

    def log_message(self, format, *args):
        """Suppress default access log spam — dashboard polls every 2s."""
        pass


# ── Signal handling & main ────────────────────────────────────────────────

def _shutdown_handler(sig, frame):
    global running
    running = False


def main():
    global running

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    # Seed known_files so we don't reprocess existing recordings on startup
    if RECORDINGS_DIR.exists():
        for f in os.listdir(RECORDINGS_DIR):
            known_files.add(f)

    load_existing_csv()
    log_event("Dashboard server starting")
    update_status_json()

    # Start background file manager
    fm_thread = threading.Thread(target=file_manager_loop, daemon=True, name="file-manager")
    fm_thread.start()

    # Start HTTP server
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    server.timeout = 1  # allow clean shutdown checks

    log_event(f"Listening on port {PORT}")
    print(f"Aviation SDR dashboard: http://localhost:{PORT}")

    try:
        while running:
            server.handle_request()
    except Exception as exc:
        log_event(f"Server error: {exc}")
    finally:
        server.server_close()
        running = False
        fm_thread.join(timeout=5)
        log_event("Dashboard server stopped")
        update_status_json()
        print("Shutdown complete.")


if __name__ == "__main__":
    main()
