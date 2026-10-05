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
  scripts/gen_selftest_vectors.py   regenerate the on-chip test vectors
  tb/             Verilator self-test testbench (tb/run.sh)
  src/
    top.sv        Blackboard wrapper (SPI/MMIO + self-test mux)
    self_test.sv  on-chip golden-vector self-test FSM
    pkg/globals.sv          fixed-point formats & parameters (read first)
    pkg/selftest_params.sv  generated: ANC_N, ANC_PRIME, X_LEN, Y_CHK
    mem/          generated ROMs: x.mem, e.mem, w.mem, shat.mem
    anchor_top.sv anti_noise.sv error_block.sv s_hat_fir.sv sample_in.sv
    regfile.sv mmio_regs.sv serial_bridge.sv tt_um_anchor.sv
  build/          Vivado output (gitignored)
```

SW0 selects what drives the single `anchor_top` instance:

| SW0 | mode                                                     |
|-----|----------------------------------------------------------|
| 0   | **on-chip self-test** (default) — pass/fail on LEDs      |
| 1   | **PmodA SPI/MMIO** host interface                        |

LEDs: `led[0]=pass`, `led[1]=done`, `led[2]=busy`, `led[3]=fail`,
`led[4]=SW0/mode`, `led[5]=heartbeat`; RGB LED: green=pass, red=fail,
blue=running.

## On-chip self-test

`self_test.sv` replays reduced golden vectors (default `ANC_N=1024`) through
`anchor_top`, in the same stage order as `chip/dv/anchor_top_tb.sv`, then
checks the final `w[]` and a 64-bit rotate-xor checksum of every `y[n]`.
`y[n]` is not stored — the checksum+weights are the pass criterion.

Regenerate the vectors after an RTL change (`make fmt`/lint, then):

```sh
.venv/bin/python fpga/scripts/gen_selftest_vectors.py     # ANC_N env overrides size
```

This rewrites `src/mem/*.mem` and `src/pkg/selftest_params.sv` (both committed
so a Vivado-only user can build without Python). The hardware recurrence is
`chk <= {chk[62:0], chk[63]} ^ y_n`.

Simulate the self-test before building:

```sh
fpga/tb/run.sh        # Verilator; prints PASS and writes tb/run/out/self_test_tb.vcd
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
`sclk ≤ 6.25 MHz` (`serial_bridge` requires `sclk ≤ clk/4`; the datapath runs
on a 25 MHz divided clock). The register map is documented at the top of
`src/mmio_regs.sv`.

## Notes

- `globals.sv` must be read before the modules (they `import globals::*` in
  their port lists); `build.tcl` reads `src/pkg/*.sv` first for this reason.
- `build.tcl` copies `src/mem/*.mem` into the run dir so the self-test ROMs'
  relative `$readmemh` paths resolve.
- Clock: the 100 MHz board clock is divided by 4 to 25 MHz (`clk_anc`) for the
  whole design — `error_block`'s NLMS update has a ~21 ns critical path, so
  100 MHz does not close timing. The divider output is a BUFG with a
  `create_generated_clock` constraint (see `constr/top.xdc`).
- One `anchor_top` instance is shared between the self-test and the PmodA SPI
  path, selected by SW0. `chip/rtl/tt_um_anchor.sv` is kept untouched for the
  TinyTapeout flow; `top.sv` repeats its SPI/MMIO wiring plus the mux.
