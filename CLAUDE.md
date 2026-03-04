# Aviation SDR — Project Instructions

## Project Overview
Aviation frequency monitoring using RTL-SDR on Raspberry Pi. Tracks aircraft, decodes telemetry, and feeds data into [Spacenodes Ops Center](https://github.com/infodatanodes/Spacenodes-Ops-Center).

**GitHub Project:** #8 — `gh project item-list 8 --owner infodatanodes`
**Integration Target:** Spacenodes Ops Center (air traffic layer on the map dashboard)

## Capabilities (Planned)

| Capability | Frequency | Protocol | Status |
|-----------|-----------|----------|--------|
| ADS-B tracking | 1090 MHz | Mode S Extended Squitter | readsb installed on Pi, not yet receiving (USB power issue) |
| UAT tracking | 978 MHz | Universal Access Transceiver | Not started — US only, <18,000 ft GA aircraft |
| ACARS decoding | 131.55 MHz | VHF data link | Not started — text messages between aircraft & ground |
| VHF Airband | 118-137 MHz | AM voice | RTLSDR-Airband cloned on Pi, not built |
| UHF Military | 225-400 MHz | AM voice | Not started |

## Hardware (On Pi #2 — pi-scanner, 100.68.206.39)

| Item | Status | Notes |
|------|--------|-------|
| RTL-SDR Blog V4 | Owned, on Pi | SN: 00000002, for ADS-B (1090 MHz) |
| Older RTL-SDR (RTL2838UHIDIR) | Owned, on Pi | Flaky USB — disconnects repeatedly, needs powered hub |
| D3000 discone antenna | Owned, connected | 25-1300 MHz, connected to both dongles |
| Powered USB hub | **To order** | Required — 2 dongles brownout Pi USB bus |

## Software Installed on Pi

| Software | Status | Notes |
|----------|--------|-------|
| readsb v3.16.10 | Installed, enabled | ADS-B decoder — crashes due to USB power issue with 2nd dongle |
| tar1090 | Install failed | Needs readsb stable first — web map UI |
| RTLSDR-Airband | Cloned, not built | VHF airband recorder at `/home/pi/RTLSDR-Airband/` |
| rtl_test/rtl_fm/rtl_power | Installed | Basic RTL-SDR utilities |

## Known Issues

1. **USB bus instability**: Older RTL-SDR dongle repeatedly disconnects/reconnects, destabilizing the bus and causing the V4 to wedge (samples_processed: 0). Fix: unplug older dongle OR use powered USB hub.
2. **tar1090 install**: Failed because readsb wasn't stable. Retry after USB issue resolved.
3. **No location set**: Need to run `sudo readsb-set-location <lat> <lon>` for proper distance/map centering.

## Aviation Frequencies — DFW Area

### ADS-B
- 1090 MHz (Mode S) — all commercial + most GA aircraft

### UAT
- 978 MHz — US only, GA aircraft below 18,000 ft, also carries FIS-B weather data

### ACARS (VHF Data Link)
- Primary: 131.550 MHz
- Secondary: 131.450 MHz, 131.475 MHz, 131.725 MHz

### VHF Airband — DFW Airports
| Frequency | Service | Airport |
|-----------|---------|---------|
| 124.150 | ATIS | DFW |
| 126.550 | Ground | DFW |
| 123.850 | Tower | DFW |
| 132.475 | Approach | DFW Regional |
| 125.800 | ATIS | DAL (Love Field) |
| 121.500 | Emergency | Universal |

### Military
- 225-400 MHz UHF AM — NAS Fort Worth JRB, Carswell Field

## Integration with Spacenodes Ops Center

### Current State
- `ercot_proxy.py` polls airplanes.live API for air traffic data
- Displays aircraft as a layer on the Spacenodes map dashboard

### Target State
- Replace airplanes.live with local readsb JSON API from Pi #1/Pi #2
- Fallback to airplanes.live if Pi is unreachable
- Add ACARS text messages as a data feed (decoded aircraft communications)
- Correlate ADS-B positions with VHF airband audio (which aircraft is talking)
- UAT data provides FIS-B weather info — potential weather layer source

### API Endpoint (When Working)
```
http://100.68.206.39/tar1090/data/aircraft.json
```
Same JSON format as airplanes.live — should be near-drop-in replacement.

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
