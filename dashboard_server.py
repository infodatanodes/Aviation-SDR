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
from urllib.request import urlopen, Request
from urllib.error import URLError

# ── Paths ─────────────────────────────────────────────────────────────────
HOME = Path(os.environ.get("HOME", "/home/pi"))
RECORDINGS_DIR = HOME / "closecall" / "recordings"
CSV_LOG = HOME / "closecall" / "aviation_scan_log.csv"
STATUS_JSON = HOME / "closecall" / "listener_status.json"
EVENT_LOG = HOME / "closecall" / "airband_events.log"
STATS_APPROACH = HOME / "closecall" / "airband_approach_stats.txt"
STATS_SCAN = HOME / "closecall" / "airband_scan_stats.txt"
ADSB_JSON = Path("/run/readsb/aircraft.json")
ACARS_LOG = HOME / "closecall" / "acars_messages.jsonl"
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
        ts = row.get("timestamp", row.get("dtg", ""))
        name = row.get("channel_name", row.get("identification", "Unknown"))
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


def _parse_duration(row):
    """Extract duration from CSV row, handling old column layout.

    Old CSV has duration_sec='am' (mode) and peak_rms=actual duration.
    New CSV has duration_secs=actual duration.
    """
    for col in ("duration_secs", "duration_sec", "peak_rms"):
        val = row.get(col)
        if val:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return 0.0


def get_recent_transmissions(rows, limit=20):
    """Return the last N transmissions from CSV rows."""
    recent = []
    for row in rows[-limit:]:
        try:
            ts_str = row.get("timestamp", row.get("dtg", ""))
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            freq = row.get("frequency_mhz", "")
            name = row.get("channel_name", row.get("identification", "Unknown"))
            dur = _parse_duration(row)
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
            ts_str = row.get("timestamp", row.get("dtg", ""))
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            if dt >= cutoff:
                active.add(row.get("channel_name", row.get("identification", "")))
        except (ValueError, KeyError):
            continue
    return active


def get_channel_sparkline(rows, minutes=30):
    """Return per-channel activity bucketed into 1-minute slots for sparklines.

    Returns dict: channel_name -> list of 30 ints (transmission counts per minute,
    oldest first).
    """
    cutoff = datetime.now() - timedelta(minutes=minutes)
    buckets = defaultdict(lambda: [0] * minutes)
    now = datetime.now()
    for row in rows:
        try:
            ts_str = row.get("timestamp", row.get("dtg", ""))
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            if dt < cutoff:
                continue
            name = row.get("channel_name", row.get("identification", ""))
            mins_ago = int((now - dt).total_seconds() / 60)
            idx = minutes - 1 - mins_ago
            if 0 <= idx < minutes:
                buckets[name][idx] += 1
        except (ValueError, KeyError):
            continue
    return dict(buckets)


# ── ADS-B + ACARS data ───────────────────────────────────────────────────

def get_adsb_aircraft():
    """Read aircraft from readsb JSON file."""
    try:
        with open(ADSB_JSON) as f:
            data = json.load(f)
        aircraft = []
        for a in data.get("aircraft", []):
            hex_code = a.get("hex", "")
            flight = a.get("flight", "").strip()
            if not flight and not hex_code:
                continue
            aircraft.append({
                "hex": hex_code,
                "flight": flight or hex_code.upper(),
                "alt": a.get("alt_baro"),
                "speed": a.get("gs"),
                "rssi": a.get("rssi"),
                "squawk": a.get("squawk"),
                "distance": a.get("r_dst"),
                "seen": a.get("seen", 0),
            })
        aircraft.sort(key=lambda x: (x["distance"] if x["distance"] is not None else 999))
        return {
            "count": len(data.get("aircraft", [])),
            "aircraft": aircraft[:25],
        }
    except (OSError, json.JSONDecodeError):
        return {"count": 0, "aircraft": []}


def get_acars_messages():
    """Read recent ACARS messages from JSONL log file."""
    messages = []
    try:
        if not ACARS_LOG.exists():
            return messages
        with open(ACARS_LOG) as f:
            lines = f.readlines()
        for line in lines[-15:]:
            try:
                msg = json.loads(line.strip())
                ts = msg.get("timestamp", 0)
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts)
                    ts_str = dt.strftime("%H:%M:%S")
                else:
                    ts_str = str(ts)
                text = (msg.get("text") or "").strip()
                label = msg.get("label", "")
                # Skip empty keepalive/polling messages (_d with no text)
                if not text and label == "_d":
                    continue
                messages.append({
                    "timestamp": ts_str,
                    "flight": (msg.get("flight") or "").strip(),
                    "tail": (msg.get("tail") or "").strip(),
                    "label": label,
                    "text": text[:100] if text else label,
                    "freq": msg.get("freq", ""),
                    "level": msg.get("level"),
                    "error": msg.get("error", 0),
                })
            except (json.JSONDecodeError, ValueError):
                continue
        messages.reverse()
    except OSError:
        pass
    return messages


