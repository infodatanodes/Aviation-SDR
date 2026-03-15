#!/usr/bin/env python3
"""
acars_parser.py — Structured ACARS message parser for Spacenodes

Extracts structured data from raw acarsdec JSON messages:
  - Positions (10+ formats across labels 10, 12, 16, 21, 22, 80, 83, 4A, H1-POSN, H1-/A1)
  - OOOI events (gate out, wheels off, wheels on, gate in)
  - Weather/winds aloft
  - Engine telemetry (DFB reports)
  - Flight plans and routes
  - Maintenance alerts
  - Free text (pilot/dispatch messages)

Usage:
    from acars_parser import parse_acars_message
    parsed = parse_acars_message(raw_msg_dict)
"""

import re

# ── Category constants ────────────────────────────────────────────────────

CAT_POSITION = "position"
CAT_OOOI = "oooi"
CAT_WEATHER = "weather"
CAT_ENGINE = "engine"
CAT_FLIGHT_PLAN = "flight_plan"
CAT_MAINTENANCE = "maintenance"
CAT_FREE_TEXT = "free_text"
CAT_KEEPALIVE = "keepalive"
CAT_OPS = "ops"
CAT_BINARY = "binary"
CAT_UNKNOWN = "unknown"

# Labels known to carry binary/encoded data (not parseable)
BINARY_LABELS = {"37", "44", "48"}

# Labels that are airline ops status (origin/station, no rich data)
OPS_LABELS = {"5Z", "Q7", "B9", "5V", "BA", "SA", "25"}

# OOOI label meanings
OOOI_LABELS = {
    "QA": "out",     # gate out (alternate)
    "QP": "out",     # gate out
    "QQ": "off",     # wheels off
    "QR": "on",      # wheels on (alternate)
    "QS": "in",      # gate in
    "QF": "off",     # wheels off (alternate)
}


def parse_acars_message(msg):
    """Parse a raw acarsdec message dict into structured data.

    Returns the original message with added fields:
      - category: str — message category
      - parsed: dict — extracted structured data (varies by category)
    """
    text = (msg.get("text") or "").strip()
    label = msg.get("label", "")
    result = {}

    # Keepalive — no text, label _d
    if not text and label == "_d":
        return {"category": CAT_KEEPALIVE, "parsed": {}}

    # Binary/encoded labels
    if label in BINARY_LABELS:
        return {"category": CAT_BINARY, "parsed": {}}

    # OOOI events — acarsdec pre-parses these fields
    oooi = _parse_oooi(msg, label, text)
    if oooi:
        return {"category": CAT_OOOI, "parsed": oooi}

    # Position reports — try all formats
    position = _parse_position(label, text)
    if position:
        return {"category": CAT_POSITION, "parsed": position}

    # Weather (check BEFORE engine — DFB messages with /WX are weather)
    weather = _parse_weather(label, text)
    if weather:
        return {"category": CAT_WEATHER, "parsed": weather}

    # Engine telemetry / DFB reports
    engine = _parse_engine(label, text)
    if engine:
        return {"category": CAT_ENGINE, "parsed": engine}

    # Maintenance alerts
    maint = _parse_maintenance(label, text)
    if maint:
        return {"category": CAT_MAINTENANCE, "parsed": maint}

    # Free text (pilot/dispatch messages) — check BEFORE flight_plan
    # Label 30 is always free text even if it contains route info
    ftext = _parse_free_text(label, text)
    if ftext:
        return {"category": CAT_FREE_TEXT, "parsed": ftext}

    # Flight plan / route
    fplan = _parse_flight_plan(msg, label, text)
    if fplan:
        return {"category": CAT_FLIGHT_PLAN, "parsed": fplan}

    # Ops labels
    if label in OPS_LABELS:
        ops = _parse_ops(label, text)
        return {"category": CAT_OPS, "parsed": ops}

    # H1 subtype classification for remaining messages
    if label == "H1":
        h1 = _parse_h1_subtype(text)
        if h1:
            return h1

    return {"category": CAT_UNKNOWN, "parsed": {}}


# ── OOOI ──────────────────────────────────────────────────────────────────

def _parse_oooi(msg, label, text):
    """Parse OOOI (Out/Off/On/In) events."""
    # acarsdec pre-parses these into top-level fields
    has_oooi_fields = any(k in msg for k in ("depa", "dsta", "gtout", "wloff", "wlon", "gtin"))

    if not has_oooi_fields and label not in OOOI_LABELS and label != "49":
        return None

    result = {}

    if msg.get("depa"):
        result["depa"] = msg["depa"].strip()
    if msg.get("dsta"):
        dsta = msg["dsta"].strip()
        if dsta:
            result["dsta"] = dsta
    if msg.get("gtout"):
        result["gtout"] = msg["gtout"].strip()
    if msg.get("wloff"):
        result["wloff"] = msg["wloff"].strip()
    if msg.get("wlon"):
        result["wlon"] = msg["wlon"].strip()
    if msg.get("gtin"):
        result["gtin"] = msg["gtin"].strip()
    if msg.get("eta"):
        result["eta"] = msg["eta"].strip()

    if label in OOOI_LABELS:
        result["event"] = OOOI_LABELS[label]

    # Label 49: TOIC format — "01TOIC ASA9981/112116KFTWKSEA"
    if label == "49" and "TOIC" in text:
        m = re.search(r'TOIC\s+\w+/(\d{6})(K[A-Z]{3})(K[A-Z]{3,4})', text)
        if m:
            result["event"] = "toic"
            result["time"] = m.group(1)
            result["depa"] = m.group(2)
            result["dsta"] = m.group(3)

    # Some QQ messages embed position data in text
    pos = _parse_position_from_qq(text)
    if pos:
        result["position"] = pos

    return result if result else None


