# Pi Kiosk Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the curses tty1 display with a graphical HTML dashboard in Chromium kiosk mode on the Pi's 15.6" 1080p monitor.

**Architecture:** Python HTTP server (stdlib `http.server`) serves a single-page dashboard HTML and exposes `/api/stats` JSON endpoint. The server also handles file management (MP3 rename, CSV logging) migrated from `airband_display.py`. Chromium runs in kiosk mode via Cage (minimal Wayland compositor) on Debian Trixie.

**Tech Stack:** Python 3 stdlib (http.server, json, csv, subprocess), HTML/CSS/JS (vanilla, no frameworks), Cage (Wayland kiosk compositor), Chromium browser.

**Pi Details:** Debian Trixie (13), no desktop environment, no Wayfire. Use Cage + Chromium (both available via apt). Pi hostname: pi-scanner, IP: 100.68.206.39, user: pi.

---

### Task 1: Create dashboard_server.py — HTTP server + /api/stats endpoint

**Files:**
- Create: `dashboard_server.py`

**Context:** This server replaces `airband_display.py`. It must:
- Serve `dashboard.html` at `/`
- Expose `/api/stats` as JSON (polled every 2s by frontend)
- Parse rtl_airband Prometheus-format stats files
- Read last 15 entries from `aviation_scan_log.csv`
- Read Pi system health (temp via `vcgencmd`, load from `/proc/loadavg`, RAM from `/proc/meminfo`, disk from `shutil.disk_usage`)
- Check Icecast via `http://localhost:8010/status-json.xsl`
- Watch recordings dir for new MP3s, rename them, log to CSV (migrated from airband_display.py)

**Step 1: Write dashboard_server.py**

The server needs these functions migrated from `airband_display.py`:
- `parse_airband_filename()` — extract timestamp + freq from `SDR_YYYYMMDD_HHMMSS_FREQHZ.mp3`
- `rename_for_pipeline()` — rename with channel label and MHz
- `get_mp3_duration()` — estimate from file size
- `log_to_csv()` — append to aviation_scan_log.csv
- `log_event()` — append to airband_events.log
- `update_status_json()` — write listener_status.json
- `process_new_files()` — scan recordings dir for new MP3s

New functions:
- `parse_prometheus_stats(filepath)` — parse rtl_airband stats txt into dict
- `get_system_health()` — temp, load, RAM, disk
- `get_icecast_status()` — mount count from Icecast JSON
- `get_recent_transmissions(n)` — last N from CSV
- `build_stats_response()` — assemble full /api/stats JSON
- `FileManagerThread` — background thread running process_new_files() every 2s

Channel config (same as airband_display.py):
```python
DONGLE1_CHANNELS = [
    (132922000, "DFW Approach", "132.922"),
]
DONGLE2_CHANNELS = [
    (124300000, "Regional Approach", "124.300"),
    (125025000, "DFW Departure", "125.025"),
    (126550000, "DFW Clearance", "126.550"),
]
```

Stats file paths on Pi:
- `/home/pi/closecall/airband_approach_stats.txt`
- `/home/pi/closecall/airband_scan_stats.txt`

Prometheus format example:
```
channel_squelch_counter{freq="132.922",label="DFW Approach"}	548
channel_dbfs_signal_level{freq="132.922",label="DFW Approach"}	-40.493
channel_dbfs_noise_level{freq="132.922",label="DFW Approach"}	-40.865
channel_activity_counter{freq="132.922",label="DFW Approach"}	5776
```

HTTP handler:
- `GET /` → serve dashboard.html
- `GET /api/stats` → return JSON blob
- Everything else → 404

**Step 2: Test locally**

Run: `python3 dashboard_server.py`
Test: `curl http://localhost:8080/api/stats | python3 -m json.tool`
Expected: JSON with clock, uptime, dongles (empty channels if no stats files), health, etc.

Note: System health functions will return zeros on WSL (no vcgencmd, etc). That's fine — they'll work on Pi.

**Step 3: Commit**

```bash
git add dashboard_server.py
git commit -m "feat: add dashboard HTTP server with /api/stats endpoint"
```

---

### Task 2: Create dashboard.html — single-page dashboard

**Files:**
- Create: `dashboard.html`

**Context:** Single HTML file with embedded CSS and JS. No external dependencies. Fixed 1920x1080 layout. Fetches `/api/stats` every 2 seconds.

**Step 1: Write dashboard.html**

