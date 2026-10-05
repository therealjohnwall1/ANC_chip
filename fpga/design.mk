# Per-design settings, read by the Makefile and by `psync fpgas flash`.
# Board: Real Digital Blackboard (Zynq XC7Z007S, 100 MHz PL clock).
TOP        = top
PART       = xc7z007sclg400-1
FLASH_ARGS = --cable ft2232 --fpga-part xc7z007sclg400
