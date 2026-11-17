#!/usr/bin/env bash
# A hardware adapter only: Connect schedules, timestamps and transports events.
# No address, interface name, hostname or raw command output leaves this script.
set -euo pipefail
export LC_ALL=C
read -r temp < /sys/class/thermal/thermal_zone0/temp
read -r freq < /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
read -r load _ < /proc/loadavg
throttle="$(vcgencmd get_throttled 2>/dev/null)"
[[ "$throttle" =~ ^throttled=0x[0-9a-fA-F]+$ ]] || { echo 'Cannot read throttling flags' >&2; exit 1; }
flags=$(( ${throttle#throttled=} ))
read -r total available < <(awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END {print t, a}' /proc/meminfo)
read -r disk_total disk_used < <(df -B1 --output=size,used / | tail -n 1)
# Select the physical interface locally; only the counters leave the Pi.
iface="${HEALTH_INTERFACE:?}"
[[ "$iface" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'Invalid interface setting' >&2; exit 1; }
read -r rx < "/sys/class/net/$iface/statistics/rx_bytes"
read -r tx < "/sys/class/net/$iface/statistics/tx_bytes"
for value in "$temp" "$freq" "$total" "$available" "$disk_total" "$disk_used" "$rx" "$tx"; do
  [[ "$value" =~ ^[0-9]+$ ]] || { echo 'Invalid health counter' >&2; exit 1; }
done
[[ "$load" =~ ^[0-9]+\.[0-9]+$ ]] || { echo 'Invalid load reading' >&2; exit 1; }
(( total > 0 && available <= total && disk_total > 0 && disk_used <= disk_total )) || exit 1
printf '{"signal":"health","cpu_temp_c":%s,"cpu_freq_mhz":%s,"throttled":%s,"under_voltage":%s,"load_1m":%s,"mem_used_bytes":%s,"mem_total_bytes":%s,"disk_used_bytes":%s,"disk_total_bytes":%s,"net_rx_bytes":%s,"net_tx_bytes":%s}\n' \
  "$(awk -v x="$temp" 'BEGIN {print x/1000}')" "$(awk -v x="$freq" 'BEGIN {print x/1000}')" \
  "$(( (flags & 4) != 0 ))" "$(( (flags & 1) != 0 ))" "$load" \
  "$(( (total-available)*1024 ))" "$(( total*1024 ))" "$disk_used" "$disk_total" "$rx" "$tx"