Layout sections (top to bottom):
1. **Title bar** (48px) — "SPACENODES AVIATION SDR AIRBAND MONITOR", live clock, uptime
2. **Dongle panels** (~400px) — Two-column grid, left=Approach dongle, right=Scanner dongle
   - Each dongle: header with serial number + LIVE/IDLE badge
   - Per-channel cards: frequency badge, name, signal gauge (CSS bar), noise gauge, squelch count, live pulse indicator
3. **Activity timeline** (~120px) — Canvas or div-based bar chart showing last 6 hours of transmission density (built from transmission timestamps)
4. **Bottom split** (~300px):
   - Left: Stats panel (today total, top channel, pipeline total) + Pi Health (temp, CPU, RAM, disk, Icecast)
   - Right: Recent transmissions scrolling log (last 15, color-coded by channel)

Visual style:
- Background: `#0a0a0a`
- Cards: `#1a1a2e` with subtle border
- Channel colors: `#00bfff` (approach), `#00ff88` (departure), `#ff6b35` (clearance), `#a855f7` (regional)
- Live indicator: CSS `@keyframes pulse` animation (glow effect)
- Signal bars: CSS width transition (0.5s ease) with gradient fill
- Font: `'Courier New', monospace`, white text `#e0e0e0`
- Active transmission: card border glows channel color

JS logic:
```javascript
async function fetchStats() {
    const res = await fetch('/api/stats');
    const data = await res.json();
    updateClock(data.clock, data.uptime);
    updateDongles(data.dongles);
    updateTimeline(data.recent_transmissions);
    updateStats(data.stats);
    updateHealth(data.health);
    updateTransmissionLog(data.recent_transmissions);
}
setInterval(fetchStats, 2000);
fetchStats();
```

Signal gauge HTML pattern:
```html
<div class="signal-bar">
    <div class="signal-fill" style="width: 65%"></div>
    <span class="signal-label">-40.5 dBFS</span>
</div>
```

**Step 2: Test locally with server**

Run: `python3 dashboard_server.py`
Open: `http://localhost:8080` in a browser
Expected: Dark dashboard with layout sections visible. Data fields show placeholder/zero values (no Pi stats files locally). Clock updates every 2s.

**Step 3: Commit**

```bash
git add dashboard.html
git commit -m "feat: add kiosk dashboard HTML with live data polling"
```

---

### Task 3: Deploy server + dashboard to Pi

**Files:**
- Deploy: `dashboard_server.py` → `/home/pi/dashboard/dashboard_server.py`
- Deploy: `dashboard.html` → `/home/pi/dashboard/dashboard.html`
- Create on Pi: `/etc/systemd/system/airband-dashboard.service`

**Step 1: Create dashboard directory and copy files**

```bash
ssh pi@100.68.206.39 "mkdir -p /home/pi/dashboard"
scp dashboard_server.py pi@100.68.206.39:/home/pi/dashboard/
scp dashboard.html pi@100.68.206.39:/home/pi/dashboard/
```

**Step 2: Create systemd service**

Create `/etc/systemd/system/airband-dashboard.service` on Pi:
```ini
[Unit]
Description=Aviation SDR Dashboard Server
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/dashboard
ExecStart=/usr/bin/python3 /home/pi/dashboard/dashboard_server.py
Restart=always
RestartSec=5
Environment=HOME=/home/pi

[Install]
WantedBy=multi-user.target
```

**Step 3: Start service and verify**

```bash
ssh pi@100.68.206.39 "sudo systemctl daemon-reload && sudo systemctl enable airband-dashboard && sudo systemctl start airband-dashboard"
ssh pi@100.68.206.39 "systemctl is-active airband-dashboard"
ssh pi@100.68.206.39 "curl -s http://localhost:8080/api/stats | python3 -m json.tool | head -20"
```

Expected: Service active, /api/stats returns JSON with real Pi data (temp, signal levels, transmissions).

**Step 4: Disable old display service**

```bash
ssh pi@100.68.206.39 "sudo systemctl stop airband-display && sudo systemctl disable airband-display"
```

**Step 5: Commit**

```bash
git commit -m "feat: deploy dashboard server to Pi, disable curses display"
```

---

### Task 4: Install Cage + Chromium kiosk on Pi

**Files:**
- Create on Pi: `/home/pi/run_kiosk.sh`
- Create on Pi: `/etc/systemd/system/kiosk.service`

**Context:** Pi runs Debian Trixie with no desktop. Cage is a minimal Wayland compositor that runs a single app fullscreen. No desktop environment needed.

**Step 1: Install packages**

