#!/usr/bin/env bash
# livetest_c.sh -- one-shot entry point for the option-C JTAG live link.
#
# Creates the host venv if needed, ensures pyftdi is installed, then runs the
# JTAG stream reader (fpga/host/jtag_live.py). Extra args are passed through.
#
#   ./livetest_c.sh                      # default: read a few ramp windows
#   ./livetest_c.sh ramp --count 8
#   ./livetest_c.sh plot                 # live rolling plot
#   ./livetest_c.sh --url ftdi://ftdi:2232/1 plot
#
# Run from anywhere; paths are resolved relative to this script.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/fpga/host/.venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
	echo "[livetest_c] creating venv $VENV"
	python3 -m venv --system-site-packages "$VENV"
fi

if ! "$PY" -c "import pyftdi" >/dev/null 2>&1; then
	echo "[livetest_c] installing pyftdi"
	"$VENV/bin/pip" install --quiet --upgrade pip
	"$VENV/bin/pip" install --quiet pyftdi
fi

if [ "$#" -eq 0 ]; then
	set -- ramp --count 4
fi

exec "$PY" "$ROOT/fpga/host/jtag_live.py" "$@"
