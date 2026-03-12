# Aviation SDR — Project Instructions

## Project Overview
Aviation frequency monitoring using RTL-SDR on Raspberry Pi. Tracks aircraft, decodes telemetry, and feeds data into [Spacenodes Ops Center](https://github.com/infodatanodes/Spacenodes-Ops-Center).

**GitHub Project:** #8 — `gh project item-list 8 --owner infodatanodes`
**Integration Target:** Spacenodes Ops Center (air traffic layer on the map dashboard)

## Capabilities

| Capability | Frequency | Protocol | Status |
|-----------|-----------|----------|--------|
| ADS-B tracking | 1090 MHz | Mode S Extended Squitter | **ACTIVE** — readsb on Pi, local feed to Spacenodes, tar1090 web map working |
| VHF Airband | 118-137 MHz | AM voice | **ACTIVE** — RTLSDR-Airband multichannel mode, 4 DFW freqs (2 dongles), integrated into Spacenodes map |
| UAT tracking | 978 MHz | Universal Access Transceiver | **ACTIVE** — dump978-fa on RTL2838UHIDIR V3 (SN:00000105), JSON on port 30979, raw on port 30978 |
| ACARS decoding | 131.55/131.45/131.475/131.725 MHz | VHF data link | **ACTIVE** — acarsdec on old RTL2838UHIDIR (SN:00000104), 4 ACARS freqs, parsed positions/routes fed to Spacenodes |
| UHF Military | 225-400 MHz | AM voice | Not started |

## Hardware (On Pi #2 — pi-scanner, 100.68.206.39)

### RF Signal Chain
```
D3000 Discone Antenna (25-1300 MHz, mounted outside)
        │
   50ft LMR-400 coax (N to SMA)
        │
   XRDS-RF 2-Way Splitter (3 dB, SMA, 50Ω)
        │
   ┌────┴────┐
   OUT1      OUT2        (SMA jumpers)
   │         │
   V4 #1     V4 #2
   SN:001    SN:002
   Approach  Scanner
   132.922   3 freqs (multichannel)

   V4 #3 (SN:003) — ADS-B 1090 MHz — on D3000 directly (no splitter)
   Old RTL2838UHIDIR (SN:004) — ACARS 131.55 MHz — on Bingfu indoor antenna
```

**Antenna setup (March 10, 2026):** Each dongle has a dedicated antenna. Splitters removed — wideband frequency differences made splitting impractical, and the LNA was hurting ADS-B performance. D3000 discone feeds ADS-B directly. VHF airband dongles use dedicated Bingfu antennas and scanner antenna. LNA disconnected (overloaded ADS-B at 1090 MHz, limited range to 12nm; without it: 40+ nm).

### Installed Hardware
| Item | Status | Notes |
|------|--------|-------|
| RTL-SDR Blog V4 (SN: 00000001) | **Active** | Dedicated DFW Approach 132.922 MHz |
| RTL-SDR Blog V4 (SN: 00000002) | **Active** | Multichannel — 124.300/125.025/126.550 MHz (centered 125.425) |
| RTL-SDR Blog V4 (SN: 00000003) | **Active** | ADS-B 1090 MHz — readsb, feeds Spacenodes via local API |
| RTL2838UHIDIR (SN: 00000104) | **Active** | ACARS 131.55 MHz — old generic dongle (Realtek), acarsdec |
| RTL2838UHIDIR V3 (SN: 00000105) | **Active** | UAT 978 MHz — V3 dongle (R820T), dump978-fa |
| D3000 discone antenna | **Connected** | 25-1300 MHz, feeds ADS-B dongle directly (no splitter) |
| 50ft LMR-400 coax (N to SMA) | **Connected** | Low-loss feed from D3000 to ADS-B dongle |
| Bingfu antennas | **Connected** | Dedicated antennas for VHF airband dongles (SN:001, SN:002) |
| Scanner antenna | **Connected** | Additional dedicated antenna for VHF airband |
| Bingfu indoor antenna | **Connected** | Feeds ACARS dongle (SN:004) |
| Atolla 7-Port Powered USB Hub (5V/4A) | **Connected** | Powers all 4 dongles |
| RTL-SDR Blog Wideband LNA | **Disconnected** | Removed — overloaded ADS-B at 1090 MHz (12nm range) |
| XRDS-RF 2-Way Splitter | **Disconnected** | Removed — wideband freq split impractical, each dongle has dedicated antenna |
| Superbat SMA M-to-M Jumpers (6") | **Spare** | No longer needed with splitter removed |

### Arriving / Pending
| Item | Purpose |
|------|---------|
| Browning BR-6283 (806-866 MHz, 3 dBd, 25") | Dedicated 800 MHz antenna for NTIRN P25 |
| 50ft LMR-400 (PL259 UHF M-to-M) | Feed line for Browning antenna |

### No Longer Needed
| Item | Reason |
|------|--------|
| 3-Way SMA Splitter (RF-MY13, 380-2500 MHz) | Splitter approach abandoned — dedicated antennas per dongle |

## Software Installed on Pi

| Software | Status | Notes |
|----------|--------|-------|
| RTLSDR-Airband (approach) | **Active** | `rtl-airband-approach.service` — dedicated 132.922 MHz, SN:00000001, Icecast `/approach` |
| RTLSDR-Airband (scanner) | **Active** | `rtl-airband-scan.service` — multichannel 124.300/125.025/126.550 MHz (center 125.425), SN:00000002, Icecast `/scan` |
| dashboard_server.py | **Active** | `airband-dashboard.service` — HTTP server port 8080, serves dashboard + /api/stats. **Runs from `/home/pi/dashboard/`** (not closecall) |
| Chromium kiosk | **Active** | `kiosk.service` — Cage + Chromium fullscreen on 15.6" 1080p HDMI, `--remote-debugging-port=9222` for CDP screenshots |
| airband_display.py | **Retired** | Replaced by dashboard_server.py + kiosk |
| transfer_recordings.sh | **Active** | Cron every 2 min — SCPs MP3s to main PC `C:/ProScan/Recordings/Aviation-SDR/` |
| Icecast2 | **Active** | Port 8010 — `/approach` (dedicated) + `/scan` (scanner) mounts |
| readsb v3.16.10 | **Active** | ADS-B decoder on SN:00000003, `--gain auto`, local JSON API |
| tar1090 | **Active** | Web map at `http://100.68.206.39/tar1090/` — reads from readsb |
| acarsdec | **Active** | ACARS decoder on SN:00000004, 4 freqs (131.550/131.450/131.475/131.725), gain 28, output to `/home/pi/closecall/acars_messages.jsonl` |
| dump978-fa | **Active** | `dump978-fa.service` — UAT 978 MHz decoder on SN:00000105, JSON port 30979, raw port 30978 |
| rtl_test/rtl_fm/rtl_power | Installed | Blog fork versions at `/usr/local/bin/` |
| librtlsdr (Blog fork) | Installed | Built from source at `/usr/local/lib/` — required for V4 |

## Resolved Issues

1. **"SDR wedged" crash** — Root cause: `rtl_airband` was auto-starting on boot and claiming the USB device before readsb. Fix: `sudo systemctl disable rtl_airband`.
2. **Wrong librtlsdr** — Debian's stock `librtlsdr0` doesn't properly support V4's R828D tuner in async mode. Fix: removed Debian package, rebuilt Blog fork from source, rebuilt readsb from source.
3. **Location set** — `sudo readsb-set-location 32.75 -97.33` (Fort Worth area).
4. **Older dongle repurposed** — RTL2838UHIDIR (SN:004) now used for ACARS decoding.
5. **Scan mode fails with LNA+splitter** — Scan mode hops too fast across frequencies; squelch never opens because dwell time is too short with amplified noise floor. Fix: switched to multichannel mode (dongle stays parked, demodulates all channels simultaneously within ~2.3 MHz bandwidth).
6. **Dedicated antennas > splitters for wideband** — Splitting a wideband antenna across dongles on very different frequencies (132 MHz VHF vs 1090 MHz ADS-B) is impractical. Each dongle now has its own antenna matched to its frequency range.
6. **Debug log filling SD card (twice)** — First time: `-e` flag on services wrote 20GB. Second time: rtl_airband writes `/rtl_airband_debug.log` **by default** via `-d` flag (default path). Fix: added `-d /dev/null` to both service ExecStart lines. The `-e` flag only controls stderr, NOT the debug file.
7. **Old rtl-airband.service crash-looping** — Stale original service (18,925 restarts) fighting for device 0. Fix: disabled, replaced by `rtl-airband-approach` and `rtl-airband-scan` services pinned by serial number.
8. **Kiosk ACARS panel showing stale data** — ACARS messages from hours ago persisted on display with no age indication. Fix: added 30-minute cutoff filter in `get_acars_messages()` in dashboard_server.py. Messages older than 30 min are dropped; panel shows "No recent messages — listening on 4 frequencies" when empty.
9. **Kiosk display freeze** — Dashboard fetch loop could die silently, leaving stale aircraft/ACARS on screen for hours. Fix: added self-healing watchdog in dashboard.html — auto-reloads page after 30 consecutive fetch failures or 5 minutes with no successful update.
10. **Kiosk 3 AM restart seatd permission errors** — Cage sometimes fails to acquire DRM session on daily restart cron (`Could not open target tty: Permission denied`). Cage's `Restart=always` in systemd retries and typically succeeds on second attempt ~10s later.
11. **Duplicate serial 00000001 on 5th dongle** — New V3 dongle shipped with default SN:001, colliding with existing approach V4. Used `rtl_eeprom -d <idx> -s <serial>` to program unique serials. Also renamed ACARS dongle from SN:00000004 to SN:00000104 because librtlsdr interprets small numeric serials as device indices (00000004 → index 4).
12. **Dashboard server path confusion** — Service runs from `/home/pi/dashboard/dashboard_server.py`, NOT `/home/pi/closecall/`. Both copies exist; the closecall copy is stale. Always edit the `/home/pi/dashboard/` version.

## Lessons Learned

- **Always use `-d /dev/null` in production** — rtl_airband writes debug log by default, `-e` only controls stderr
- **Multichannel mode > scan mode** when using LNA — ~2.3 MHz bandwidth limit per dongle, but no missed transmissions
- **Pin dongles by serial number**, not index — indices can swap on reboot
- **Dallas Approach (125.350)** showed zero activity — replaced with Regional Approach (124.300)
- **Dallas Love ATIS (127.000)** is a robot weather loop — exclude from pipeline to avoid wasted Whisper cycles
- **DFW Clearance (126.550)** has most noise of the monitored channels — candidate for audio filtering
- **LNA overloads ADS-B at 1090 MHz** — +18.7 dB gain saturated the R828D tuner, range dropped to 12nm. Without LNA: 40+ nm. Do NOT use LNA on ADS-B path.
- **Identify dongles via SysFS** — `cat /sys/bus/usb/devices/*/product` shows "Blog V4" (V4) vs "RTL2838UHIDIR" (old). Serial in `/sys/bus/usb/devices/*/serial`.
- **Dashboard serves from `/home/pi/dashboard/`** — NOT `/home/pi/closecall/`. Both dirs have copies; always edit dashboard/ version.
- **ACARS messages need age filtering** — acarsdec appends to JSONL forever; dashboard must filter old entries or display goes stale during quiet hours.
- **Remote kiosk screenshots via CDP** — Chromium `--remote-debugging-port=9222` enables Chrome DevTools Protocol. Script at `/home/pi/closecall/kiosk_screenshot.py` takes PNG screenshots. Use for remote monitoring.
- **ADS-B range overnight** — With D3000 + no LNA, typical overnight range 25-57 nm. Daytime should be higher with more aircraft at various distances.
- **RTL-SDR serial collision** — All Blog V4 dongles ship with SN:00000001. Always reprogram with `rtl_eeprom` before adding to multi-dongle setup. Use serials > 100 (e.g., 00000104) to avoid librtlsdr interpreting them as device indices.
- **UAT traffic is sporadic** — GA aircraft below 18,000 ft, mainly daytime. FIS-B ground stations broadcast weather data periodically regardless of aircraft.
- **ACARS activity patterns** — Busy during daytime/evening, very sparse 2-5 AM. All 4 monitored ACARS freqs most active on 131.725 MHz in DFW area.

## Log Management

- **transfer_logs.sh**: Runs daily 3 AM via cron, transfers CSV/logs to `C:\ProScan\Recordings\Aviation-SDR\logs\` with date stamps, truncates on Pi
- **transfer_recordings.sh**: Runs every 2 min via cron, SCPs MP3s to main PC, deletes local

## Known Issues

1. **DNS on Pi** — Tailscale DNS resolver doesn't forward to public DNS. Fix: manually set `nameserver 8.8.8.8` in `/etc/resolv.conf` (Tailscale may overwrite on restart).
2. **WiFi instead of Ethernet** — Pi is on WiFi (wlan0). Should use wired Ethernet for stability.
3. **Dallas Approach (125.350) silent** — Zero activity in 1-hour test. May not be active freq for this location.

## Aviation Frequencies — DFW Area

### ADS-B
- 1090 MHz (Mode S) — all commercial + most GA aircraft

### UAT
- 978 MHz — US only, GA aircraft below 18,000 ft, also carries FIS-B weather data

### ACARS (VHF Data Link)
- Primary: 131.550 MHz
- Secondary: 131.450 MHz, 131.475 MHz, 131.725 MHz

### VHF Airband — Active Monitoring (20 Frequencies)
Configured in `/usr/local/etc/rtl_airband.conf` on Pi, scan mode.

| Frequency | Service | Airport |
|-----------|---------|---------|
| 118.050 | Tower West | DFW |
| 119.050 | Tower East | DFW |
| 121.650 | Ground | DFW |
| 124.150 | ATIS | DFW |
| 125.025 | Departure | DFW |
| 126.550 | Clearance | DFW |
| 127.000 | ATIS | Love Field |
| 132.922 | Approach | DFW |
| 133.200 | Approach | DFW |
| 134.900 | Tower | Love Field |
| 135.575 | Tower | Alliance |
| 132.450 | Tower | Meacham |
| 124.300 | Approach | Regional |
| 119.200 | Center | Fort Worth |
| 120.350 | Center | Fort Worth |
| 127.800 | Center | Fort Worth |
| 128.250 | Center | Dallas |
| 121.500 | Emergency (Guard) | Universal |
| 123.025 | Unicom | GA Airports |
| 123.450 | Air-to-Air | GA |

**Top channels by activity**: DFW Approach (132.922), Love ATIS (127.000), DFW Departure (125.025)

### Military
- 225-400 MHz UHF AM — NAS Fort Worth JRB, Carswell Field

## Integration with Spacenodes Ops Center

### Active — VHF Airband → Map Integration (March 2026)
1. Pi records VHF airband transmissions as per-transmission MP3s
2. `transfer_recordings.sh` SCPs MP3s to main PC every 2 min → `C:\ProScan\Recordings\Aviation-SDR\`
3. `recording_watcher.py` aviation worker picks up files, transcribes with Whisper (aviation-specific prompt)
4. Callsign extracted: airline+digits ("American 1415"→AAL1415), N-number, or ICAO code
5. WebSocket broadcasts `aviation_transmission` to map
6. **Map**: aircraft with matching callsign gets blue glow ring (#00bfff), popup shows ATC Radio section with audio player
7. Tower frequency transmissions show 📡 marker at airport coordinates
8. All indicators fade after 10 minutes

**Key files on Spacenodes side**: `recording_watcher.py` (aviation worker), `map.html` (glow + popup), `aviation_log.db` (transmission history)
**API**: `GET /api/aviation-log?callsign=AAL1415` — query transmission history

**POC results** (30 recordings): Whisper turbo extracted callsigns from 37% (11/30 — 8 clean hits)

### Active — Air Traffic Tracking (Local readsb)
- `ercot_proxy.py` polls local readsb on Pi for ADS-B data (airplanes.live code preserved but disabled)
- Aircraft enriched from tar1090 DB (registration, type, description, owner)
- Displays aircraft as a layer on the Spacenodes map dashboard

### Active — ACARS Data Feed
- `ercot_proxy.py` polls Pi `/api/acars` every 30s
- Parses positions (/A1 decimal, POSN compact), flight plans (DA:/AA:), routes
- Cross-references ACARS flights/tails with ADS-B aircraft
- ACARS-only positions rendered as amber markers on map
- Aircraft popups show ACARS section (route, waypoints, message text)

### Future
- Fallback to airplanes.live if Pi is unreachable
- UAT data provides FIS-B weather info — potential weather layer source
- Dedicated 1090 MHz antenna for improved ADS-B range (100-200nm target)

### readsb API Endpoint
```
http://100.68.206.39/tar1090/data/aircraft.json
```
Same JSON format as airplanes.live — near-drop-in replacement once dedicated dongle is assigned.

## NotebookLM Research

Aviation research notebook exists in NotebookLM (ID: 5d5b971c) with 13 YouTube sources covering:
- ADS-B setup with readsb on Raspberry Pi
- Dual ADS-B + UAT monitoring
- ACARS decoding with acarsdec
- VHF airband monitoring
- Antenna considerations (need separate antennas for 1090 MHz vs VHF)

## Pi Network Info

| Pi | Hostname | Tailscale IP | Role |
|----|----------|-------------|------|
| Pi #2 | pi-scanner | 100.68.206.39 | P25 scanner + Aviation SDR |

## Useful Commands

```bash
# SSH to Pi
ssh pi@100.68.206.39

# Check readsb status
systemctl status readsb

# Check aircraft count
cat /run/readsb/aircraft.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"Aircraft: {len(d[\"aircraft\"])}")'

# Set receiver location
sudo readsb-set-location <latitude> <longitude>

# Test RTL-SDR dongle
rtl_test -t -d 0

# Check USB devices
lsusb

# Check Pi temperature
vcgencmd measure_temp

# Remote kiosk screenshot (requires --remote-debugging-port=9222)
ssh pi@100.68.206.39 "python3 /home/pi/closecall/kiosk_screenshot.py /tmp/kiosk_cdp.png"
scp pi@100.68.206.39:/tmp/kiosk_cdp.png /tmp/kiosk_cdp.png

# Check all 4 dongles are connected
ssh pi@100.68.206.39 "cat /sys/bus/usb/devices/*/serial 2>/dev/null | sort -u"

# Check ACARS message log
ssh pi@100.68.206.39 "tail -5 /home/pi/closecall/acars_messages.jsonl"

# Check all 6 services
ssh pi@100.68.206.39 "systemctl is-active readsb rtl-airband-approach rtl-airband-scan airband-dashboard kiosk dump978-fa"

# Check UAT messages
ssh pi@100.68.206.39 "timeout 10 nc -q1 localhost 30979 | head -5"
```

## References
- [readsb GitHub](https://github.com/wiedehopf/readsb)
- [tar1090 GitHub](https://github.com/wiedehopf/tar1090)
- [RTLSDR-Airband GitHub](https://github.com/charlie-foxtrot/RTLSDR-Airband)
- [acarsdec GitHub](https://github.com/TLeconte/acarsdec)
- [RTL-SDR Blog V4](https://www.rtl-sdr.com/rtl-sdr-blog-v4-dongle-initial-release/)
