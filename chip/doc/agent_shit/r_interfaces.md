# Anchor -- Hardware Interfaces (draft dump)

Scratch dump of the parameters/signals/timing worked out in chat, derived from
`scripts/ans.py` (`lms_anc`), `chip/doc/overview.md`'s features table, and the
block layout in `chip/doc/diagrams/FullSystem.drawio.svg`. Not final -- copy
what's useful into the real `interfaces.md` / `theory_of_op.md` / `registers.md`
split and prune the rest.

Key structural point baked into all of this: the chip never computes `y_sec`.
In `ans.py`, `s_taps` (real secondary path) only exists because the simulation
has to model the physical world. In real hardware, `y[n]` goes out the DAC and
travels the *real* acoustic path for free -- it shows up already summed into
whatever the error-mic ADC hands you as `err_sample_i`. The chip only ever
needs `s_hat_taps` (the estimate) to build the filtered-reference for the LMS
update. So the datapath is two FIRs (`W` and `S_hat`) plus one update step,
not three.

## Parameters

| Param | Meaning | Proposed default | From |
|---|---|---|---|
| `FS` | sample rate | 16 kHz | overview.md |
| `SAMPLE_W` | ADC/DAC sample width | 16-bit signed, Q1.15 | standard audio PCM |
| `COEF_W` | stored coefficient width (`w_n`, `s_hat`) | 24-bit signed, Q1.23 | precision margin over samples -- LMS coefficients need more headroom than the samples they're built from, or they quantize-stall before converging |
| `N_W_MAX` | max taps, adaptive filter `W(z)` | 64 (parameterizable) | `taps` in `lms_anc` |
| `N_S_MAX` | max taps, `S_hat(z)` estimate | 16-32 | secondary paths are physically short (speaker -> error mic), matches `s_hat_taps` |
| `ACC_W` | MAC accumulator width | 48-bit | `SAMPLE_W + COEF_W + ceil(log2(N_W_MAX))` = 16+24+6 = 46, round to 48 |
| `LR_W` | step-size register | 16-bit unsigned, Q0.16 | `lr` in `lms_anc` |
| `EPS_W` | NLMS regularizer register | 16-bit unsigned | `epsil` in `lms_anc` |

These are recommendations, not facts pulled from a doc -- `ans.py` is float64,
so someone has to pick the fixed-point widths. Flag if different numbers are
wanted before this gets copied into a permanent doc.

## 1. Clock & Reset

| Signal | Dir | Width | Notes |
|---|---|---|---|
| `clk_core` | in | 1 | main datapath/FSM clock -- everything below runs here unless noted |
| `clk_mcu` | in | 1 | register-bus clock; **async** to `clk_core` per the CDC feature row |
| `rst_n` | in | 1 | async assert, sync de-assert; resynchronized locally into both `clk_core` and `clk_mcu` domains (two independent reset synchronizers, same source) |

## 2. Streaming sample interface (real-time path, hard deadline)

| Signal | Dir | Width | Notes |
|---|---|---|---|
| `ref_sample_i` | in | `SAMPLE_W` | `x[n]`, reference mic ADC |
| `err_sample_i` | in | `SAMPLE_W` | `e[n]` directly -- error mic ADC, already includes the real acoustic `S(z)` |
| `sample_valid_i` | in | 1 | pulse, one `clk_core` cycle after CDC, marks both sample inputs valid for this period |
| `sample_accepted_o` | out | 1 | pulse ack back to the ADC-side domain; used to detect **overrun** (new `sample_valid_i` before this pulses) |
| `anti_noise_o` | out | `SAMPLE_W` | `y[n]`, to Audio Mixer -> DAC |
| `output_valid_o` | out | 1 | pulse, `anti_noise_o` stable/ready to mix this period |
| `busy_o` | out | 1 | level, high while this sample's MAC sequence is running -- if still high at the next `sample_valid_i`, that's the deadline-miss condition |
| `adapt_enable_i` | in | 1 | gates the weight-update stage only (freeze coefficients, keep filtering) |
| `anc_enable_i` | in | 1 | gates `W(z)` filtering itself (bypass -> `anti_noise_o` forced to 0/mute) |

**Timing relationship:**

```
clk_core        _|-|_|-|_|-|_|-|_|-|_|-|_|-|_ ...
sample_valid_i   -|_______________________________  (1-cycle pulse, period = 1/FS = 62.5 us)
sample_accepted_o    -|___________________________   (asserts same or next cycle, ack)
busy_o           ____|-----------------|_________   (high for the MAC sequence)
output_valid_o   ____________________|-|___________   (pulses once busy_o drops)
```

Cycle budget per sample, serial single-MAC architecture (cheapest area, matches
tinytapeout constraints):

```
cycles/sample ~= N_W (compute y[n] = W . x_window)
              + N_S (compute x_filt[n] = S_hat . x_window)
              + N_W (weight update w_n+1[k] per tap)
              + overhead (rounding/saturation, ~5-10 cycles)
```

