#!/bin/sh
# Run manually during the documented load scenario; Ctrl+C stops sampling.
set -eu
while true; do
  date -u '+%Y-%m-%dT%H:%M:%SZ'
  awk '/MemAvailable|SwapFree|SwapTotal/ {print}' /proc/meminfo
  cat /proc/loadavg
  ps -eo pid,comm,rss,%cpu --sort=-rss | head -n 12
  sleep 5
done
