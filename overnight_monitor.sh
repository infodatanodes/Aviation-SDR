#!/bin/bash
# Aviation SDR Overnight Monitor
# Collects system snapshots every 15 minutes until 4:00 AM
# Output: /tmp/aviation_overnight_report.txt

REPORT="/tmp/aviation_overnight_report.txt"
PI="pi@100.68.206.39"
END_TIME="04:00"
INTERVAL=900  # 15 minutes

echo "===========================================================" > "$REPORT"
echo "  AVIATION SDR OVERNIGHT MONITOR" >> "$REPORT"
echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')" >> "$REPORT"
echo "  End target: $END_TIME" >> "$REPORT"
echo "===========================================================" >> "$REPORT"
echo "" >> "$REPORT"

SNAPSHOT=0

while true; do
    HOUR=$(date '+%H')

    # Stop at 4 AM (hours 04-20 mean we've passed 4 AM)
    if [ "$HOUR" -ge 4 ] && [ "$HOUR" -le 20 ]; then
        break
    fi

    SNAPSHOT=$((SNAPSHOT + 1))
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

    echo "──────────────────────────────────────────────────────────" >> "$REPORT"
    echo "  SNAPSHOT #$SNAPSHOT — $TIMESTAMP" >> "$REPORT"
    echo "──────────────────────────────────────────────────────────" >> "$REPORT"

    # Pi health
    PI_DATA=$(ssh $PI "
        echo 'TEMP:' \$(vcgencmd measure_temp)
        echo 'LOAD:' \$(cat /proc/loadavg | cut -d' ' -f1-3)
        echo 'RAM:' \$(free -m | awk '/Mem:/{print \$3\"/\"\$2\"MB\"}')
        echo 'DISK:' \$(df -h / | awk 'NR==2{print \$3\"/\"\$2\" (\"\$5\")\"}')
    " 2>/dev/null)

    echo "" >> "$REPORT"
    echo "  PI HEALTH:" >> "$REPORT"
    echo "$PI_DATA" | sed 's/^/    /' >> "$REPORT"

    # Service status
    SVC=$(ssh $PI "systemctl is-active rtl-airband-approach rtl-airband-scan airband-display" 2>/dev/null)
    echo "" >> "$REPORT"
    echo "  SERVICES: $(echo $SVC | tr '\n' ' ')" >> "$REPORT"

    # Channel stats (squelch opens = transmissions received)
    APPROACH_SQ=$(ssh $PI "cat /home/pi/closecall/airband_approach_stats.txt 2>/dev/null | grep 'channel_squelch_counter{' | awk -F'\t' '{print \$2}'" 2>/dev/null)
    APPROACH_SIG=$(ssh $PI "cat /home/pi/closecall/airband_approach_stats.txt 2>/dev/null | grep 'channel_dbfs_signal_level{' | awk -F'\t' '{print \$2}'" 2>/dev/null)

    echo "" >> "$REPORT"
    echo "  APPROACH (132.922): squelch_opens=$APPROACH_SQ signal=${APPROACH_SIG}dBFS" >> "$REPORT"

    ssh $PI "cat /home/pi/closecall/airband_scan_stats.txt 2>/dev/null | grep -E 'channel_squelch_counter|channel_dbfs_signal_level'" 2>/dev/null | while read line; do
        FREQ=$(echo "$line" | grep -oP 'freq="\K[^"]+')
        LABEL=$(echo "$line" | grep -oP 'label="\K[^"]+')
        VAL=$(echo "$line" | awk -F'\t' '{print $2}')
        TYPE=$(echo "$line" | grep -oP '# HELP \K\w+' || echo "")
        if echo "$line" | grep -q 'squelch_counter'; then
            echo "  SCANNER $LABEL ($FREQ): squelch_opens=$VAL" >> "$REPORT"
        elif echo "$line" | grep -q 'dbfs_signal_level'; then
            echo "  SCANNER $LABEL ($FREQ): signal=${VAL}dBFS" >> "$REPORT"
        fi
    done

    # Recording counts and recent activity
    REC_COUNT=$(ssh $PI "ls /home/pi/closecall/recordings/*.mp3 2>/dev/null | wc -l" 2>/dev/null)
    CSV_COUNT=$(ssh $PI "wc -l < /home/pi/closecall/aviation_scan_log.csv 2>/dev/null" 2>/dev/null)
    LAST_REC=$(ssh $PI "ls -t /home/pi/closecall/recordings/*.mp3 2>/dev/null | head -1 | xargs -I{} stat -c '%Y' {} 2>/dev/null" 2>/dev/null)
    NOW_EPOCH=$(date +%s)
    if [ -n "$LAST_REC" ] && [ "$LAST_REC" -gt 0 ] 2>/dev/null; then
        AGO=$(( (NOW_EPOCH - LAST_REC) ))
        echo "  RECORDINGS: $REC_COUNT pending transfer, last ${AGO}s ago" >> "$REPORT"
    else
        echo "  RECORDINGS: $REC_COUNT pending transfer" >> "$REPORT"
    fi
    echo "  CSV LOG: $CSV_COUNT entries" >> "$REPORT"

    # Last 3 CSV entries for activity tracking
    echo "" >> "$REPORT"
    echo "  RECENT TRANSMISSIONS:" >> "$REPORT"
    ssh $PI "tail -3 /home/pi/closecall/aviation_scan_log.csv 2>/dev/null" 2>/dev/null | while read line; do
        echo "    $line" >> "$REPORT"
    done

    # Ops Center aviation log count
    OPS_COUNT=$(ssh mainpc "curl -s http://localhost:8085/api/aviation-log?limit=1" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total","?"))' 2>/dev/null)
    echo "  OPS CENTER TOTAL: $OPS_COUNT aviation entries" >> "$REPORT"

    # Main PC recording transfers
    PC_LATEST=$(ssh mainpc "powershell -Command \"(Get-ChildItem 'C:\ProScan\Recordings\Aviation-SDR\' -File | Sort LastWriteTime -Desc | Select -First 1).LastWriteTime.ToString('HH:mm:ss')\"" 2>/dev/null | tr -d '\r')
    echo "  LATEST PC TRANSFER: $PC_LATEST" >> "$REPORT"

    echo "" >> "$REPORT"

    sleep $INTERVAL
done

echo "===========================================================" >> "$REPORT"
echo "  MONITOR ENDED: $(date '+%Y-%m-%d %H:%M:%S')" >> "$REPORT"
echo "  Total snapshots: $SNAPSHOT" >> "$REPORT"
echo "===========================================================" >> "$REPORT"

# Final summary stats
echo "" >> "$REPORT"
echo "  FINAL STATS:" >> "$REPORT"
ssh $PI "echo '  Approach squelch opens:' \$(cat /home/pi/closecall/airband_approach_stats.txt 2>/dev/null | grep 'channel_squelch_counter{' | awk -F'\t' '{print \$2}')" 2>/dev/null >> "$REPORT"
ssh $PI "cat /home/pi/closecall/airband_scan_stats.txt 2>/dev/null | grep 'channel_squelch_counter{'" 2>/dev/null | while read line; do
    FREQ=$(echo "$line" | grep -oP 'freq="\K[^"]+')
    LABEL=$(echo "$line" | grep -oP 'label="\K[^"]+')
    VAL=$(echo "$line" | awk -F'\t' '{print $2}')
    echo "  Scanner $LABEL ($FREQ) squelch opens: $VAL" >> "$REPORT"
done
CSV_FINAL=$(ssh $PI "wc -l < /home/pi/closecall/aviation_scan_log.csv 2>/dev/null" 2>/dev/null)
OPS_FINAL=$(ssh mainpc "curl -s http://localhost:8085/api/aviation-log?limit=1" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total","?"))' 2>/dev/null)
echo "  CSV log entries: $CSV_FINAL" >> "$REPORT"
echo "  Ops Center total: $OPS_FINAL" >> "$REPORT"
echo "" >> "$REPORT"
echo "  Report saved to: $REPORT" >> "$REPORT"
