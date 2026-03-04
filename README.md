# Aviation SDR

Aviation frequency monitoring using RTL-SDR on Raspberry Pi — ADS-B, UAT, ACARS, and VHF Airband.

Feeds into [Spacenodes Ops Center](https://github.com/infodatanodes/Spacenodes-Ops-Center) for real-time aircraft tracking on the map dashboard.

## Capabilities

- **ADS-B** (1090 MHz) — Commercial and GA aircraft tracking
- **UAT** (978 MHz) — General aviation below 18,000 ft + FIS-B weather
- **ACARS** (131.55 MHz) — Aircraft text telemetry and messaging
- **VHF Airband** (118-137 MHz) — ATC voice communications
- **UHF Military** (225-400 MHz) — Military aviation comms

## Hardware

- Raspberry Pi 4 Model B
- RTL-SDR Blog V4 (1090 MHz ADS-B)
- D3000 Discone Antenna (25-1300 MHz)
- Additional RTL-SDR for VHF/UAT (planned)

## Software Stack

- [readsb](https://github.com/wiedehopf/readsb) — ADS-B decoder
- [tar1090](https://github.com/wiedehopf/tar1090) — Web-based aircraft map
- [RTLSDR-Airband](https://github.com/charlie-foxtrot/RTLSDR-Airband) — VHF airband recorder
- [acarsdec](https://github.com/TLeconte/acarsdec) — ACARS message decoder
