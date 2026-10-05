#!/usr/bin/env bash
# Build + run a Verilator testbench.
#
#   fpga/tb/run.sh selftest   # self_test + anchor_top (default)
#   fpga/tb/run.sh spi        # top in SPI mode, driven by a bit-banged host
#
# The vector ROMs are copied next to the run dir so the RTL's relative
# $readmemh("x.mem", ...) paths resolve (same convention as Vivado, where
# build.tcl stages src/mem/*.mem in the build directory).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RTL="$HERE/../src"
RUN="$HERE/run"
TARGET="${1:-selftest}"

case "$TARGET" in
  selftest)
    TB="self_test_tb.sv"
    SRC=(self_test.sv anchor_top.sv sample_in.sv anti_noise.sv s_hat_fir.sv error_block.sv regfile.sv)
    TRACE=(--trace --trace-structs)
    ;;
  spi)
    TB="spi_host_tb.sv"
    SRC=(top.sv self_test.sv serial_bridge.sv mmio_regs.sv anchor_top.sv
         sample_in.sv anti_noise.sv s_hat_fir.sv error_block.sv regfile.sv)
    TRACE=()
    ;;
  loop)
    TB="anc_loop_tb.sv"
    SRC=(anc_loop.sv plant_fir.sv anchor_top.sv sample_in.sv anti_noise.sv
         s_hat_fir.sv error_block.sv regfile.sv)
    TRACE=()
    ;;
  *)
    echo "usage: $0 [selftest|spi]" >&2
    exit 2
    ;;
esac

rm -rf "$RUN"
mkdir -p "$RUN/out"
cp "$RTL"/mem/*.mem "$RUN/"

verilator \
  --binary --timing --timescale 1ns/1ps \
  "${TRACE[@]}" \
  -Wno-fatal -Wno-WIDTH -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-DECLFILENAME \
  -I"$RTL/pkg" -I"$HERE" \
  --top-module "${TB%.sv}" -o tb_sim --Mdir "$RUN/obj_dir" \
  "$RTL/pkg/globals.sv" "$RTL/pkg/selftest_params.sv" \
  "${SRC[@]/#/$RTL/}" \
  "$HERE/$TB"

cd "$RUN" && ./obj_dir/tb_sim
