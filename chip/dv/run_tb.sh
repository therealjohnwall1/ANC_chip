#!/usr/bin/env bash
# Build and run a DV testbench, then report the waveform path.
#
#   ./chip/dv/run_tb.sh sample_in            # build + run sample_in_tb
#   ./chip/dv/run_tb.sh                       # default sample_in
#
# Runnable from any cwd. Todo: add more testbench targets to the TBS map.

set -uo pipefail

DV_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$DV_DIR/../.." && pwd)"
RTL_DIR="$REPO_ROOT/chip/rtl"
DV_PKG_DIR="$DV_DIR/pkg"

# testbench name -> (tb source, vcd name)
declare -A TBS=(
  [sample_in]="sample_in_tb.sv|sample_in_tb.vcd"
)

SET="${1:-sample_in}"

entry="${TBS[$SET]:-}"
if [ -z "$entry" ]; then
    echo "unknown testbench '$SET'" >&2
    echo "valid: ${!TBS[*]}" >&2
    exit 1
fi

# ${entry} is "src|vcd"; split on the separator.
TB_SRC="${entry%%|*}"
VCD_NAME="${entry##*|}"

OUT_DIR="$DV_DIR/out"
mkdir -p "$OUT_DIR"

# pkg sources first (packages must be elaborated before imports), then RTL, then TB
SOURCES=(
  "$RTL_DIR/pkg/globals.sv"
  "$DV_PKG_DIR/dv_globals.sv"
  "$RTL_DIR/sample_in.sv"
  "$DV_DIR/$TB_SRC"
)

build() {
    ( cd "$DV_DIR" && \
      rm -rf obj_dir && \
      verilator \
        --binary \
        --timing \
        --timescale 1ns/1ps \
        -I"$RTL_DIR/pkg" \
        -I"$DV_PKG_DIR" \
        -o tb_sim \
        --trace \
        --trace-structs \
        "${SOURCES[@]}" )
}

if ! build; then
    echo "build failed" >&2
    exit 1
fi

if ! ( cd "$DV_DIR" && ./obj_dir/tb_sim ); then
    echo "simulation failed" >&2
    exit 1
fi

echo ""
echo "waveform: $OUT_DIR/$VCD_NAME"
echo "  gtkwave $OUT_DIR/$VCD_NAME"
