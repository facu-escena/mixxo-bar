#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
IP="$(ipconfig getifaddr en0 || true)"
if [ -z "$IP" ]; then
  IP="127.0.0.1"
fi
URL="http://${IP}:8787/"
SERIAL="${ADB_SERIAL:-192.168.0.243:5555}"

python3 "$ROOT/server.py" &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf "http://127.0.0.1:8787/api/content" >/dev/null; then
    break
  fi
  sleep 0.2
done

if adb -s "$SERIAL" get-state >/dev/null 2>&1; then
  TMP="$(mktemp)"
  printf '%s\n' "$URL" > "$TMP"
  adb -s "$SERIAL" push "$TMP" /sdcard/Android/data/com.cordiez.mixxo.bar/files/server.txt >/dev/null
  rm -f "$TMP"
  adb -s "$SERIAL" shell am force-stop com.cordiez.mixxo.bar
  adb -s "$SERIAL" shell am start -n com.cordiez.mixxo.bar/.MainActivity >/dev/null
fi

wait "$pid"