def _parse_position_from_qq(text):
    """Extract position from QQ text like 'N3248.0W09721.2020177'."""
    m = re.search(r'([NS])(\d{2})(\d{2}\.\d)[EW](\d{3})(\d{2}\.\d)', text)
    if m:
        ns, lat_d, lat_m, lon_d, lon_m = m.groups()
        lat = float(lat_d) + float(lat_m) / 60
        lon = float(lon_d) + float(lon_m) / 60
        if ns == "S":
            lat = -lat
        lon = -lon  # DFW area is always W
        return {"lat": round(lat, 4), "lon": round(lon, 4)}
    return None


# ── Positions ─────────────────────────────────────────────────────────────

def _parse_position(label, text):
    """Try all position formats. Returns dict with lat, lon, and optional fields."""

    # H1 #M1BPOSN format: POSN32478W096547,DIETZ,021001,120,WHOOT,021241,HEDMN,P4,25134
    result = _parse_h1_posn(text)
    if result:
        return result

    # Label 80: POSRPT format with /POS /WYP /HDG /FL etc
    result = _parse_label80_posrpt(text)
    if result:
        return result

    # Label 12: "N 33.307,W 97.846,34000,021845, 274,.C-FCSX,0546"
    result = _parse_label12(text)
    if result:
        return result

    # Label 10: "/N32.490/W097.216/10/0.24/170/012/KCXO/1337/0070/00052"
    result = _parse_label10(text)
    if result:
        return result

    # Label 21: "POSN 32.772W 96.964, 296,114548,5377,26102, 22, 15,134936,KDTW"
    result = _parse_label21(text)
    if result:
        return result

    # Label 22: "N 324619W 965757,..."
    result = _parse_label22(text)
    if result:
        return result

    # Label 16: "190144,,, 379,N 32.948 W 97.333"
    result = _parse_label16(text)
    if result:
        return result

    # Label 4A: "151324,1541, 76,30437,N 32.192,W 96.690"
    result = _parse_label4a(text)
    if result:
        return result

    # Label 83: "001PR10133933N3249.1W09721.2006004"
    result = _parse_label83(text)
    if result:
        return result

    # H1 /A1 format: "/A1 005441, 31.8107,- 97.9854,268,..."
    result = _parse_h1_a1(text)
    if result:
        return result

    # Generic N/W decimal fallback
    result = _parse_generic_nw(text)
    if result:
        return result

    return None


def _parse_h1_posn(text):
    """Parse #M1BPOSN32478W096547,DIETZ,021001,120,WHOOT,021241,HEDMN,P4,25134,checksum"""
    m = re.search(r'POSN(\d{5})([EW])(\d{6}),(\w+),(\d{6}),(\d+),(\w+),(\d{6}),(\w+),([PM]\d+),(\d+)', text)
    if not m:
        return None

    lat_raw, ew, lon_raw, waypoint, time1, alt, next_wp, time2, after_wp, wind, fuel = m.groups()

    # lat_raw=32478 → 32.478°, lon_raw=096547 → 96.547°
    lat = float(lat_raw[:2]) + float(lat_raw[2:]) / 1000
    lon = float(lon_raw[:3]) + float(lon_raw[3:]) / 1000
    if ew == "W":
        lon = -lon

    return {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "alt": int(alt) * 100,
        "waypoint": waypoint,
        "next_waypoint": next_wp,
        "after_waypoint": after_wp,
        "time": time1,
        "next_time": time2,
        "wind": wind,
        "fuel": int(fuel),
        "format": "H1_POSN",
    }


def _parse_label80_posrpt(text):
    """Parse label 80 POSRPT: /POS N3229.5W09634.3/FL 168/WYP BELLS/..."""
    # Try deg.min format first: N3229.5W09634.3
    pos_m = re.search(r'/POS\s+([NS])(\d{2})(\d{2,3}\.\d)([EW])(\d{3})(\d{2,3}\.\d)', text)
    if not pos_m:
        # Try compact format: N32280W097317
        pos_m = re.search(r'/POS\s+([NS])(\d{2})(\d{3})([EW])(\d{3})(\d{3})', text)
        if pos_m:
            ns, lat_d, lat_frac, ew, lon_d, lon_frac = pos_m.groups()
            lat = float(lat_d) + float(lat_frac) / 1000
            lon = float(lon_d) + float(lon_frac) / 1000
            if ns == "S":
                lat = -lat
            if ew == "W":
                lon = -lon
        else:
            return None
    else:
        ns, lat_d, lat_m, ew, lon_d, lon_m = pos_m.groups()
        lat = float(lat_d) + float(lat_m) / 60
        lon = float(lon_d) + float(lon_m) / 60
        if ns == "S":
            lat = -lat
        if ew == "W":
            lon = -lon

    result = {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "format": "POSRPT",
    }

    fl = re.search(r'/FL\s+(\d+)', text)
    alt_m = re.search(r'/ALT\s+\+?(\d+)', text)
    if fl:
        result["alt"] = int(fl.group(1)) * 100
    elif alt_m:
        result["alt"] = int(alt_m.group(1))

    hdg = re.search(r'/HDG\s+(\d+)', text)
    if hdg:
        result["heading"] = int(hdg.group(1))

    mch = re.search(r'/MCH\s+(\d+)', text)
    if mch:
        result["mach"] = int(mch.group(1)) / 1000

    tas = re.search(r'/TAS\s+(\d+)', text)
    if tas:
        result["tas"] = int(tas.group(1))

    sat = re.search(r'/SAT\s+([+-]?\d+)', text)
    if sat:
        result["sat"] = int(sat.group(1))

    wyp = re.search(r'/WYP\s+(\w+)', text)
    if wyp:
        result["waypoint"] = wyp.group(1)

    nwyp = re.search(r'/NWYP\s+(\w+)', text)
    if nwyp:
        result["next_waypoint"] = nwyp.group(1)

    fob = re.search(r'/FOB\s+[NP]?(\d+)', text)
    if fob:
        result["fuel"] = int(fob.group(1))

    eta = re.search(r'/ETA\s+([\d:.]+)', text)
    if eta:
        result["eta"] = eta.group(1)

    dest = re.search(r'MSLP/(K[A-Z]{3})', text)
    if dest:
        result["destination"] = dest.group(1)

    swnd = re.search(r'/SWND\s+(\d+)', text)
    dwnd = re.search(r'/DWND\s+(\d+)', text)
    if swnd and dwnd:
        result["wind_speed"] = int(swnd.group(1))
        result["wind_dir"] = int(dwnd.group(1))

    return result


