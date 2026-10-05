#!/usr/bin/env bash
# livetest_c.sh -- one-shot entry point for the option-C JTAG live link.
#
# Creates the host venv if needed, ensures pyftdi is installed, then runs the
# JTAG stream reader (fpga/host/jtag_live.py). Extra args are passed through.
#
#   ./livetest_c.sh                      # default: print a few windows
#   ./livetest_c.sh raw --count 8
#   ./livetest_c.sh plot                 # live rolling waveform + spectrum
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
	set -- raw --count 4
fi

exec "$PY" "$ROOT/fpga/host/jtag_live.py" "$@"