Example, `N_W=32, N_S=16`: `2*32+16+8 = 88` cycles. Deadline is
`T_s * f_core`. At `f_core = 4 MHz` -> 250 cycles available, ~35% utilized,
headroom left for NLMS's division/reciprocal step if `normalize` is enabled.
At `f_core ~= 1.5 MHz` you're already at the wall -- this is the number that
sets the minimum core clock for a given tap count, or the max tap count for a
given clock.

## 3. Coefficient window (indirect access)

Indirect addressing keeps the MMIO address space small instead of exposing
64+ individual 24-bit registers directly:

| Signal | Dir | Width | Notes |
|---|---|---|---|
| `coef_sel_i` | in | 1 | 0 = `W[]` (adaptive filter), 1 = `S_hat[]` (secondary-path estimate) |
| `coef_idx_i` | in | `ceil(log2(N_W_MAX))` | tap index; auto-increments on each access |
| `coef_data_i` | in | `COEF_W` | write data |
| `coef_data_o` | out | `COEF_W` | readback data |
| `coef_wr_i` | in | 1 | pulse, write-strobe into the indexed slot |
| `coef_rd_i` | in | 1 | pulse, read-strobe, `coef_data_o` valid next cycle |
| `coef_bank_i` | in | 1 | active/shadow bank select |
| `coef_commit_i` | in | 1 | pulse -- atomically swaps shadow->active, gated to idle/sample-boundary only |
| `shat_valid_o` | out | 1 | sticky, set once a full valid `S_hat[]` has been loaded/committed -- `W(z)` filtering can be gated on this at bring-up |

`W[]` is chip-written (adaptation) but MCU-readable for debug; `S_hat[]` is
MCU-written (loaded from an offline characterization or an on-chip calibration
state) and chip-readable. Both share the same indirect port, discriminated by
`coef_sel_i`.

## 4. MCU command/status register interface

Generic synchronous bus (APB-lite shape) rather than full AXI:

| Signal | Dir | Width | Notes |
|---|---|---|---|
| `psel_i` | in | 1 | select |
| `penable_i` | in | 1 | enable phase (2-cycle APB-style access) |
| `pwrite_i` | in | 1 | 1=write |
| `paddr_i` | in | 8 | 256-byte address space |
| `pwdata_i` | in | 16 | matches `SAMPLE_W`; coefficient window uses its own wider indirect path instead of forcing 24-bit onto this bus |
| `prdata_o` | out | 16 | |
| `pready_o` | out | 1 | wait-state support |
| `irq_o` | out | 1 | OR of enabled status flags |

Register bit fields (content, not full bit-map -- that's the register-map doc):
- **CMD**: `RESET, INIT, PRIME, ADAPT_EN, FREEZE, CALIBRATE, MUTE, COMMIT`
- **STATUS/IRQ** (sticky, W1C): `OVERRUN, UNDERRUN, DEADLINE_MISS, CLIP, OVERFLOW, CALIB_FAIL, PROTO_ERR, ILLEGAL_STATE`

## 5. Test/observability interface

| Signal | Dir | Width | Notes |
|---|---|---|---|
| `test_mode_i` | in | 2 | 0=normal, 1=impulse inject, 2=constant inject, 3=PRBS inject |
| `test_data_i` | in | `SAMPLE_W` | payload for constant-injection mode |
| `loopback_en_i` | in | 1 | digital loopback, `ref_sample_i`->`anti_noise_o` path bypassing the adaptive filter, for datapath-only bring-up |
| `dbg_acc_o` | out | `ACC_W` | accumulator snapshot (last MAC sequence), read-only |
| `dbg_err_power_o` | out | 32 | running error-power estimate |

## Open decisions still unlocked

1. **Pin count.** Everything above is the *logical* core-side interface.
   TinyTapeout gives very few physical pins, so this parallel bus almost
   certainly gets serialized (SPI-like shim) onto the actual package -- lock
   the logical interface and the FSM/datapath first, decide the physical
   serialization protocol last.
2. **NLMS divide cost.** `normalize=True` in `ans.py` divides by
   `x_filt_power + epsil` every sample -- a real divider or reciprocal-LUT
   costs extra cycles/area beyond the MAC budget above; worth deciding
   whether NLMS ships at all in v1 or stays LMS-only initially.
3. **Coefficient bus width mismatch** (`COEF_W=24` vs `pwdata_i=16`) means
   coefficient loads go through the indirect window's own wider port, not the
   generic register bus -- confirm that split is wanted rather than packing
   24 bits over two 16-bit register writes.

## Doc-split note (per OpenTitan convention, verified against aon_timer)

- `theory_of_operation.html` (OpenTitan): block diagram + design details,
  references signals by *name* in prose only, no signal table.
- `interfaces.html` (OpenTitan): separate page, "Inter-Module Signals" table
  (name/type/direction/width/description) + Interrupts + Security
  Alerts/Countermeasures.
- `registers.html`: auto-generated bit-field layout.

So: `theory_of_op.md` should only *name* signals inline where needed to
explain FSM/datapath behavior (e.g. "adaptation is gated by
`adapt_enable_i`"), the full tables in this file belong in a real
`interfaces.md`, and bit-field/address-offset detail belongs in
`registers.md`.
