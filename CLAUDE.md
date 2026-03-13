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
| UAT tracking | 978 MHz | Universal Access Transceiver | **DISABLED** — dump978-fa disabled 2026-03-13 (V4 SN:002 freed up, could be re-enabled). V4 needed for FM notch filter — V3 overloaded by FM broadcast indoors. Indoor 978 antenna insufficient for FIS-B ground stations — needs outdoor mounting. |
| ACARS decoding | 130.025/130.425/130.450/131.550/131.725 MHz | VHF data link | **ACTIVE** — acarsdec on old RTL2838UHIDIR (SN:00000104) + wideband LNA, gain 42, 5 North America ACARS freqs, parsed positions/routes fed to Spacenodes |
| UHF Military | 225-400 MHz | AM voice | Not started |

## Hardware (On Pi #2 — pi-scanner, 100.68.206.39)

### RF Signal Chain (as of 2026-03-13)
```
D3000 Discone Antenna (25-1300 MHz, mounted outside)
        │
   50ft LMR-400 coax (N to SMA)
        │
   V4 #3 (SN:003) — ADS-B 1090 MHz — readsb, gain 42.1

Bingfu Indoor Antenna
        │
   RTL-SDR Blog Wideband LNA (SPF5189Z, +20 dB)
        │
   RTL2838UHIDIR (SN:104) — ACARS 5 NA freqs — acarsdec, gain 42

V4 #1 (SN:001) — Unplugged (was Approach)
V4 #2 (SN:002) — Available (freed from ACARS — V4 bad for VHF)
V3 (SN:105) — Unplugged (was multichannel scanner)
```

**Antenna setup (updated 2026-03-13):** D3000 discone feeds ADS-B (V4 SN:003) directly via LMR-400. ACARS uses Bingfu indoor antenna → wideband LNA → old RTL2838 (SN:104). LNA removed from ADS-B path (overloaded at 1090 MHz, limited range to 12nm; without it: 40+ nm). VHF airband dongles (currently unplugged) had dedicated Bingfu antennas.

