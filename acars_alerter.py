#!/usr/bin/env python3
"""
acars_alerter.py — Sends Discord alerts for aircraft-reported faults detected in ACARS.

Only alerts on aircraft-self-classified faults (CFBFLR, CFBWRN, CFBFDE, HARD severity,
fault keywords). Does NOT alert on assumed numeric thresholds.

Called by dashboard_server.py when a WARNING or CAUTION-level message is detected.
"""

import json
import time
import urllib.request
import urllib.error
from datetime import datetime

# Discord air-traffic channel webhook
DISCORD_WEBHOOK_AIR_TRAFFIC = "https://discord.com/api/webhooks/1471574249016918017/4iOzhaXMBTyk0W-Td7zBwTj1esu13QUohqTMJGYGbY5gYOpixvX515-gEN_QelMDXtcN"

# Cooldown: don't spam the same flight+fault within 10 minutes
_alert_cooldown = {}
COOLDOWN_SECONDS = 600


def _cooldown_key(msg):
    """Generate dedup key for cooldown."""
    flight = (msg.get("flight") or msg.get("tail") or "").strip()
    label = msg.get("label", "")
    reason = msg.get("alert_reason", "")
    return f"{flight}-{label}-{reason}"


def should_alert(msg):
    """Check if this message warrants a Discord alert."""
    level = msg.get("alert_level", "none")
    if level not in ("warning", "caution"):
        return False

    key = _cooldown_key(msg)
    now = time.time()
    last_sent = _alert_cooldown.get(key, 0)
    if now - last_sent < COOLDOWN_SECONDS:
        return False

    return True


def send_alert(msg):
    """Send a Discord alert for an aircraft-reported fault."""
    level = msg.get("alert_level", "none")
    if not should_alert(msg):
        return False

    flight = (msg.get("flight") or "").strip()
    tail = (msg.get("tail") or "").strip()
    summary = msg.get("summary", "")
    reason = msg.get("alert_reason", "")
    details = msg.get("alert_details", {})
    category = msg.get("category", "")
    freq = msg.get("freq", "?")
    ts = msg.get("timestamp", time.time())
    time_str = datetime.fromtimestamp(ts).strftime("%I:%M:%S %p CDT")

    # Color based on level
    color = 0xff0000 if level == "warning" else 0xffa500  # red or orange

    # Title
    level_label = "AIRCRAFT FAULT" if level == "warning" else "CAUTION"
    title = f"{'🔴' if level == 'warning' else '🟡'} {level_label}: {flight or tail}"

    # Get parsed maintenance data for enrichment
    parsed = msg.get("parsed", {})

    # Description
    desc_parts = [
        f"**Flight:** {flight or 'N/A'}",
        f"**Tail:** {tail or 'N/A'}",
        f"**Time:** {time_str}",
        f"**Frequency:** {freq} MHz",
        f"**Alert Reason:** {reason}",
    ]

    # ── Enriched fault details ──────────────────────────────────
    # ATA system with chapter name
    ata_code = details.get("ata_code") or parsed.get("ata_code", "")
    if ata_code:
        chapter = ata_code.split("-")[0] if "-" in ata_code else ata_code[:2]
        # Inline ATA lookup for common chapters
        ata_names = {
            "21": "Air Conditioning", "22": "Auto Flight", "23": "Communications",
            "24": "Electrical Power", "26": "Fire Protection", "27": "Flight Controls",
            "28": "Fuel", "29": "Hydraulic Power", "31": "Instruments",
            "32": "Landing Gear", "34": "Navigation", "36": "Pneumatic",
            "49": "APU", "71": "Power Plant", "72": "Engine", "73": "Engine Fuel",
            "75": "Engine Bleed", "76": "Engine Controls", "77": "Engine Indicating",
            "79": "Oil", "80": "Starting",
        }
        ch_name = ata_names.get(chapter, "")
        if ch_name:
            desc_parts.append(f"**ATA System:** {ch_name} (ATA {ata_code})")
        else:
            desc_parts.append(f"**ATA Code:** {ata_code}")

    # Severity (HARD/INT)
    severity = parsed.get("severity")
    if severity:
        sev_label = "PERSISTENT" if severity == "HARD" else "INTERMITTENT"
        desc_parts.append(f"**Severity:** {sev_label}")

    # Flight phase
    flight_phase = parsed.get("flight_phase")
    if flight_phase:
        desc_parts.append(f"**Flight Phase:** {flight_phase}")

    # Fault time from CFB
    fault_time = parsed.get("fault_time")
    if fault_time:
        desc_parts.append(f"**Fault Time:** {fault_time}")

    # Source system
    if details.get("system"):
        desc_parts.append(f"**Source:** {details['system']}")
    if details.get("component") and details.get("component") != details.get("system"):
        desc_parts.append(f"**Component:** {details['component']}")

    # Affected/correlated systems (decoded)
    affected = parsed.get("affected_systems_decoded")
    if affected:
        desc_parts.append(f"**Also Affected:** {', '.join(affected[:6])}")

    # Engine divergence (existing)
    if details.get("divergent_engine"):
        desc_parts.append(f"**Divergent Engine:** #{details['divergent_engine']}")
        desc_parts.append(f"**Values:** {details.get('values', [])}")
        desc_parts.append(f"**Average:** {details.get('average', 'N/A')}")

    # Decoded summary
    if summary:
        desc_parts.append(f"\n**Decoded:** {summary}")

    # Raw text (truncated)
    raw = (msg.get("text") or "").strip()
    if raw:
        desc_parts.append(f"\n**Raw ACARS:**\n```\n{raw[:300]}\n```")

    embed = {
        "title": title,
        "description": "\n".join(desc_parts),
        "color": color,
        "timestamp": datetime.utcfromtimestamp(ts).isoformat() + "Z",
        "footer": {"text": f"Aviation SDR | ACARS {category} | Pi #2"}
    }

    payload = json.dumps({"embeds": [embed]}).encode()
    req = urllib.request.Request(
        DISCORD_WEBHOOK_AIR_TRAFFIC,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Aviation-SDR-Alerter/1.0"
        },
        method="POST"
    )

    try:
        resp = urllib.request.urlopen(req, timeout=15)
        # Record cooldown
        key = _cooldown_key(msg)
        _alert_cooldown[key] = time.time()
        return True
    except urllib.error.HTTPError as e:
        print(f"[ALERT] Discord HTTP error {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"[ALERT] Discord error: {e}")
        return False
