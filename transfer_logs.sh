#!/bin/bash
# Transfer and rotate logs from Pi to main PC
# Runs daily via cron at 3:00 AM
#
# Valuable logs (transfer then truncate on Pi):
#   - aviation_scan_log.csv    (transmission history — primary data)
#   - airband_events.log       (RX events with timestamps)
#   - transfer_log.txt         (SCP transfer history)
#   - channel_activity.csv     (channel hit counts)
#
# Stale files (delete, not needed):
#   - signal_log.csv           (old RF listener, no longer used)
#   - sessions/                (old RF listener sessions)
#   - rf_listener.log          (old RF listener)
#   - cb_scan_log.csv          (old CB scan experiment)
#   - ham10_scan_log.csv       (old ham scan experiment)
#   - *_tmp.csv                (empty temp files)
#   - airband_stats.txt        (old combined stats, replaced by per-dongle stats)

REMOTE="mainpc"
REMOTE_LOG_DIR="C:/ProScan/Recordings/Aviation-SDR/logs"
PI_DIR="/home/pi/closecall"
DATE=$(date '+%Y%m%d')

# ── Transfer valuable logs ───────────────────────────────────────────────
for logfile in aviation_scan_log.csv airband_events.log transfer_log.txt channel_activity.csv; do
    src="$PI_DIR/$logfile"
    [ -f "$src" ] || continue

    # Append date to filename for archiving
    base="${logfile%.*}"
    ext="${logfile##*.}"
    dest="${REMOTE_LOG_DIR}/${base}_${DATE}.${ext}"

    if scp -o ConnectTimeout=10 -o BatchMode=yes "$src" "$REMOTE:$dest" 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') LOG_TRANSFER $logfile -> $dest" >> "$PI_DIR/transfer_log.txt"

        # Truncate on Pi (keep CSV headers)
        if [ "$ext" = "csv" ]; then
            head -1 "$src" > "${src}.tmp" && mv "${src}.tmp" "$src"
        else
            > "$src"
        fi
    fi
done

# ── Clean up stale files ─────────────────────────────────────────────────
rm -f "$PI_DIR/signal_log.csv"
rm -f "$PI_DIR/rf_listener.log"
rm -f "$PI_DIR/cb_scan_log.csv"
rm -f "$PI_DIR/ham10_scan_log.csv"
rm -f "$PI_DIR/scan_tmp.csv"
rm -f "$PI_DIR/cb_scan_tmp.csv"
rm -f "$PI_DIR/band_scan_tmp.csv"
rm -f "$PI_DIR/airband_stats.txt"
rm -rf "$PI_DIR/sessions"

# ── Trim real-time stats files if they grow too large ────────────────────
for statsfile in airband_approach_stats.txt airband_scan_stats.txt; do
    f="$PI_DIR/$statsfile"
    [ -f "$f" ] && [ "$(wc -c < "$f")" -gt 100000 ] && > "$f"
done