def _parse_label12(text):
    """Parse: N 33.307,W 97.846,34000,021845, 274,.tail,fuel"""
    m = re.search(r'([NS])\s*([\d.]+),\s*([EW])\s*([\d.]+),\s*(\d+),\s*(\d{6}),\s*(\d+)', text)
    if not m:
        return None

    ns, lat, ew, lon, alt, time_raw, heading = m.groups()
    lat = float(lat)
    lon = float(lon)
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon

    result = {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "alt": int(alt),
        "heading": int(heading),
        "time": time_raw,
        "format": "label12",
    }

    # Try to get fuel from end: .tail,fuel
    fuel_m = re.search(r',(\d{3,5})\s*$', text)
    if fuel_m:
        result["fuel"] = int(fuel_m.group(1))

    return result


def _parse_label10(text):
    """Parse: /N32.490/W097.216/.../KCXO/1337/0070/00052/waypoints"""
    m = re.search(r'/([NS])([\d.]+)/([EW])([\d.]+)/(\d+)/([\d.]+)/(\d+)/(\d+)/(K[A-Z]{3,4})/(\d{4})', text)
    if not m:
        return None

    ns, lat, ew, lon, _, _, gs, _, dest, eta = m.groups()
    lat = float(lat)
    lon = float(lon)
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon

    result = {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "groundspeed": int(gs),
        "destination": dest,
        "eta": eta,
        "format": "label10",
    }

    # Waypoints: /DARTZ/1305/BRDEN/1303/
    wps = re.findall(r'/([A-Z]{4,5})/(\d{4})/', text)
    if wps:
        result["waypoints"] = [{"name": w[0], "time": w[1]} for w in wps]

    # Fuel
    fuel_m = re.search(r'/(\d{4,5})/', text)
    if fuel_m:
        result["fuel"] = int(fuel_m.group(1))

    return result


def _parse_label21(text):
    """Parse: POSN 32.772W 96.964, 296,114548,5377,26102, 22, 15,134936,KDTW"""
    m = re.search(r'POSN\s+([\d.]+)([EW])\s*([\d.]+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)', text)
    if not m:
        return None

    lat, ew, lon, heading, time_raw, alt10, fuel = m.groups()

    result = {
        "lat": round(float(lat), 4),
        "lon": -round(float(lon), 4) if ew == "W" else round(float(lon), 4),
        "heading": int(heading),
        "time": time_raw,
        "alt": int(alt10) * 10,
        "fuel": int(fuel),
        "format": "label21",
    }

    # Destination at end
    dest = re.search(r'(K[A-Z]{3,4})\s*$', text)
    if dest:
        result["destination"] = dest.group(1)

    return result


def _parse_label22(text):
    """Parse: N 324619W 965757,..."""
    m = re.search(r'([NS])\s*(\d{2})(\d{2})(\d{2})([EW])\s*(\d{2,3})(\d{2})(\d{2})', text)
    if not m:
        return None

    ns, lat_d, lat_m, lat_s, ew, lon_d, lon_m, lon_s = m.groups()
    lat = float(lat_d) + float(lat_m) / 60 + float(lat_s) / 3600
    lon = float(lon_d) + float(lon_m) / 60 + float(lon_s) / 3600
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon

    return {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "format": "label22",
    }


def _parse_label16(text):
    """Parse: 190144,,, 379,N 32.948 W 97.333"""
    m = re.search(r'([NS])\s*([\d.]+)\s+([EW])\s*([\d.]+)', text)
    if not m:
        return None

    ns, lat, ew, lon = m.groups()
    lat = float(lat)
    lon = float(lon)
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon

    result = {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "format": "label16",
    }

    # Speed before position
    spd = re.search(r',\s*(\d{2,3}),\s*[NS]', text)
    if spd:
        result["groundspeed"] = int(spd.group(1))

    # Time at start
    tm = re.search(r'^(\d{6})', text)
    if tm:
        result["time"] = tm.group(1)

    return result


def _parse_label4a(text):
    """Parse: 151324,1541, 76,30437,N 32.192,W 96.690"""
    m = re.search(r'(\d{6}),\s*(\d{4}),\s*(\d+),\s*(\d+),\s*([NS])\s*([\d.]+),\s*([EW])\s*([\d.]+)', text)
    if not m:
        return None

    time_raw, eta, fuel, alt, ns, lat, ew, lon = m.groups()
    lat = float(lat)
    lon = float(lon)
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon

    return {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "alt": int(alt),
        "time": time_raw,
        "eta": eta,
        "fuel": int(fuel),
        "format": "label4A",
    }


