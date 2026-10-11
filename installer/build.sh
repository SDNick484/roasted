#!/bin/sh
# Build dist/setup-roasted.sh: the installer header + this app as a base64 tarball after __PAYLOAD_BELOW__.
set -eu
cd "$(dirname "$0")/.."
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p dist "$STAGE/roasted"
cp -r app.py README.md ops static tests "$STAGE/roasted/"
find "$STAGE" \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} +
OUT=dist/setup-roasted.sh
{
  cat installer/setup-head.sh
  echo "__PAYLOAD_BELOW__"
  tar --owner=0 --group=0 -C "$STAGE" -czf - roasted | base64
} > "$OUT"
chmod +x "$OUT"
sh -n "$OUT"
echo "Built $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
