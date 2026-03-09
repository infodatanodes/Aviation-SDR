# Pi Kiosk Dashboard Design

**Date**: 2026-03-09
**Status**: Approved
**Replaces**: airband_display.py (curses tty1 display)

## Overview

Replace the ASCII curses display with a full graphical HTML dashboard rendered in Chromium kiosk mode on the Pi's 15.6" 1080p HDMI monitor. View-only, no mouse/keyboard.

## Architecture

### Components

1. **`dashboard_server.py`** — Python HTTP server (port 8080)
   - Serves `dashboard.html` at `/`
   - Exposes `/api/stats` JSON endpoint (polled every 2s by frontend)
   - Reads rtl_airband stats files (Prometheus text format)
   - Reads last N entries from `aviation_scan_log.csv`
   - Reads Pi system health (temp, load, RAM, disk)
   - Checks Icecast status via localhost:8010
   - Takes over file management from airband_display.py:
     - Watches recordings dir for new MP3s
     - Renames files with frequency/channel names
     - Writes CSV log entries
     - Writes status JSON

2. **`dashboard.html`** — Single-page dashboard
   - Dark theme, ops center aesthetic
   - 1920x1080 fixed layout
   - `setInterval(fetch('/api/stats'), 2000)` for live updates
   - CSS animations for live indicators, signal bar transitions
   - Color-coded channels

3. **`airband-dashboard.service`** — systemd service
   - Replaces `airband-display.service`
   - Runs `dashboard_server.py`
   - Restart=always

4. **Chromium kiosk** — autostart via wayfire.ini
   - `chromium-browser http://localhost:8080 --kiosk --noerrdialogs --disable-infobars --no-first-run --ozone-platform=wayland --start-maximized`
   - Cursor hidden via ydotool
   - Screen blanking disabled

### Data Flow

```
rtl_airband stats files ──→ dashboard_server.py ──→ /api/stats JSON
Pi system (temp/load/etc) ─→ dashboard_server.py ──→ /api/stats JSON
aviation_scan_log.csv ─────→ dashboard_server.py ──→ /api/stats JSON
Icecast localhost:8010 ────→ dashboard_server.py ──→ /api/stats JSON
                                     │
                              dashboard.html (fetch every 2s)
                                     │
                              Chromium kiosk (localhost:8080)
```

### `/api/stats` Response Shape

```json
{
  "clock": "09:42:15",
  "uptime": "4d 3h 12m",
  "dongles": {
    "approach": {
      "serial": "00000001",
      "label": "DFW Approach",
      "centerfreq": "132.922",
      "channels": [
        {
          "freq": "132.922",
          "name": "DFW Approach",
          "signal_dbfs": -40.5,
          "noise_dbfs": -40.9,
          "squelch_opens": 548,
          "active": true
        }
      ]
    },
    "scanner": {
      "serial": "00000002",
      "label": "Multichannel Scanner",
      "centerfreq": "125.425",
      "channels": [...]
    }
  },
  "recent_transmissions": [
    {
      "timestamp": "05:42:15",
      "frequency": "132.922",
      "name": "DFW Approach",
      "duration": 3.5
    }
  ],
  "stats": {
    "today_total": 312,
    "top_channel": "DFW Approach",
    "top_count": 142,
    "pipeline_total": 5893
  },
  "health": {
    "temp_c": 47.2,
    "cpu_load": 0.34,
    "ram_used_mb": 416,
    "ram_total_mb": 3796,
    "disk_percent": 26,
    "icecast_mounts": 2
  }
}
```

## Dashboard Layout (1920x1080)

### Sections (top to bottom)

1. **Title Bar** (48px) — "SPACENODES AVIATION SDR AIRBAND MONITOR", clock, uptime
2. **Dongle Panels** (~400px) — Two columns, left=Approach, right=Scanner
   - Per-channel cards: frequency, name, signal gauge, squelch count, live/idle indicator
3. **Activity Timeline** (~120px) — Horizontal bar chart, last 6 hours of transmission density
4. **Bottom Split** (~300px) — Left=Stats+Health, Right=Recent Transmissions scroll

### Visual Style
- Background: #0a0a0a (near black)
- Card backgrounds: #1a1a2e
- Accent colors: #00bfff (approach), #00ff88 (departure), #ff6b35 (clearance), #a855f7 (regional)
- Live indicator: pulsing glow animation
- Signal bars: gradient fill with smooth CSS transitions
- Font: monospace (system), white text

## Kiosk Setup on Pi

1. Install: `sudo apt install ydotool chromium-browser` (if not present)
2. Enable Desktop Autologin via `raspi-config`
3. Create `/home/pi/run_kiosk.sh` and `/home/pi/hide_cursor.sh`
4. Edit `~/.config/wayfire.ini` autostart section
5. Add `screensaver = false` and `dpms = false`
6. Add daily 3 AM cron: restart Chromium to prevent memory bloat

## Migration Plan

- `airband-display.service` disabled, replaced by `airband-dashboard.service`
- `airband_display.py` kept in repo but no longer active on Pi
- File management (MP3 rename, CSV logging) moves into `dashboard_server.py`
- Existing cron jobs (transfer_recordings, transfer_logs) unchanged
