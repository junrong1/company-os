#!/usr/bin/env bash
# Serve this folder over HTTP. Opening company-os.html directly with file:// also
# works — this is only here for when you want a real origin (or to view it from
# another device on the network).
#
#   ./serve.sh          → http://localhost:8791/company-os.html
#   ./serve.sh 9000     → a different port
#
# Ctrl-C to stop. Note the server dies with the terminal that started it.
set -euo pipefail

PORT="${1:-8791}"
cd "$(dirname "$0")"

if lsof -i ":$PORT" >/dev/null 2>&1; then
  echo "Port $PORT is already in use. Pass a different one: ./serve.sh 9000" >&2
  exit 1
fi

echo "Serving $(pwd)"
echo "  → http://localhost:$PORT/company-os.html"
exec python3 -m http.server "$PORT"
