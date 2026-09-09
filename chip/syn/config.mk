# anchor_top -- OpenROAD-flow-scripts design config
#
# Driven from chip/syn/work via orfs.sh (podman wrapper). Paths here are
# container paths: the repo is mounted at /work might need to do diff install steps
#
# Reference: ~/opt/OpenROAD-flow-scripts/flow/designs/sky130hd/gcd/config.mk

export DESIGN_NAME = anchor_top
export PLATFORM    = sky130hd

# globals.sv is a package and must be elaborated first, before any module
# that does `import globals::*`.
export VERILOG_FILES = \
  /work/chip/rtl/pkg/globals.sv \
  /work/chip/rtl/sample_in.sv \
  /work/chip/rtl/regfile.sv \
  /work/chip/rtl/anti_noise.sv \
  /work/chip/rtl/s_hat_fir.sv \
  /work/chip/rtl/error_block.sv \
  /work/chip/rtl/anchor_top.sv

export SDC_FILE = /work/chip/syn/constraint.sdc

export SYNTH_HDL_FRONTEND = slang

# loose ik chill
export CORE_UTILIZATION = 50
export TNS_END_PERCENT = 100
