# fpga — anchor ANC on the Real Digital Blackboard

FPGA prototype shell for the `anchor` active-noise-cancelling chip. This wraps
the TinyTapeout top (`tt_um_anchor`: SPI slave → MMIO registers → `anchor_top`
FxLMS datapath) in a board-level `top` for the Real Digital Blackboard
(Zynq **XC7Z007S-1CLG400**, 100 MHz PL clock).

The same sources are used two ways:

1. **Locally** with a Vivado install (any recent version).
2. **Remotely** via [`piserver_synch`](../../../program/piserver_synch) `psync`,
   which rsyncs this directory to a build server, runs Vivado there, pulls the
   `.bit`/reports back, and flashes the board.

## Layout

```
fpga/
  Makefile        non-project Vivado build (make / make clean)
  build.tcl       the Vivado script (read sources -> synth -> pnr -> bitstream)
  design.mk       TOP / PART / FLASH_ARGS (also read by `psync ... flash`)
  constr/top.xdc  Blackboard pin constraints (clock, LEDs, PmodA SPI)
  src/
    top.sv        Blackboard wrapper
    pkg/globals.sv  fixed-point formats & parameters (read first)
    anchor_top.sv anti_noise.sv error_block.sv s_hat_fir.sv sample_in.sv
    regfile.sv mmio_regs.sv serial_bridge.sv tt_um_anchor.sv
  build/          Vivado output (gitignored)
```

`src/*.sv` are **copies** of `../chip/rtl` (Vivado's non-project flow only reads
from this directory). Keep them in sync when the RTL changes.

## Build locally

Requires Vivado on `PATH` (or set `VIVADO=`):

```sh
make            # -> build/top.bit, build/*.rpt
make clean
```

Under the hood:

```sh
vivado -mode batch -nojournal -log vivado.log \
       -source build.tcl -tclargs top xc7z007sclg400-1
```

## Flash

```sh
openFPGALoader --cable ft2232 --fpga-part xc7z007sclg400 build/top.bit
```

This loads the bitstream into PL SRAM (volatile — lost on power cycle). Use
`psync fpgas flash <name>` to do the same thing through the sync tooling.

## PmodA SPI/MMIO host interface

`tt_um_anchor` is exposed on PmodA for an external host:

| PmodA pin | FPGA pin | signal      | dir  |
|-----------|----------|-------------|------|
| JA1_P     | F16      | `pmod_sclk` | in   |
| JA1_N     | F17      | `pmod_cs_n` | in   |
| JA2_P     | G19      | `pmod_sdi`  | in   |
| JA2_N     | G20      | `pmod_sdo`  | out  |
| JA3_P     | E18      | `pmod_irq`  | out  |

SPI mode 0, MSB-first, 24-bit frames: `rw | addr[6:0] | data[15:0]`. Keep
`sclk ≤ 25 MHz` (`serial_bridge` requires `sclk ≤ clk/4`). The register map is
documented at the top of `src/mmio_regs.sv`.

## Notes

- `globals.sv` must be read before the modules (they `import globals::*` in
  their port lists); `build.tcl` reads `src/pkg/*.sv` first for this reason.
- No PLL: the 100 MHz board clock runs the design directly.
- `btn`/`sw` are reserved for the on-chip bring-up/self-test FSM.