def _parse_label83(text):
    """Parse: 001PR10133933N3249.1W09721.2006004"""
    m = re.search(r'([NS])(\d{2})(\d{2}\.\d)([EW])(\d{3})(\d{2}\.\d)', text)
    if not m:
        return None

    ns, lat_d, lat_m, ew, lon_d, lon_m = m.groups()
    lat = float(lat_d) + float(lat_m) / 60
    lon = float(lon_d) + float(lon_m) / 60
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon

    return {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "format": "label83",
    }


def _parse_h1_a1(text):
    """Parse /A1 format: /A1 005441, 31.8107,- 97.9854,268,123.2,426,..."""
    positions = []
    for match in re.finditer(
        r'/A(\d)\s+(\d{6}),\s*([-\d.]+),\s*([-\s\d.]+),\s*(\d+),\s*([\d.]+),\s*(\d+)',
        text
    ):
        seq, time_raw, lat, lon, heading, _, alt = match.groups()
        try:
            lat = float(lat)
            lon = float(lon.replace(" ", ""))
            positions.append({
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "alt": int(alt) * 100,
                "heading": int(heading),
                "time": time_raw,
                "format": "H1_A1",
            })
        except (ValueError, TypeError):
            continue

    if positions:
        # Return the latest position, include all as track
        result = positions[-1].copy()
        if len(positions) > 1:
            result["track"] = positions
        return result
    return None


def _parse_generic_nw(text):
    """Fallback: find any N xx.xxx W yy.yyy pattern."""
    m = re.search(r'([NS])\s*([\d.]{4,8})\s*[,/]?\s*([EW])\s*([\d.]{4,8})', text)
    if not m:
        return None

    ns, lat, ew, lon = m.groups()
    try:
        lat = float(lat)
        lon = float(lon)
    except ValueError:
        return None

    if lat < 10 or lat > 80 or lon < 10 or lon > 180:
        return None

    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon

    return {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "format": "generic",
    }


# ── Engine telemetry ──────────────────────────────────────────────────────

def _parse_engine(label, text):
    """Parse DFB engine reports and label 32/33 engine data."""
    # DFB reports: #DFBA319,... /REP.../CC.../C0.../C1.../CE...
    if "#DFB" in text:
        result = {"type": "dfb"}

        # Aircraft type
        atype = re.search(r'#DFB([A-Z]\d{3})', text)
        if atype:
            result["aircraft_type"] = atype.group(1)

        # Turbulence
        tb = re.search(r'TB(\d{6})', text)
        if tb:
            result["turbulence"] = tb.group(1)

        # Route: TRP ORIG DEST
        trp = re.search(r'TRP\s+(K[A-Z]{3})\s+(K[A-Z]{3})', text)
        if trp:
            result["origin"] = trp.group(1)
            result["destination"] = trp.group(2)

        # CC line: tail, date, time, origin, dest, flight
        cc = re.search(r'CC([A-Z0-9-]+),(\w{3}\d{2}),(\d{6}),(K[A-Z]{3,4}),(K[A-Z]{3,4}),(\d+)', text)
        if cc:
            result["tail"] = cc.group(1)
            result["date"] = cc.group(2)
            result["time"] = cc.group(3)
            result["origin"] = cc.group(4)
            result["destination"] = cc.group(5)

        # Positions from /A1 /A2 etc
        positions = _parse_h1_a1(text)
        if positions:
            result["position"] = positions

        return result

    # Label 32/33: engine parameter CSV
    if label in ("32", "33"):
        parts = text.split(",")
        if len(parts) >= 5:
            result = {"type": "engine_csv"}
            # Try to extract origin/dest from 3-letter codes
            for p in parts:
                p = p.strip()
                if len(p) == 3 and p.isalpha() and p.isupper():
                    if "origin" not in result:
                        result["origin"] = p
                    elif "destination" not in result:
                        result["destination"] = p
            return result

    return None


# ── Weather ───────────────────────────────────────────────────────────────

def _parse_weather(label, text):
    """Parse weather/winds data from ACARS messages."""
    if "/WX" not in text:
        return None

    result = {"type": "weather"}

    # WX reports: /WX02EN11KAUSKDTW\nN30397W09770819410865P0352680080
    wx_route = re.search(r'/WX\d+\w+\d+(K[A-Z]{3})(K[A-Z]{3,4})', text)
    if wx_route:
        result["origin"] = wx_route.group(1)
        result["destination"] = wx_route.group(2)

    # Parse wind observations: position + wind data
    # Format: N30397W097708 19410865 P035 268 0080
    wx_obs = re.findall(
        r'([NS])(\d{5})([EW])(\d{6})(\d{3})(\d{5})([PM]\d{3})(\d{3})(\d{4})',
        text
    )
    if wx_obs:
        observations = []
        for obs in wx_obs:
            ns, lat_r, ew, lon_r, heading, alt_raw, temp, wind_dir, wind_spd = obs
            lat = float(lat_r[:2]) + float(lat_r[2:]) / 1000
            lon = float(lon_r[:3]) + float(lon_r[3:]) / 1000
            if ns == "S":
                lat = -lat
            if ew == "W":
                lon = -lon
            observations.append({
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "alt": int(alt_raw),
                "temp_c": int(temp.replace("P", "+").replace("M", "-")),
                "wind_dir": int(wind_dir),
                "wind_speed": int(wind_spd),
            })
        result["observations"] = observations

    return result if len(result) > 1 else None


# ── Maintenance ───────────────────────────────────────────────────────────

