#!/usr/bin/env bash
# Build + run the on-chip self-test testbench with Verilator.
#
#   fpga/tb/run.sh
#
# The vector ROMs are copied next to the run dir so the RTL's relative
# $readmemh("x.mem", ...) paths resolve (same convention as Vivado, where
# build.tcl stages src/mem/*.mem in the build directory).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RTL="$HERE/../src"
RUN="$HERE/run"

rm -rf "$RUN"
mkdir -p "$RUN/out"
cp "$RTL"/mem/*.mem "$RUN/"

verilator \
  --binary --timing --timescale 1ns/1ps \
  --trace --trace-structs \
  -Wno-fatal -Wno-WIDTH -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-DECLFILENAME \
  -I"$RTL/pkg" -I"$HERE" \
  --top-module self_test_tb -o tb_sim --Mdir "$RUN/obj_dir" \
  "$RTL/pkg/globals.sv" "$RTL/pkg/selftest_params.sv" \
  "$RTL/self_test.sv" \
  "$RTL/anchor_top.sv" "$RTL/sample_in.sv" "$RTL/anti_noise.sv" \
  "$RTL/s_hat_fir.sv" "$RTL/error_block.sv" "$RTL/regfile.sv" \
  "$HERE/self_test_tb.sv"

cd "$RUN" && ./obj_dir/tb_sim
