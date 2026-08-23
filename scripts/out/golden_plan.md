# golden/ build plan

Fixed-point reference model for the FxLMS datapath, one layer per RTL block,
so each stage can be diffed against a waveform on its own.

Scope as agreed: **a single sample's propagation through the datapath**.
No closed loop, no plant model, no attenuation measurement, no DV vector dump.
Layers 0-5 are what those would sit on top of, so nothing gets thrown away
when they are wanted later.

Decisions already locked:

| decision | choice |
|---|---|
| `COEF_W` | Q1.15 (16-bit), over the 24-bit draft |
| NLMS divide | exact fixed-point divide, no LUT/Newton model |
| trace depth | one sample, chainable to three; no full lifecycle |

---

## Step 0 - pin down the reference semantics  [DONE]

**Added:** `golden/README.md` (conventions), `scripts/verify_convention.py`.

**Purpose:** `ans.py` precomputes `x_filt = np.convolve(x, s_hat)` over the
whole signal before its loop starts. Hardware cannot -- it has to build
`x_f[n]` from a tap line, one sample at a time. That restatement has to be
exact before any code is built on it, because once the fixed-point layers land
an off-by-one and a quantization effect are indistinguishable from the
outside: both read as "golden disagrees with ans.py by a little".

What it established:

- `x_f[n] = sum(k=0..N_S-1) s_hat[k] * x[n-k]` -- **includes** `x[n]`
- `y[n]   = sum(k=0..N_W-1) w[k]     * x[n-1-k]` -- **excludes** `x[n]`
- the update window follows the `W` offset, on the `x_f` signal
- tap line depths: `x_line` needs `max(N_W+1, N_S)`, `xf_line` needs `N_W+1`
- stage order: W FIR reads `w[]` before the update writes it (a RAW hazard for
  any overlapped MAC schedule)

Verified, not assumed: `python -m scripts.verify_convention` diffs a streaming
implementation against `lms_anc` across six configurations (both optimizers,
`S_hat` shorter and longer than `W`, configs I and G). Worst disagreement
3.6e-15 -- float64 reassociation, not an algorithmic difference.

## Step 1 - `golden/fmt.py`

**Adds:** a `Fmt` dataclass (sample / coef / acc / energy / step formats,
rounding mode, overflow policy) and the arithmetic primitives: `to_sample`,
`to_coef`, `mul`, `acc_add`, `round_to_coef`, `assert_no_saturation`.

**Purpose:** every quantization decision lives in one file. Changing `COEF_W`
from Q1.15 to Q1.23 to see what it buys is one field, not five files. It is
also where the fxpmath `overflow=` / `rounding=` settings get bound, so those
RTL choices are visible in one place instead of scattered across call sites.

**Surfaces an open decision.** `SAMPLE_W`, `COEF_W`, `ACC_W` have doc-assigned
widths. The NLMS intermediates -- energy accumulator, reciprocal, `mu` -- do
not. `eps = 1e-5` in `ans.py`, and `E[n]` reaches ~50 at `N_W=32` (from the
partial-sum peaks in `range_check.py`), so energy needs ~6 integer bits. In a
16-bit register that leaves 10 fractional bits, LSB ~1e-3 -- at which point
`eps` quantizes to zero and the regularizer is gone. Exposed as config, not
picked silently.

## Step 2 - `golden/mac.py`

**Adds:** `mac_step(acc, coef, sample, fmt) -> (acc, record)` and
`fir_sequence(coefs, window, fmt) -> (result, partials)`.

**Purpose:** the one hardware block reused for `W(z)`, `S_hat(z)`, and the
energy sum. `fir_sequence` returns every partial sum, not just the final dot
product, because `range_check.py` established that the quantity which must not
overflow is `max|cumsum|`, not `max|dot|`. A mid-sequence saturation then
reports the exact tap index instead of showing up as a wrong `y[n]`.

## Step 3 - `golden/fir.py`

**Adds:** `filter_w(w, x_line, fmt)` -> `y[n]`, `filter_shat(s_hat, x_line,
fmt)` -> `x_f[n]`. Thin wrappers over `mac.fir_sequence` applying the step 0
window offsets.

**Purpose:** keeps the two FIR passes independently checkable. Same MAC
hardware, but different tap counts, different coefficient sources (`W` is
chip-written, `S_hat` is MCU-written), and different window offsets. Holding
the offset logic here and out of `mac.py` means an off-by-one fails a `fir.py`
test rather than corrupting the MAC.

## Step 4 - `golden/update.py`

**Adds:** `energy(x_f_window, fmt)`, `step_size(energy, lr, eps, fmt)` (exact
divide), `update_taps(w, x_f_window, err, step, fmt) -> (w_new, records)`.

**Purpose:** the adaptation stage, and where Q1.15 coefficients are most
likely to bite. `range_check.py` puts the quantize-stall floor near 12
fractional bits, so Q1.15 leaves ~3 bits of margin. `update_taps` returns
per-tap records showing which updates rounded to zero -- the stall condition
made directly observable instead of inferred from an attenuation curve.

**Surfaces a second decision.** `mu * e[n] * x_f[k]` is a three-way product,
and fixed-point multiplication is not associative once there is rounding
between stages. `(e * x_f) * mu` and `(mu * e) * x_f` give different answers
and different intermediate widths. Made an explicit named choice rather than
an accident of typing order.

## Step 5 - `golden/core.py`

**Adds:** a `ChipState` dataclass (raw tap line, filtered line, `w[]`,
`s_hat[]`, formats) and `sample_step(state, x_sample, e_sample) -> (y,
new_state, trace)`.

**Purpose:** one `sample_valid_i` pulse -- the FSM's whole job for one sample,
in the order the RTL will execute it. `e_sample` is an **input**; the chip
never sees `d[n]` or `y_s(n)`.

## Step 6 - `golden/demo.py`

**Adds:** a small hand-built state, a `float_ref()` doing the same step in
float64, a wide-format equivalence check, and a printed trace of one sample
(chainable to three).

**Purpose:** two jobs. At a wide format (Q1.40, big accumulator) golden must
match `float_ref` to ~1e-9 -- proof the layer split introduced no structural
bug, independent of any quantization question. And the printed trace is the
artifact actually diffed against a waveform: every partial sum, the energy,
`mu`, per-tap before/after. Three chained samples catch tap-line shift bugs
that a single step cannot show.

## Step 7 - `golden/README.md`

**Adds:** conventions (done in step 0), plus the function -> RTL signal map
and which formats are doc-backed vs still open.

**Purpose:** so the mapping from `mac_step` to a specific cycle in the
waveform viewer is not reconstructed from memory later.

## Step 8 - dependency + doc pointers

**Adds:** `fxpmath` to `requirements.txt`, pointer lines in `scripts/README.md`.

**Purpose:** `fxpmath` is not installed yet (0.4.10, pure-Python wheel, numpy
is the only transitive dep and is already present), so this has to happen
before anything runs.

---

## Not in scope

Closed loop, `plant.py`, attenuation measurement, CSV/hex vector dump for a
cocotb testbench, end-to-end `ans.py` comparison. Deferred, not cancelled.