def _parse_maintenance(label, text):
    """Parse maintenance/fault messages."""
    # ATA fault codes
    if "FAILED" in text or "FAULT" in text or "Equation ID" in text:
        result = {"type": "fault"}

        # ATA code
        ata = re.search(r'ATA(\d{2}-\d{2})', text)
        if ata:
            result["ata_code"] = ata.group(1)

        # System name
        sys_m = re.search(r'ATA\d{2}-\d{2}\s+(.+?)(?:\r?\n|$)', text)
        if sys_m:
            result["system"] = sys_m.group(1).strip()

        # Component
        comp = re.search(r'(?:FAILED|FAULT)[/\s]*(\w+)', text)
        if comp:
            result["component"] = comp.group(1)

        # Equation ID
        eq = re.search(r'Equation ID:\s*(\S+)', text)
        if eq:
            result["equation_id"] = eq.group(1)

        result["text"] = text.replace("\r\n", " ").strip()[:200]
        return result

    # #CFB prefix (common fault block)
    if text.startswith("#CFB") or "#CFB" in text:
        result = {"type": "fault", "text": text.replace("\r\n", " ").strip()[:200]}
        ata = re.search(r'ATA(\d{2}-\d{2})', text)
        if ata:
            result["ata_code"] = ata.group(1)
        return result

    return None


# ── Flight plan / route ───────────────────────────────────────────────────

def _parse_flight_plan(msg, label, text):
    """Parse flight plan, route, and departure info."""
    result = {}

    # acarsdec pre-parsed depa/dsta (label 20, etc)
    if msg.get("depa"):
        result["origin"] = msg["depa"].strip()
    if msg.get("dsta"):
        dsta = msg["dsta"].strip()
        if dsta:
            result["destination"] = dsta

    # Label 24/39: "102324 KDFW KATL7\n/FN 0374"
    if label in ("24", "30", "39"):
        route_m = re.search(r'(K[A-Z]{3,4})\s+(K[A-Z]{3,4})', text)
        if route_m:
            result["origin"] = route_m.group(1)
            result["destination"] = route_m.group(2)

        fn = re.search(r'/FN\s+(\d+)', text)
        if fn:
            result["flight_number"] = fn.group(1)

        # Label 30 often has free text after route — handle in free_text
        if label == "30":
            lines = text.split("\n")
            free_lines = [l.strip() for l in lines[2:] if l.strip() and not l.strip().startswith("/")]
            if free_lines:
                result["message"] = " ".join(free_lines)

    # H1 waypoint routes: WINDU.SSOLO.GABOO.SEWZY
    wpts = re.search(r'[:/]([A-Z]{3,5}(?:\.[A-Z]{3,5}){2,})', text)
    if wpts:
        result["waypoints"] = wpts.group(1).split(".")

    # H1 REQPWI (route request): /WQ230:WINDU.SSOLO.GABOO...
    wq = re.search(r'/WQ([\d.]+):(.+?)(?:/DQ|$)', text)
    if wq:
        result["requested_altitude"] = wq.group(1)
        wps = wq.group(2).strip()
        if "." in wps:
            result["waypoints"] = [w for w in wps.split(".") if w]

    # Label 20 with depa/dsta already parsed
    if label == "20" and result:
        return result

    return result if result else None


# ── Free text ─────────────────────────────────────────────────────────────

def _parse_free_text(label, text):
    """Parse pilot/dispatch free text messages."""
    # Label 30 and 39 are the primary free text labels
    if label not in ("30", "39"):
        return None

    result = {}

    # First line usually has route info: "111330 KAUS KDTW7"
    lines = text.split("\n")
    route_m = re.search(r'(K[A-Z]{3,4})\s+(K[A-Z]{3,4})', lines[0] if lines else "")
    if route_m:
        result["origin"] = route_m.group(1)
        result["destination"] = route_m.group(2)

    fn = re.search(r'/FN\s+(\d+)', text)
    if fn:
        result["flight_number"] = fn.group(1)

    # Extract the actual message body (skip route line and /FN line)
    free_lines = []
    for i, line in enumerate(lines):
        line = line.strip()
        if i >= 2 and line and not line.startswith("/FN") and not line.startswith("/TCI"):
            free_lines.append(line)

    if free_lines:
        result["message"] = " ".join(free_lines)
    elif not result:
        return None

    return result


# ── Ops ───────────────────────────────────────────────────────────────────

def _parse_h1_subtype(text):
    """Classify remaining H1 messages by their prefix."""
    # #M1BRESPWI / #M1BRESPOS — acknowledgement/response (link management)
    if text.startswith("#M1BRESP") or text.startswith("#M1BRESR"):
        return {"category": CAT_OPS, "parsed": {"type": "ack"}}

    # #M1BREQPWI — route/altitude request
    if text.startswith("#M1BREQ"):
        result = {}
        wq = re.search(r'/WQ([\d.]+):(.+?)(?:/DQ|$)', text)
        if wq:
            result["requested_altitude"] = wq.group(1)
            wps = wq.group(2).strip()
            if "." in wps:
                result["waypoints"] = [w for w in wps.split(".") if w]
        if result:
            return {"category": CAT_FLIGHT_PLAN, "parsed": result}
        return {"category": CAT_OPS, "parsed": {"type": "request"}}

    # #M1BFPN — flight plan notification
    if text.startswith("#M1BFPN"):
        result = {"type": "fpn"}
        # Extract waypoints
        wpts = re.findall(r'([A-Z]{3,5})', text[7:])
        if wpts:
            result["waypoints"] = wpts[:20]
        return {"category": CAT_FLIGHT_PLAN, "parsed": result}

    # #M1BPER — performance data
    if text.startswith("#M1BPER"):
        return {"category": CAT_ENGINE, "parsed": {"type": "performance"}}

    return None


def _parse_ops(label, text):
    """Parse airline ops messages (5Z, Q7, etc)."""
    result = {}

    # 5Z: "OS KDFW /IR KDFW0118"
    station = re.search(r'OS\s+(K[A-Z]{3,4})', text)
    if station:
        result["station"] = station.group(1)

    # ETAs
    eta = re.search(r'/EON(\d{4})', text) or re.search(r'/IR\s+\w+(\d{4})', text)
    if eta:
        result["eta"] = eta.group(1)

    return result


