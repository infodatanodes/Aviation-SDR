# Aviation SDR — Project Instructions

## Project Overview
Aviation frequency monitoring using RTL-SDR on Raspberry Pi. Tracks aircraft, decodes telemetry, and feeds data into [Spacenodes Ops Center](https://github.com/infodatanodes/Spacenodes-Ops-Center).

**GitHub Project:** #8 — `gh project item-list 8 --owner infodatanodes`
**Integration Target:** Spacenodes Ops Center (air traffic layer on the map dashboard)

## Capabilities

| Capability | Frequency | Protocol | Status |
|-----------|-----------|----------|--------|
| ADS-B tracking | 1090 MHz | Mode S Extended Squitter | readsb installed on Pi, tar1090 web map working |
| VHF Airband | 118-137 MHz | AM voice | **ACTIVE** — RTLSDR-Airband scan mode, 20 DFW freqs, integrated into Spacenodes map |
| UAT tracking | 978 MHz | Universal Access Transceiver | Not started — US only, <18,000 ft GA aircraft |
| ACARS decoding | 131.55 MHz | VHF data link | Not started — text messages between aircraft & ground |
| UHF Military | 225-400 MHz | AM voice | Not started |

## Hardware (On Pi #2 — pi-scanner, 100.68.206.39)

### RF Signal Chain
```
D3000 Discone Antenna (25-1300 MHz, mounted outside)
        │
   50ft LMR-400 coax (N to SMA)
        │
   RTL-SDR Blog Wideband LNA (+18.7 dB, USB powered from hub)
        │  (SMA jumper)
   XRDS-RF 2-Way Splitter (3 dB, SMA, 50Ω)
        │
   ┌────┴────┐
   OUT1      OUT2        (SMA jumpers)
   │         │
   V4 #1     V4 #2
   SN:001    SN:002
   Approach  Scanner
   132.922   19 freqs
```

### Installed Hardware
| Item | Status | Notes |
|------|--------|-------|
| RTL-SDR Blog V4 (SN: 00000001) | **Active** | Dedicated DFW Approach 132.922 MHz |
| RTL-SDR Blog V4 (SN: 00000002) | **Active** | Scanner — 19 DFW aviation frequencies |
| D3000 discone antenna | Mounted outside | 25-1300 MHz |
| 50ft LMR-400 coax (N to SMA) | Connected | Low-loss feed from antenna to LNA |
| RTL-SDR Blog Wideband LNA | **Connected** | +18.7 dB gain, USB powered from hub |
| XRDS-RF 2-Way Splitter | **Connected** | 3 dB split, feeds both dongles from LNA |
| Superbat SMA M-to-M Jumpers (6") | **Connected** | LNA→splitter, splitter→dongles |
| Atolla 7-Port Powered USB Hub (5V/4A) | **Connected** | Powers dongles + LNA USB |

### Arriving (eBay, March 6-13)
| Item | Purpose |
|------|---------|
| 3-Way SMA Splitter (RF-MY13, 380-2500 MHz) | Future 3-way split for ADS-B + airband + P25 |
| Browning BR-6283 (806-866 MHz, 3 dBd, 25") | Dedicated 800 MHz antenna for NTIRN P25 |
| 50ft LMR-400 (PL259 UHF M-to-M) | Feed line for Browning antenna |

## Software Installed on Pi

| Software | Status | Notes |
|----------|--------|-------|
| RTLSDR-Airband (approach) | **Active** | `rtl-airband-approach.service` — dedicated 132.922 MHz, SN:00000001, Icecast `/approach` |
| RTLSDR-Airband (scanner) | **Active** | `rtl-airband-scan.service` — 19 freqs scan mode, SN:00000002, Icecast `/scan` |
| airband_display.py | **Active** | `airband-display.service` — curses UI on tty1, two-panel layout |
| transfer_recordings.sh | **Active** | Cron every 2 min — SCPs MP3s to main PC `C:/ProScan/Recordings/Aviation-SDR/` |
| Icecast2 | **Active** | Port 8010 — `/approach` (dedicated) + `/scan` (scanner) mounts |
| readsb v3.16.10 | Installed (disabled) | ADS-B decoder — no dedicated dongle assigned yet |
| tar1090 | Running (stale) | Web map at `http://100.68.206.39/tar1090/` — reads from readsb (currently inactive) |
| rtl_test/rtl_fm/rtl_power | Installed | Blog fork versions at `/usr/local/bin/` |
| librtlsdr (Blog fork) | Installed | Built from source at `/usr/local/lib/` — required for V4 |

## Resolved Issues

1. **"SDR wedged" crash** — Root cause: `rtl_airband` was auto-starting on boot and claiming the USB device before readsb. Fix: `sudo systemctl disable rtl_airband`.
2. **Wrong librtlsdr** — Debian's stock `librtlsdr0` doesn't properly support V4's R828D tuner in async mode. Fix: removed Debian package, rebuilt Blog fork from source, rebuilt readsb from source.
3. **Location set** — `sudo readsb-set-location 32.75 -97.33` (Fort Worth area).
4. **Older dongle removed** — RTL2838UHIDIR unplugged, USB bus stable.

## Known Issues

1. **DNS on Pi** — Tailscale DNS resolver doesn't forward to public DNS. Fix: manually set `nameserver 8.8.8.8` in `/etc/resolv.conf` (Tailscale may overwrite on restart).
2. **WiFi instead of Ethernet** — Pi is on WiFi (wlan0). Should use wired Ethernet for stability.

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

### Active — Air Traffic Tracking
- `ercot_proxy.py` polls airplanes.live API for ADS-B aircraft data
- Displays aircraft as a layer on the Spacenodes map dashboard

### Future
- Replace airplanes.live with local readsb JSON API from Pi (when dedicated ADS-B dongle is set up)
- Fallback to airplanes.live if Pi is unreachable
- Add ACARS text messages as a data feed (decoded aircraft communications)
- UAT data provides FIS-B weather info — potential weather layer source

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
```

## References
- [readsb GitHub](https://github.com/wiedehopf/readsb)
- [tar1090 GitHub](https://github.com/wiedehopf/tar1090)
- [RTLSDR-Airband GitHub](https://github.com/charlie-foxtrot/RTLSDR-Airband)
- [acarsdec GitHub](https://github.com/TLeconte/acarsdec)
- [RTL-SDR Blog V4](https://www.rtl-sdr.com/rtl-sdr-blog-v4-dongle-initial-release/)