def get_acars_parsed(limit=50):
    """Read and parse ACARS messages into structured data for Spacenodes.

    Uses acars_parser module for comprehensive message parsing across all
    ACARS label types (positions, OOOI, weather, engine, maintenance, etc).
    """
    from acars_parser import parse_acars_message

    messages = []
    try:
        if not ACARS_LOG.exists():
            return messages
        with open(ACARS_LOG) as f:
            lines = f.readlines()
        for line in lines[-limit:]:
            try:
                msg = json.loads(line.strip())
                text = (msg.get("text") or "").strip()
                label = msg.get("label", "")

                if not text and label == "_d":
                    continue

                ts = msg.get("timestamp", 0)
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts)
                    ts_iso = dt.isoformat()
                else:
                    ts_iso = str(ts)

                parsed_data = parse_acars_message(msg)

                result = {
                    "timestamp": ts_iso,
                    "flight": (msg.get("flight") or "").strip(),
                    "tail": (msg.get("tail") or "").strip(),
                    "label": label,
                    "text": text,
                    "freq": msg.get("freq", ""),
                    "level": msg.get("level"),
                    "error": msg.get("error", 0),
                    "category": parsed_data["category"],
                    "parsed": parsed_data["parsed"],
                }

                messages.append(result)
            except (json.JSONDecodeError, ValueError):
                continue
        messages.reverse()
    except OSError:
        pass
    return messages


# ── Emergency squawk detection + Discord alerts ─────────────────────────

EMERGENCY_SQUAWKS = {
    "7500": "HIJACK",
    "7600": "RADIO FAILURE",
    "7700": "EMERGENCY",
}

DISCORD_WEBHOOK = os.environ.get(
    "DISCORD_WEBHOOK_AIR_TRAFFIC",
    "https://discord.com/api/webhooks/1471574249016918017/4iOzhaXMBTyk0W-Td7zBwTj1esu13QUohqTMJGYGbY5gYOpixvX515-gEN_QelMDXtcN",
)

EMERGENCY_LOG = HOME / "closecall" / "emergency_squawks.log"

# Track alerted aircraft to avoid spamming (hex -> last_alert_time)
_alerted_emergencies = {}
_ALERT_COOLDOWN = 300  # seconds before re-alerting same aircraft


def check_emergency_squawks(adsb_data):
    """Check for emergency squawk codes, log and alert via Discord."""
    emergencies = []
    now = time.time()

    for aircraft in adsb_data.get("aircraft", []):
        squawk = aircraft.get("squawk", "")
        if squawk not in EMERGENCY_SQUAWKS:
            continue
        if aircraft.get("seen", 99) > 30:
            continue  # stale data

        hex_code = aircraft.get("hex", "unknown")
        flight = (aircraft.get("flight") or "").strip() or hex_code.upper()
        alert_type = EMERGENCY_SQUAWKS[squawk]
        alt = aircraft.get("alt_baro", "?")
        speed = aircraft.get("gs")
        lat = aircraft.get("lat")
        lon = aircraft.get("lon")
        dist = aircraft.get("r_dst")

        emergency = {
            "type": alert_type,
            "squawk": squawk,
            "flight": flight,
            "hex": hex_code,
            "alt": alt,
            "speed": round(speed) if speed else None,
            "lat": lat,
            "lon": lon,
            "distance": round(dist, 1) if dist else None,
        }
        emergencies.append(emergency)

        # Check cooldown
        last_alert = _alerted_emergencies.get(hex_code, 0)
        if now - last_alert < _ALERT_COOLDOWN:
            continue

        _alerted_emergencies[hex_code] = now

        # Log to file
        try:
            with open(EMERGENCY_LOG, "a") as f:
                f.write(f"{datetime.now().isoformat()} SQUAWK {squawk} "
                        f"({alert_type}) {flight} alt={alt} "
                        f"lat={lat} lon={lon} dist={dist}\n")
        except OSError:
            pass

        # Discord alert
        _send_discord_alert(emergency)

    return emergencies