# ── Airport names ─────────────────────────────────────────────────────────

AIRPORT_NAMES = {
    # Texas / DFW area
    "KDFW": "Dallas Fort Worth International",
    "KDAL": "Dallas Love Field",
    "KFTW": "Fort Worth Meacham",
    "KAFW": "Fort Worth Alliance",
    "KADS": "Addison Airport",
    "KRBD": "Dallas Executive",
    "KGPM": "Grand Prairie Municipal",
    "KGKY": "Arlington Municipal",
    "KTKI": "McKinney National",
    "KFWS": "Fort Worth Spinks",
    "KCXO": "Conroe-North Houston Regional",
    "KIAH": "Houston George Bush Intercontinental",
    "KHOU": "Houston Hobby",
    "KAUS": "Austin-Bergstrom International",
    "KSAT": "San Antonio International",
    "KELP": "El Paso International",
    "KMAF": "Midland International",
    "KAMA": "Amarillo Rick Husband International",
    "KLBB": "Lubbock Preston Smith International",
    "KCRP": "Corpus Christi International",
    "KHRL": "Harlingen Valley International",
    "KMFE": "McAllen Miller International",
    # Major US hubs
    "KATL": "Atlanta Hartsfield-Jackson",
    "KORD": "Chicago O'Hare",
    "KMDW": "Chicago Midway",
    "KLAX": "Los Angeles International",
    "KJFK": "New York JFK",
    "KLGA": "New York LaGuardia",
    "KEWR": "Newark Liberty International",
    "KSFO": "San Francisco International",
    "KDEN": "Denver International",
    "KSEA": "Seattle-Tacoma International",
    "KMCO": "Orlando International",
    "KBOS": "Boston Logan International",
    "KMSP": "Minneapolis-St. Paul International",
    "KDTW": "Detroit Metro Wayne County",
    "KPHL": "Philadelphia International",
    "KCLT": "Charlotte Douglas International",
    "KMIA": "Miami International",
    "KFLL": "Fort Lauderdale-Hollywood International",
    "KTPA": "Tampa International",
    "KPHX": "Phoenix Sky Harbor International",
    "KLAS": "Las Vegas Harry Reid International",
    "KSLC": "Salt Lake City International",
    "KBWI": "Baltimore/Washington International",
    "KIAD": "Washington Dulles International",
    "KDCA": "Washington Reagan National",
    "KSAN": "San Diego International",
    "KPDX": "Portland International",
    "KSTL": "St. Louis Lambert International",
    "KMCI": "Kansas City International",
    "KBNA": "Nashville International",
    "KRDU": "Raleigh-Durham International",
    "KCLE": "Cleveland Hopkins International",
    "KPIT": "Pittsburgh International",
    "KCVG": "Cincinnati/Northern Kentucky International",
    "KIND": "Indianapolis International",
    "KMKE": "Milwaukee Mitchell International",
    "KSMF": "Sacramento International",
    "KONT": "Ontario International",
    "KOAK": "Oakland International",
    "KSJC": "San Jose Mineta International",
    "KABQ": "Albuquerque International Sunport",
    "KTUS": "Tucson International",
    "KMEM": "Memphis International",
    "KMSN": "Madison Dane County Regional",
    "KJAN": "Jackson-Medgar Wiley Evers International",
    "KLIT": "Little Rock Clinton National",
    "KOKC": "Oklahoma City Will Rogers World",
    "KTUL": "Tulsa International",
    "KMSY": "New Orleans Louis Armstrong International",
    "KJAX": "Jacksonville International",
    "KRSW": "Fort Myers Southwest Florida International",
    "KPBI": "West Palm Beach International",
    "KBUF": "Buffalo Niagara International",
    "KSYR": "Syracuse Hancock International",
    "KPVD": "Providence T.F. Green International",
    "KBDL": "Hartford Bradley International",
    "KALB": "Albany International",
    "KROC": "Rochester Greater Rochester International",
    "KANC": "Anchorage Ted Stevens International",
    "PHNL": "Honolulu Daniel K. Inouye International",
    # Canadian
    "CYYZ": "Toronto Pearson International",
    "CYVR": "Vancouver International",
    "CYUL": "Montreal-Trudeau International",
    "CYYC": "Calgary International",
    # Mexican
    "MMMX": "Mexico City International",
    "MMUN": "Cancun International",
}


def airport_name(code):
    """Convert ICAO airport code to full name. Returns 'CODE (Unknown)' if not found."""
    if not code:
        return ""
    code = code.strip().upper()
    name = AIRPORT_NAMES.get(code)
    if name:
        return name
    # Try without K prefix for domestic
    if code.startswith("K") and len(code) == 4:
        return code[1:] + " Airport"
    return code


def _fmt_alt(alt):
    """Format altitude: 35000 -> 'FL350 (35,000 ft)'."""
    if not alt:
        return ""
    alt = int(alt)
    if alt >= 18000:
        return f"FL{alt // 100} ({alt:,} ft)"
    return f"{alt:,} ft"


def _fmt_heading(hdg):
    """Format heading with cardinal direction."""
    if hdg is None:
        return ""
    hdg = int(hdg) % 360
    dirs = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
    idx = round(hdg / 45) % 8
    return f"{hdg}° ({dirs[idx]})"


def _fmt_wind(wind_str):
    """Parse wind string like 'P4' or 'M12' -> '+4°C' or '-12°C'."""
    if not wind_str:
        return ""
    if wind_str.startswith("P"):
        return f"+{wind_str[1:]}°C"
    if wind_str.startswith("M"):
        return f"-{wind_str[1:]}°C"
    return wind_str


