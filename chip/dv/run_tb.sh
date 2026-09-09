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

# testbench name -> (tb source, vcd name, rtl source list, colon-separated)
declare -A TBS=(
  [sample_in]="sample_in_tb.sv|sample_in_tb.vcd|sample_in.sv"
  [anti_noise]="anti_noise_tb.sv|anti_noise_tb.vcd|anti_noise_est.sv"
  [regfile]="regfile_tb.sv|regfile_tb.vcd|regfile.sv"
  [error_block]="error_block_tb.sv|error_block_tb.vcd|error_block.sv"
  [s_hat_fir]="s_hat_fir_tb.sv|s_hat_fir_tb.vcd|regfile.sv:s_hat_fir.sv"
  [anc]="anchor_top_tb.sv|anchor_top_tb.vcd|regfile.sv:sample_in.sv:anti_noise_est.sv:error_block.sv:s_hat_fir.sv:anchor_top.sv"
  [mmio]="mmio_regs_tb.sv|mmio_regs_tb.vcd|mmio_regs.sv"
  [serial]="serial_bridge_tb.sv|serial_bridge_tb.vcd|serial_bridge.sv"
  [tt_um]="tt_um_anchor_tb.sv|tt_um_anchor_tb.vcd|regfile.sv:sample_in.sv:anti_noise_est.sv:error_block.sv:s_hat_fir.sv:anchor_top.sv:mmio_regs.sv:serial_bridge.sv:tt_um_anchor.sv"
)

SET="${1:-sample_in}"

entry="${TBS[$SET]:-}"
if [ -z "$entry" ]; then
    echo "unknown testbench '$SET'" >&2
    echo "valid: ${!TBS[*]}" >&2
    exit 1
fi

# ${entry} is "src|vcd|rtl[:rtl...]"; split on the separator.
TB_SRC="${entry%%|*}"
rest="${entry#*|}"
VCD_NAME="${rest%%|*}"
RTL_LIST="${rest#*|}"

OUT_DIR="$DV_DIR/out"
mkdir -p "$OUT_DIR"

# pkg sources first (packages must be elaborated before imports), then RTL, then TB
SOURCES=(
  "$RTL_DIR/pkg/globals.sv"
  "$DV_PKG_DIR/dv_globals.sv"
)
IFS=':'; for r in $RTL_LIST; do SOURCES+=("$RTL_DIR/$r"); done; unset IFS
SOURCES+=("$DV_DIR/$TB_SRC")

build() {
    ( cd "$DV_DIR" && \
      rm -rf obj_dir && \
      verilator \
        --binary \
        --timing \
        --timescale 1ns/1ps \
        -I"$RTL_DIR/pkg" \
        -I"$DV_PKG_DIR" \
        -I"$DV_DIR" \
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