### Installed Hardware
| Item | Status | Notes |
|------|--------|-------|
| RTL-SDR Blog V4 (SN: 00000001) | **Unplugged** | Was DFW Approach 132.922 MHz |
| RTL-SDR Blog V4 (SN: 00000002) | **Available** | Freed up 2026-03-13 — V4 NOT suitable for VHF ACARS (see Lessons Learned). Could be re-enabled for UAT 978 MHz. |
| RTL-SDR Blog V4 (SN: 00000003) | **Active** | ADS-B 1090 MHz — readsb, feeds Spacenodes via local API |
| RTL2838UHIDIR (SN: 00000104) | **Active** | ACARS 5 NA freqs — acarsdec, gain 42, with wideband LNA inline. Old R820T tuner outperforms V4 on VHF. |
| RTL2838UHIDIR V3 (SN: 00000105) | **Unplugged** | Was multichannel scanner — 124.300/125.025/126.550 MHz |
| D3000 discone antenna | **Connected** | 25-1300 MHz, feeds ADS-B dongle directly (no splitter) |
| 50ft LMR-400 coax (N to SMA) | **Connected** | Low-loss feed from D3000 to ADS-B dongle |
| Bingfu antennas | **Connected** | Dedicated antennas for VHF airband dongles (SN:001, SN:002) |
| Scanner antenna | **Connected** | Additional dedicated antenna for VHF airband |
| Bingfu indoor antenna | **Connected** | Feeds ACARS dongle (SN:104) via LNA |
| Atolla 7-Port Powered USB Hub (5V/4A) | **Connected** | Powers dongles |
| RTL-SDR Blog Wideband LNA (SPF5189Z) | **Connected** | Inline with RTL2838 SN:104 for ACARS. WARNING: Wideband LNA amplifies FM broadcast (+20 dB) near ACARS freq — works with old RTL2838 at gain 42, but NOT with V4 (see Lessons Learned) |
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
| Chromium kiosk | **Active** | `kiosk.service` — wrapper (`start_kiosk.sh`) restarts seatd, launches Cage + Chromium via `setsid --wait`, `DBUS_SESSION_BUS_ADDRESS=disabled:`, fullscreen on 15.6" 1080p HDMI, `--remote-debugging-port=9222` for CDP screenshots |
| airband_display.py | **Retired** | Replaced by dashboard_server.py + kiosk |
| transfer_recordings.sh | **Active** | Cron every 2 min — SCPs MP3s to main PC `C:/ProScan/Recordings/Aviation-SDR/` |
| Icecast2 | **Active** | Port 8010 — `/approach` (dedicated) + `/scan` (scanner) mounts |
| readsb v3.16.10 | **Active** | ADS-B decoder on SN:00000003, `--gain 42.1` (tuned 2026-03-12, was auto), local JSON API |
| tar1090 | **Active** | Web map at `http://100.68.206.39/tar1090/` — reads from readsb |
| acarsdec | **Active** | `acarsdec.service` — ACARS decoder on RTL2838 SN:00000104 + wideband LNA, 5 NA freqs (130.025/130.425/130.450/131.550/131.725), gain 42, output to `/home/pi/closecall/acars_messages.jsonl` |
| dump978-fa | **Disabled** | `dump978-fa.service` — UAT 978 MHz decoder, disabled 2026-03-13. V4 SN:002 now available to re-enable. |
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
13. **Kiosk Cage/Chromium crash loop after power cycle (2026-03-13)** — After hard power loss, Cage can't restart because seatd holds stale VT sessions. Restarting seatd fixes Cage but kills D-Bus, causing Chromium SIGABRT (`FATAL:dbus/bus.cc:1245`). Fix: wrapper script (`/home/pi/start_kiosk.sh`) runs as root, restarts seatd before each Cage launch, and sets `DBUS_SESSION_BUS_ADDRESS=disabled:` so Chromium never connects to D-Bus (not needed for kiosk). Also uses `setsid --wait` to give Cage its own POSIX session.
14. **ACARS V4+LNA decoded only keepalives, zero data messages (2026-03-13)** — V4 SN:002 with wideband LNA at gain 12 decoded only 7 `_d` keepalive frames in 30 minutes. Zero H1 data messages (flight plans, positions). Root cause: V4's internal triplexer/FM notch adds 2-3 dB VHF loss, and wideband LNA amplifies FM broadcast (+20 dB at 88-108 MHz) which overloads the V4's ADC before the internal FM notch can act. Fix: switched to old RTL2838UHIDIR (SN:104) + LNA + gain 42 + correct NA frequencies → 17 rich data messages in first 3 minutes. Research source: thebaldgeek.github.io, airframes.io, sigidwiki. See memory file `v4_lna_acars_troubleshooting_research.md`.
15. **Wrong ACARS frequencies for North America (2026-03-13)** — Was monitoring 131.450 and 131.475 MHz (European/Air Canada only) with zero messages ever. Research found correct NA ACARS set: 130.025, 130.425, 130.450, 131.550, 131.725 MHz. All fit within single dongle's 2 MHz bandwidth. Fix: updated acarsdec.service ExecStart with correct frequencies.

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
- **ADS-B optimal gain: 42.1 dB** — Tuned 2026-03-12 across 9 settings (32.8–49.6). Auto gain had 3,834 strong signals (clipping); 42.1 scored highest (6,832) with 17 avg aircraft and zero clipping. Above 44.5 dB, strong signals appear.
- **ACARS optimal gain: 42 dB on old RTL2838 with LNA** — Tuned 2026-03-13 after switching back to old dongle. The old R820T tuner handles the LNA better than V4's R828D at VHF. Without LNA, gain 28-42 recommended. V4 with LNA at gain 12 only decoded keepalive `_d` frames — zero data messages.
- **RTL-SDR V4 is NOT suitable for VHF ACARS** — V4's internal triplexer + FM notch filter add 2-3 dB sensitivity loss on VHF vs V3/RTL2838 (per thebaldgeek: "Avoid the RTL-SDR v4 for anything above HF"). V4 advantages (HF upconverter, FM notch) don't help at 131 MHz. The FM notch is INSIDE the dongle after the ADC — it can't prevent front-end overload from an external LNA. Use V3 or old RTL2838 for VHF ACARS.
- **Wideband LNA is dangerous near FM band** — SPF5189Z amplifies everything 50 MHz–4 GHz. FM broadcast (88-108 MHz) is only 23 MHz below ACARS (130-131 MHz). With LNA, FM gets +20 dB amplification that can overload the dongle ADC, raise noise floor, and bury ACARS signals. Works with old RTL2838 at gain 42 but NOT with V4. For guaranteed clean ACARS, use a bandpass or cavity filter, or no LNA at all.
- **Wrong ACARS frequencies waste monitoring** — 131.450 and 131.475 MHz are European/Air Canada only. Zero messages ever received on them in DFW. Correct North America set: 130.025, 130.425, 130.450, 131.550, 131.725. Sources: airframes.io, sigidwiki, thebaldgeek.
- **Don't assume hardware failure** — V4 #2 showed bad IQ on Pi (range 7-9) but tested perfect on main PC (range 255). Root cause was USB hub power starvation, not dead RF front end. Always test on second machine before declaring dead.
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

### ACARS (VHF Data Link) — North America Frequencies
- **130.025 MHz** — USA & Canada secondary (ARINC)
- **130.425 MHz** — USA additional
- **130.450 MHz** — USA & Canada additional (active in DFW per RadioReference)
- **131.550 MHz** — Primary worldwide (SITA/ARINC)
- **131.725 MHz** — Listed as Europe but confirmed active in DFW area

**NOT used (wrong region):** 131.450 (not standard NA), 131.475 (Air Canada company only), 131.825 (Europe only)

**Bandwidth note:** 130.025–131.725 = 1.7 MHz — fits within RTL-SDR's 2 MHz bandwidth on a single dongle.

**Sources:** airframes.io, sigidwiki.com, thebaldgeek.github.io, RadioReference DFW

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

# Check active services (acarsdec replaces dump978-fa as of 2026-03-13)
ssh pi@100.68.206.39 "systemctl is-active readsb rtl-airband-approach rtl-airband-scan airband-dashboard kiosk acarsdec"

# Check acarsdec service status
ssh pi@100.68.206.39 "systemctl status acarsdec"
```

## References
- [readsb GitHub](https://github.com/wiedehopf/readsb)
- [tar1090 GitHub](https://github.com/wiedehopf/tar1090)
- [RTLSDR-Airband GitHub](https://github.com/charlie-foxtrot/RTLSDR-Airband)
- [acarsdec GitHub](https://github.com/TLeconte/acarsdec)
- [RTL-SDR Blog V4](https://www.rtl-sdr.com/rtl-sdr-blog-v4-dongle-initial-release/)