# ── Message summary (plain English) ──────────────────────────────────────

def summarize_message(category, parsed, flight="", tail=""):
    """Generate a plain English summary of a parsed ACARS message.

    Returns a human-readable string describing what the message contains
    with actual values, not generic descriptions.
    """
    if not parsed or not isinstance(parsed, dict):
        return ""

    parts = []
    who = flight.strip() if flight else (tail.strip() if tail else "Aircraft")

    if category == CAT_POSITION:
        lat = parsed.get("lat")
        lon = parsed.get("lon")
        alt = parsed.get("alt")
        hdg = parsed.get("heading")
        gs = parsed.get("groundspeed")
        wp = parsed.get("waypoint")
        nwp = parsed.get("next_waypoint")
        dest = parsed.get("destination")
        fuel = parsed.get("fuel")
        eta = parsed.get("eta")

        if lat is not None and lon is not None:
            parts.append(f"Position: {abs(lat):.3f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.3f}°{'W' if lon < 0 else 'E'}")
        if alt:
            parts.append(f"Altitude: {_fmt_alt(alt)}")
        if hdg is not None:
            parts.append(f"Heading: {_fmt_heading(hdg)}")
        if gs:
            parts.append(f"Ground speed: {gs} knots")
        if wp:
            wp_text = f"Over waypoint {wp}"
            if nwp:
                wp_text += f", next waypoint {nwp}"
            parts.append(wp_text)
        if dest:
            parts.append(f"Destination: {airport_name(dest)}")
        if fuel:
            parts.append(f"Fuel remaining: {fuel:,} lbs")
        if eta:
            parts.append(f"ETA: {eta[:2]}:{eta[2:]}" if len(eta) == 4 else f"ETA: {eta}")

    elif category == CAT_OOOI:
        events = {
            "out": "pushed back from the gate",
            "off": "wheels off the runway (airborne)",
            "on": "wheels on the runway (landed)",
            "in": "arrived at the gate",
            "toic": "time of initial contact",
        }
        event = parsed.get("event")
        depa = parsed.get("depa")
        dsta = parsed.get("dsta")
        eta = parsed.get("eta")

        if event:
            parts.append(events.get(event, event).capitalize())
        if depa and dsta:
            parts.append(f"Flying from {airport_name(depa)} to {airport_name(dsta)}")
        elif depa:
            parts.append(f"Departing {airport_name(depa)}")
        elif dsta:
            parts.append(f"Arriving at {airport_name(dsta)}")

        times = []
        if parsed.get("gtout"):
            times.append(f"gate departure {parsed['gtout']}")
        if parsed.get("wloff"):
            times.append(f"takeoff {parsed['wloff']}")
        if parsed.get("wlon"):
            times.append(f"landing {parsed['wlon']}")
        if parsed.get("gtin"):
            times.append(f"gate arrival {parsed['gtin']}")
        if times:
            parts.append("Times: " + ", ".join(times))
        if eta:
            parts.append(f"ETA: {eta[:2]}:{eta[2:]}" if len(eta) == 4 else f"ETA: {eta}")

    elif category == CAT_WEATHER:
        origin = parsed.get("origin")
        dest = parsed.get("destination")
        obs = parsed.get("observations", [])

        if origin and dest:
            parts.append(f"Weather report: {airport_name(origin)} to {airport_name(dest)}")
        elif origin:
            parts.append(f"Weather report from {airport_name(origin)}")

        for ob in obs[:3]:
            wind_dir = ob.get("wind_dir")
            wind_spd = ob.get("wind_speed")
            temp = ob.get("temp_c")
            alt = ob.get("alt")
            ob_parts = []
            if wind_dir is not None and wind_spd is not None:
                ob_parts.append(f"wind from {wind_dir}° at {wind_spd} knots")
            if temp is not None:
                temp_f = round(temp * 9 / 5 + 32)
                ob_parts.append(f"temperature {temp_f}°F ({temp}°C)")
            if alt:
                ob_parts.append(f"at {_fmt_alt(alt)}")
            if ob_parts:
                parts.append("Conditions: " + ", ".join(ob_parts))

    elif category == CAT_ENGINE:
        etype = parsed.get("type", "")
        origin = parsed.get("origin")
        dest = parsed.get("destination")
        atype = parsed.get("aircraft_type")

        if etype == "dfb":
            parts.append("Engine and flight data report — automated systems snapshot sent to airline maintenance")
            if atype:
                aircraft_types = {
                    "A319": "Airbus A319", "A320": "Airbus A320", "A321": "Airbus A321",
                    "A332": "Airbus A330-200", "A333": "Airbus A330-300",
                    "B737": "Boeing 737", "B738": "Boeing 737-800", "B739": "Boeing 737-900",
                    "B752": "Boeing 757-200", "B753": "Boeing 757-300",
                    "B763": "Boeing 767-300", "B772": "Boeing 777-200", "B773": "Boeing 777-300",
                    "B788": "Boeing 787-8 Dreamliner", "B789": "Boeing 787-9 Dreamliner",
                    "CRJ2": "Bombardier CRJ-200", "CRJ7": "Bombardier CRJ-700",
                    "CRJ9": "Bombardier CRJ-900", "E170": "Embraer E170", "E175": "Embraer E175",
                    "E190": "Embraer E190", "E75L": "Embraer E175 Long Range",
                }
                parts.append(f"Aircraft type: {aircraft_types.get(atype, atype)}")
        elif etype == "performance":
            parts.append("Performance report — engine efficiency and flight parameter data")
        elif etype == "engine_csv":
            parts.append("Engine parameter data — RPM, temperatures, pressures sent to maintenance systems")
        else:
            parts.append("Engine telemetry data")

        if origin and dest:
            parts.append(f"Route: {airport_name(origin)} to {airport_name(dest)}")

    elif category == CAT_FLIGHT_PLAN:
        origin = parsed.get("origin")
        dest = parsed.get("destination")
        fn = parsed.get("flight_number")
        wps = parsed.get("waypoints", [])
        req_alt = parsed.get("requested_altitude")

        if req_alt:
            # Wind prediction request — decode the altitudes
            alts = req_alt.split(".")
            alt_strs = [_fmt_alt(int(a) * 100) for a in alts if a]
            parts.append(f"Requesting wind forecasts at {', '.join(alt_strs)}")
        elif origin and dest:
            parts.append(f"Flight plan: {airport_name(origin)} to {airport_name(dest)}")
        if fn:
            parts.append(f"Flight number: {fn}")
        if wps:
            parts.append(f"Route: {' → '.join(wps[:8])}")
            if len(wps) > 8:
                parts[-1] += " …"

    elif category == CAT_MAINTENANCE:
        ata = parsed.get("ata_code")
        system = parsed.get("system")
        component = parsed.get("component")
        text = parsed.get("text", "")

        # ATA chapter names
        ata_chapters = {
            "21": "Air Conditioning & Pressurization",
            "22": "Auto Flight",
            "23": "Communications",
            "24": "Electrical Power",
            "25": "Equipment & Furnishings",
            "26": "Fire Protection",
            "27": "Flight Controls",
            "28": "Fuel System",
            "29": "Hydraulic Power",
            "30": "Ice & Rain Protection",
            "31": "Instruments",
            "32": "Landing Gear",
            "33": "Lights",
            "34": "Navigation",
            "35": "Oxygen",
            "36": "Pneumatic",
            "38": "Water & Waste",
            "49": "Auxiliary Power Unit (APU)",
            "52": "Doors",
            "71": "Power Plant",
            "72": "Engine (Turbine/Turboprop)",
            "73": "Engine Fuel & Control",
            "74": "Ignition",
            "75": "Air (Engine Bleed)",
            "76": "Engine Controls",
            "77": "Engine Indicating",
            "78": "Exhaust",
            "79": "Oil",
            "80": "Starting",
        }

        parts.append("MAINTENANCE FAULT REPORTED")
        if ata:
            chapter = ata.split("-")[0] if "-" in ata else ata[:2]
            chapter_name = ata_chapters.get(chapter, "")
            if chapter_name:
                parts.append(f"System: {chapter_name} (ATA {ata})")
            else:
                parts.append(f"ATA code: {ata}")
        if system:
            parts.append(f"Issue: {system}")
        elif component:
            parts.append(f"Component: {component}")

        # Parse CFB fault text for human-readable details
        if text:
            # Extract meaningful parts from CFB messages
            # e.g., "CHECK FDU APU LOOP AWARN CKT" or "AIR SUPPLY & CABIN PRESSURE CONTROLLER"
            meaningful = ""
            for keyword in ["CHECK ", "FAIL", "FAULT", "WARN", "ALERT", "INOP",
                            "AIR SUPPLY", "CABIN PRESSURE", "LAVATORY", "OXYGEN",
                            "HYDRAULIC", "FUEL", "ENGINE", "APU", "GENERATOR",
                            "SMOKE", "FIRE", "DOOR", "GEAR", "BRAKE", "TIRE",
                            "BLEED", "PACK", "VALVE", "PUMP", "SENSOR", "PROBE"]:
                if keyword in text.upper():
                    # Find the section containing this keyword
                    idx = text.upper().find(keyword)
                    # Get surrounding context
                    start = max(0, text.rfind(" ", 0, max(0, idx - 20)))
                    end = min(len(text), text.find("/", idx + 1) if "/" in text[idx:] else len(text))
                    snippet = text[start:end].strip()
                    if snippet and len(snippet) > len(meaningful):
                        meaningful = snippet
            if meaningful and not system:
                parts.append(f"Detail: {meaningful}")

    elif category == CAT_FREE_TEXT:
        origin = parsed.get("origin")
        dest = parsed.get("destination")
        message = parsed.get("message")
        fn = parsed.get("flight_number")

        if origin and dest:
            parts.append(f"Message: {airport_name(origin)} to {airport_name(dest)}")
        if fn:
            parts.append(f"Flight {fn}")
        if message:
            parts.append(message[:200])

    elif category == CAT_OPS:
        etype = parsed.get("type")
        station = parsed.get("station")
        eta = parsed.get("eta")

        if etype == "ack":
            parts.append("System acknowledgment — confirming data received")
        elif station:
            parts.append(f"Operations check-in at {airport_name(station)}")
        if eta:
            parts.append(f"ETA: {eta[:2]}:{eta[2:]}" if len(eta) == 4 else f"ETA: {eta}")

    elif category == CAT_KEEPALIVE:
        parts.append("Heartbeat — aircraft checking in with airline data link")

    return ". ".join(parts) if parts else ""


# ── Public API ────────────────────────────────────────────────────────────

def parse_and_enrich(msg):
    """Parse a raw acarsdec message and return enriched version.

    Returns a new dict with all original fields plus:
      - category: str
      - parsed: dict with structured extracted data
      - summary: str with plain English description
    """
    result = dict(msg)  # shallow copy
    parsed = parse_acars_message(msg)
    result["category"] = parsed["category"]
    result["parsed"] = parsed["parsed"]
    result["summary"] = summarize_message(
        parsed["category"], parsed["parsed"],
        flight=msg.get("flight", ""),
        tail=msg.get("tail", ""),
    )
    return result