def _send_discord_alert(emergency):
    """Send emergency squawk alert to Discord."""
    if not DISCORD_WEBHOOK:
        return

    squawk = emergency["squawk"]
    alert_type = emergency["type"]
    flight = emergency["flight"]
    alt = emergency["alt"]
    dist = emergency.get("distance")
    lat = emergency.get("lat")
    lon = emergency.get("lon")
    speed = emergency.get("speed")

    color = {"7500": 0xFF0000, "7600": 0xFF8800, "7700": 0xFF0000}.get(squawk, 0xFF0000)
    emoji = {"7500": "🚨", "7600": "📻", "7700": "🆘"}.get(squawk, "⚠️")

    location = ""
    if lat and lon:
        location = f"\n**Position:** {lat:.4f}, {lon:.4f}"
    if dist:
        location += f" ({dist} nm away)"

    desc_parts = [f"**Aircraft:** {flight}", f"**Altitude:** {alt} ft"]
    if speed:
        desc_parts.append(f"**Speed:** {speed} kt")
    if location:
        desc_parts.append(location.strip())

    payload = {
        "embeds": [{
            "title": f"{emoji} SQUAWK {squawk} — {alert_type}",
            "description": "\n".join(desc_parts),
            "color": color,
            "footer": {"text": "Aviation SDR — Pi Scanner"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }],
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(DISCORD_WEBHOOK, data=data,
                      headers={"Content-Type": "application/json"})
        urlopen(req, timeout=5)
        log_event(f"Discord alert sent: SQUAWK {squawk} {flight}")
    except (URLError, OSError) as e:
        log_event(f"Discord alert failed: {e}")


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


def get_last_active(rows):
    """Return dict of channel_name -> seconds since last transmission."""
    now = datetime.now()
    last = {}
    for row in rows:
        try:
            ts_str = row.get("timestamp", row.get("dtg", ""))
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            name = row.get("channel_name", row.get("identification", ""))
            if name not in last or dt > last[name]:
                last[name] = dt
        except (ValueError, KeyError):
            continue
    return {name: int((now - dt).total_seconds()) for name, dt in last.items()}


def get_channel_history(rows, limit_per_channel=15):
    """Return per-channel recent transmission lists.

    Returns dict: channel_name -> list of {timestamp, duration} dicts (newest first).
    """
    history = defaultdict(list)
    for row in rows:
        try:
            ts_str = row.get("timestamp", row.get("dtg", ""))
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            name = row.get("channel_name", row.get("identification", "Unknown"))
            dur = _parse_duration(row)
            history[name].append({
                "timestamp": dt.strftime("%H:%M:%S"),
                "duration": round(dur, 1),
            })
        except (ValueError, KeyError):
            continue
    # Keep only last N per channel, newest first
    return {name: entries[-limit_per_channel:][::-1] for name, entries in history.items()}


def build_channel_entry(freq_mhz, name, stats_data, active_channels,
                        sparkline_data, last_active_data, channel_history):
    """Build a single channel dict for the API response."""
    key = (freq_mhz, name)
    metrics = stats_data.get(key, {})
    return {
        "freq": freq_mhz,
        "name": name,
        "squelch_opens": int(metrics.get("channel_squelch_counter", 0)),
        "active": name in active_channels,
        "sparkline": sparkline_data.get(name, [0] * 30),
        "last_active_secs": last_active_data.get(name),
        "history": channel_history.get(name, []),
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
    sparkline_data = get_channel_sparkline(rows)
    last_active_data = get_last_active(rows)
    channel_history = get_channel_history(rows)

    # Build dongle channel lists
    approach_channels = []
    for _, name, mhz in DONGLE1_CHANNELS:
        approach_channels.append(build_channel_entry(
            mhz, name, all_stats, active_channels, sparkline_data, last_active_data, channel_history))

    scanner_channels = []
    for _, name, mhz in DONGLE2_CHANNELS:
        scanner_channels.append(build_channel_entry(
            mhz, name, all_stats, active_channels, sparkline_data, last_active_data, channel_history))

    # ADS-B + ACARS (parsed with category/decoded data)
    adsb = get_adsb_aircraft()
    acars = get_acars_parsed(limit=30)

    # Emergency squawk check (runs on raw aircraft.json data)
    try:
        with open(ADSB_JSON) as f:
            raw_adsb = json.load(f)
        emergencies = check_emergency_squawks(raw_adsb)
    except (OSError, json.JSONDecodeError):
        emergencies = []

    # Flatten all channels for the new per-freq layout
    all_channels = approach_channels + scanner_channels

    return {
        "clock": now.strftime("%H:%M:%S"),
        "uptime": format_uptime(),
        "adsb": adsb,
        "acars": acars,
        "emergencies": emergencies,
        "channels": all_channels,
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
                    ts_str = row.get("timestamp", row.get("dtg", ""))
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    label = row.get("channel_name", row.get("identification", "Unknown"))
                    freq = row.get("frequency_mhz", "?")
                    dur = _parse_duration(row)
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
        elif self.path == "/api/acars":
            self._serve_acars_full()
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

    def _serve_acars_full(self):
        """Serve parsed ACARS messages with extracted positions and flight plans."""
        try:
            payload = get_acars_parsed()
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            log_event(f"acars parse error: {exc}")
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