```bash
ssh pi@100.68.206.39 "sudo apt update && sudo apt install -y cage chromium"
```

This installs:
- `cage` — Wayland kiosk compositor (~2 MB + deps)
- `chromium` — web browser (~150 MB + deps)

**Step 2: Create kiosk launch script**

Create `/home/pi/run_kiosk.sh` on Pi:
```bash
#!/bin/bash
# Wait for dashboard server to be ready
sleep 5

# Launch Chromium in kiosk mode
exec chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    --disable-session-crashed-bubble \
    --disable-features=TranslateUI \
    --ozone-platform=wayland \
    --start-maximized \
    http://localhost:8080
```

```bash
ssh pi@100.68.206.39 "chmod +x /home/pi/run_kiosk.sh"
```

**Step 3: Create kiosk systemd service**

Create `/etc/systemd/system/kiosk.service` on Pi:
```ini
[Unit]
Description=Chromium Kiosk Dashboard
After=airband-dashboard.service
Wants=airband-dashboard.service

[Service]
Type=simple
User=pi
Environment=WLR_LIBINPUT_NO_DEVICES=1
Environment=XDG_RUNTIME_DIR=/run/user/1000
ExecStart=/usr/bin/cage -s -- /home/pi/run_kiosk.sh
Restart=always
RestartSec=10
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=tty

[Install]
WantedBy=multi-user.target
```

Notes:
- `WLR_LIBINPUT_NO_DEVICES=1` — allows Cage to start without keyboard/mouse
- `cage -s` — disables screen saver / DPMS
- `TTYPath=/dev/tty1` — renders on the HDMI display
- Depends on dashboard server being up first

**Step 4: Enable and start**

```bash
ssh pi@100.68.206.39 "sudo systemctl daemon-reload && sudo systemctl enable kiosk && sudo systemctl start kiosk"
```

**Step 5: Verify**

```bash
ssh pi@100.68.206.39 "systemctl is-active kiosk && systemctl is-active airband-dashboard"
```

The Pi's HDMI display should now show the dashboard fullscreen.

**Step 6: Add daily Chromium restart cron**

```bash
ssh pi@100.68.206.39 "(crontab -l; echo '0 3 * * * systemctl restart kiosk 2>/dev/null') | sort -u | crontab -"
```

Prevents Chromium memory bloat from days of continuous running.

**Step 7: Commit**

```bash
git commit -m "feat: configure Cage + Chromium kiosk on Pi"
```

---

### Task 5: End-to-end verification

**Step 1: Full system health check**

```bash
ssh pi@100.68.206.39 "
echo '=== SERVICES ==='
systemctl is-active rtl-airband-approach rtl-airband-scan airband-dashboard kiosk
echo '=== DASHBOARD API ==='
curl -s http://localhost:8080/api/stats | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f\"Clock: {d[\"clock\"]}\"); print(f\"Channels: {sum(len(dg[\"channels\"]) for dg in d[\"dongles\"].values())}\"); print(f\"Health: {d[\"health\"][\"temp_c\"]}C, {d[\"health\"][\"disk_percent\"]}% disk\")'
echo '=== RECORDINGS ==='
ls /home/pi/closecall/recordings/*.mp3 2>/dev/null | wc -l
echo '=== CSV LOG ==='
wc -l /home/pi/closecall/aviation_scan_log.csv 2>/dev/null
echo '=== DISK ==='
df -h /
"
```

Expected:
- All 4 services active
- API returns valid JSON with 4 channels, real health data
- Recordings still flowing
- CSV log growing
- Disk stable (no debug log growth)

**Step 2: Visual check**

User verifies the 15.6" display shows:
- Dark dashboard with SPACENODES title
- Two dongle panels with channel cards
- Signal gauges updating
- Live indicators pulsing on active channels
- Recent transmissions scrolling
- Pi health stats accurate

**Step 3: Commit final state**

```bash
git add -A
git commit -m "feat: Pi kiosk dashboard complete — Cage + Chromium + live data"
git push origin main
```

---

### Task 6: Update project documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update CLAUDE.md software table**

Replace the `airband_display.py` entry:
```
| airband_display.py | **Retired** | Replaced by dashboard_server.py + kiosk |
| dashboard_server.py | **Active** | `airband-dashboard.service` — HTTP server port 8080, serves dashboard + /api/stats |
| Chromium kiosk | **Active** | `kiosk.service` — Cage + Chromium fullscreen on 15.6" 1080p HDMI |
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for kiosk dashboard migration"
git push origin main
```
