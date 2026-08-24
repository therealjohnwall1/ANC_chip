"""
Step 6: one sample propagated through every layer, and the checks that keep the
model honest.

Two jobs.

The ARTIFACT: a single sample walked through all six stages with every MAC
cycle, every partial sum, and every per-tap update printed in decimal and hex.
That is the thing you diff against a waveform when the RTL disagrees -- not
"y[n] is wrong" but "y[n] is wrong from cycle 9 of the W pass onward".

The CHECKS: `float_ref_step` is an independent transcription of the same
sample step in plain float64 -- it imports nothing from golden and calls none
of its layers, so agreeing with it means two implementations built from the
README's equations agree, not that one implementation agrees with itself. Run
golden at WIDE against it and any disagreement is a structural bug in the
layer split; run golden at DEFAULT against it and the difference IS the
fixed-point error, which is the number the whole exercise exists to produce.

Everything here is a single sample or a short chain. There is no closed loop,
no plant, no attenuation measurement -- those sit on top of this and are
deliberately not built yet.

Run from the repo root:  PYTHONPATH=.:chip/dv .venv/bin/python -m golden.demo
"""

from fractions import Fraction
import sys

import numpy as np

from golden import fmt as F
from golden import mac as M
from golden import fir as FIR
from golden import update as U
from golden import core as C


LR, EPS = 0.1, 0.00001


# ---------------------------------------------------------------------------
# The independent reference
# ---------------------------------------------------------------------------

def float_ref_step(w, s_hat, x_line, xf_line, x, e, lr=LR, eps=EPS):
    """
    One sample step in plain float64, transcribed straight from
    golden/README.md section 3. Imports nothing from golden on purpose.

    Returns a dict of every intermediate, so the comparison can be per stage
    rather than only on the final weights.
    """
    n_w, n_s = len(w), len(s_hat)

    x_line = [x] + list(x_line[:-1])                              # 1. push x[n]
    x_f = sum(s_hat[k] * x_line[k] for k in range(n_s))           # 2. INCLUDES x[n]
    xf_line = [x_f] + list(xf_line[:-1])
    y = sum(w[k] * x_line[k + 1] for k in range(n_w))             # 3. EXCLUDES x[n]

    win = [xf_line[k + 1] for k in range(n_w)]                    # W's offset, x_f
    energy = sum(v * v for v in win)                              # 4.
    mu = lr / (energy + eps)                                      # 5.
    w_new = [w[k] + mu * e * win[k] for k in range(n_w)]          # 6.

    return {"x_f": x_f, "y": y, "energy": energy, "mu": mu,
            "w": w_new, "x_line": x_line, "xf_line": xf_line}


def float_ref_chain(w, s_hat, x_line, xf_line, pairs, lr=LR, eps=EPS):
    """float_ref_step run over several (x, e) pairs."""
    out = []
    for x, e in pairs:
        r = float_ref_step(w, s_hat, x_line, xf_line, x, e, lr, eps)
        w, x_line, xf_line = r["w"], r["x_line"], r["xf_line"]
        out.append(r)
    return out


def float_ref_prime(s_hat, x_line, xf_line, x_samples):
    """Stages 1-2 only, matching core.prime."""
    n_s = len(s_hat)
    for x in x_samples:
        x_line = [x] + list(x_line[:-1])
        xf_line = [sum(s_hat[k] * x_line[k] for k in range(n_s))] + list(xf_line[:-1])
    return x_line, xf_line


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

def scenario(n_w=6, n_s=4, seed=41):
    """A small, fixed, hand-inspectable setup. Deterministic across runs."""
    rng = np.random.default_rng(seed)
    return {
        "n_w": n_w, "n_s": n_s,
        "s_hat": rng.uniform(-0.5, 0.5, n_s),
        "w0": rng.uniform(-0.3, 0.3, n_w),
        "x": rng.uniform(-0.8, 0.8, n_w + 4),
        "e": np.array([0.05, 0.03, 0.01, -0.02]),
    }


def build(sc, fmt):
    """A primed ChipState and the matching primed float reference."""
    st = C.ChipState.init(sc["s_hat"], sc["n_w"], LR, EPS, fmt, w=sc["w0"])
    st = C.prime(st, sc["x"][:sc["n_w"]])

    depth = FIR.required_depth(sc["n_w"], sc["n_s"])
    fx_line, fxf_line = float_ref_prime(list(sc["s_hat"]), [0.0] * depth,
                                        [0.0] * (sc["n_w"] + 1),
                                        sc["x"][:sc["n_w"]])
    return st, (list(sc["w0"]), list(sc["s_hat"]), fx_line, fxf_line)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name, value, limit, fmt="{:.2e}"):
        ok = value <= limit
        self.rows.append((name, fmt.format(value), fmt.format(limit), ok))
        return ok

    def note(self, name, text, ok=True):
        self.rows.append((name, text, "", ok))

    def report(self):
        w = max(len(r[0]) for r in self.rows)
        print(f"  {'check':<{w}}  {'measured':>12}  {'limit':>10}")
        for name, val, lim, ok in self.rows:
            print(f"  {name:<{w}}  {val:>12}  {lim:>10}   {'ok' if ok else 'FAIL'}")
        return all(r[3] for r in self.rows)


def check_layer_primitives(ck):
    """Layers 0-1: the multiplier is exact and the MAC matches float64."""
    f = F.DEFAULT
    rng = np.random.default_rng(3)
    a, b = F.to_sample(0.3, f), F.to_coef(-0.7, f)
    p = F.mul(a, b)
    ck.add("L0 mul exactness", abs(F.as_float(p) - F.as_float(a) * F.as_float(b)), 0.0)

    worst = 0.0
    for n in (8, 32, 64):
        c = [F.to_coef(v) for v in rng.uniform(-0.8, 0.8, n)]
        s = [F.to_sample(v) for v in rng.uniform(-0.9, 0.9, n)]
        ref = float(np.dot([F.as_float(v) for v in c], [F.as_float(v) for v in s]))
        worst = max(worst, abs(M.fir_sequence(c, s, f).value - ref))
    ck.add("L1 MAC vs float64 dot", worst, 0.0)

    # tap order is free only while the accumulator carries the full fraction
    c = [F.to_coef(v) for v in rng.uniform(-0.8, 0.8, 32)]
    s = [F.to_sample(v) for v in rng.uniform(-0.9, 0.9, 32)]
    ck.add("L1 tap order invariance",
           abs(M.fir_sequence(c, s, f).value - M.fir_sequence(c[::-1], s[::-1], f).value), 0.0)


def check_windows(ck):
    """Layer 2: the offsets reproduce ans.py's own expressions."""
    W = F.WIDE
    rng = np.random.default_rng(17)
    n_w, n_s, n = 32, 12, 200
    w_f, s_f = rng.uniform(-0.8, 0.8, n_w), rng.uniform(-0.5, 0.5, n_s)
    x = rng.uniform(-0.9, 0.9, n)

    xf_ref = np.convolve(x, s_f)[:n]                                # ans.py's x_filt
    y_ref = [w_f @ x[i - n_w:i][::-1] for i in range(n_w, n)]       # ans.py's y_hat

    w_q = [F.to(v, W.coef, W) for v in w_f]
    s_q = [F.to(v, W.coef, W) for v in s_f]
    line = [F.to(0.0, W.sample, W)] * FIR.required_depth(n_w, n_s)
    d_xf = d_y = 0.0
    for i in range(n):
        line = [F.to(x[i], W.sample, W)] + line[:-1]
        d_xf = max(d_xf, abs(FIR.filter_shat(s_q, line, W).value - xf_ref[i]))
        if i >= n_w:
            d_y = max(d_y, abs(FIR.filter_w(w_q, line, W).value - y_ref[i - n_w]))
    ck.add("L2 x_f vs np.convolve", d_xf, 1e-14)
    ck.add("L2 y vs w @ x[i-taps:i][::-1]", d_y, 1e-14)

    # the offset has to be load-bearing, or the check above proves nothing
    line = [F.to(0.0, W.sample, W)] * FIR.required_depth(n_w, n_s)
    for i in range(n_w + 1):
        line = [F.to(x[i], W.sample, W)] + line[:-1]
        shifted = M.fir_sequence(s_q, [line[k + 1] for k in range(n_s)], W).value
    shift_err = abs(shifted - xf_ref[n_w])
    ck.note("L2 shifted window is caught", f"{shift_err:.1e} err", shift_err > 1e-3)


def check_divider(ck):
    """Layer 3: the divide is a correctly-rounded rational quotient, exactly."""
    f = F.DEFAULT
    rng = np.random.default_rng(31)
    worst = 0
    for _ in range(20):
        xf = [F.to_xf(v) for v in rng.uniform(-0.9, 0.9, 32)]
        st = U.step_size(U.energy(xf, f).acc, LR, EPS, f)
        lr_q = F.to(LR, f.lr, f)
        exact = (Fraction(F.raw(lr_q), 1 << f.lr.frac_bits)
                 / Fraction(F.raw(st.divisor), 1 << f.acc.frac_bits)
                 * (1 << f.step.frac_bits))
        want = int(exact + Fraction(1, 2)) if exact >= 0 else -int(-exact + Fraction(1, 2))
        worst = max(worst, abs(F.raw(st.mu) - want))
    ck.add("L3 divider vs exact rational", float(worst), 0.0, "{:.0f}")


def check_wide_vs_float(ck, sc):
    """Layer 4 at WIDE: golden must be the float reference, stage for stage."""
    st, (w, s_hat, fx, fxf) = build(sc, F.WIDE)
    pairs = list(zip(sc["x"][sc["n_w"]:], sc["e"]))
    _, traces = C.chain(st, pairs)
    refs = float_ref_chain(w, s_hat, fx, fxf, pairs)

    d_xf = max(abs(t.xf.value - r["x_f"]) for t, r in zip(traces, refs))
    d_y = max(abs(t.y_value - r["y"]) for t, r in zip(traces, refs))
    d_e = max(abs(t.energy.value - r["energy"]) for t, r in zip(traces, refs))
    d_mu = max(abs(t.step.value - r["mu"]) for t, r in zip(traces, refs))
    d_w = max(max(abs(F.as_float(a) - b) for a, b in zip(t.state_out.w, r["w"]))
              for t, r in zip(traces, refs))
    ck.add("L4 WIDE x_f vs float ref", d_xf, 1e-14)
    ck.add("L4 WIDE y   vs float ref", d_y, 1e-14)
    ck.add("L4 WIDE E   vs float ref", d_e, 1e-14)
    ck.add("L4 WIDE mu  vs float ref", d_mu, 1e-14)
    ck.add("L4 WIDE w   vs float ref", d_w, 1e-14)


def check_against_ans(ck, n_w=6, n_s=4, n=120, seed=77):
    """
    The whole chip against scripts/ans.py, sample for sample.

    Golden is driven with the e[n] that ans.py's plant produced -- the chip
    takes e as an input, so the plant stays on the testbench side.
    """
    from scripts.ans import lms_anc
    W = F.WIDE
    rng = np.random.default_rng(seed)
    x, d = rng.uniform(-0.7, 0.7, n), rng.uniform(-0.7, 0.7, n)
    s_taps = rng.uniform(-0.4, 0.4, n_s + 2)
    s_hat = s_taps[:n_s]

    _, _, y_ref, e_ref, w_ref = lms_anc(x, d, n_w, LR, s_taps=s_taps,
                                        s_hat_taps=s_hat, normalize=True,
                                        keep_history=True)
    st = C.prime(C.ChipState.init(s_hat, n_w, LR, EPS, W, w=[0.0] * n_w), x[:n_w])
    _, traces = C.chain(st, [(x[n_w + j], e_ref[j]) for j in range(len(y_ref))])

    ck.add(f"L4 y vs lms_anc ({len(traces)} samples)",
           max(abs(t.y_value - r) for t, r in zip(traces, y_ref)), 1e-13)
    ck.add("L4 w vs lms_anc, every step",
           max(max(abs(F.as_float(a) - b) for a, b in zip(t.state_out.w, ref))
               for t, ref in zip(traces, w_ref[1:])), 1e-13)


def check_hazards(ck, sc):
    """Properties that are easy to break and invisible in a single sample."""
    st, _ = build(sc, F.WIDE)
    before = [F.raw(v) for v in st.w]
    t = C.sample_step(st, sc["x"][sc["n_w"]], sc["e"][0])
    ck.note("L4 state_in not mutated",
            "yes" if [F.raw(v) for v in st.w] == before else "MUTATED",
            [F.raw(v) for v in st.w] == before)

    # y[n] must use the weights held on entry, not the ones written this sample
    line = [F.as_float(v) for v in t.state_out.x_line]
    y_old = float(np.dot([F.as_float(v) for v in st.w], line[1:sc["n_w"] + 1]))
    y_new = float(np.dot([F.as_float(v) for v in t.state_out.w], line[1:sc["n_w"] + 1]))
    ck.add("L4 y uses OLD weights", abs(t.y_value - y_old), 1e-13)
    ck.note("L4 (an overlapped schedule would give)", f"{y_new:+.7f}")

    # A tap line short by one sample. Out of reset w == 0, so y[n] = w . window
    # is zero whatever the window holds -- the priming bug is genuinely
    # invisible on the first sample and only surfaces once the weights the
    # first (wrong) update wrote start feeding back into y. That is the case
    # worth testing, because w == 0 is where the real chip starts.
    pairs = list(zip(sc["x"][sc["n_w"]:], sc["e"]))
    zero_w = [0.0] * sc["n_w"]
    short = C.prime(C.ChipState.init(sc["s_hat"], sc["n_w"], LR, EPS, F.WIDE,
                                     w=zero_w), sc["x"][:sc["n_w"] - 1])
    full = C.prime(C.ChipState.init(sc["s_hat"], sc["n_w"], LR, EPS, F.WIDE,
                                    w=zero_w), sc["x"][:sc["n_w"]])
    errs = [abs(a.y_value - b.y_value)
            for a, b in zip(C.chain(short, pairs)[1], C.chain(full, pairs)[1])]
    ck.note("L4 under-primed from reset: step 0", f"{errs[0]:.1e} hidden", errs[0] == 0.0)
    ck.note("L4 under-primed from reset: step 1", f"{errs[1]:.1e} exposed", errs[1] > 0.0)

    # With weights already loaded there is no hiding period at all.
    short_w = C.prime(C.ChipState.init(sc["s_hat"], sc["n_w"], LR, EPS, F.WIDE,
                                       w=sc["w0"]), sc["x"][:sc["n_w"] - 1])
    e0 = abs(C.chain(short_w, pairs)[1][0].y_value
             - C.chain(build(sc, F.WIDE)[0], pairs)[1][0].y_value)
    ck.note("L4 under-primed with w loaded: step 0", f"{e0:.1e} exposed", e0 > 0.0)


# ---------------------------------------------------------------------------
# The artifact
# ---------------------------------------------------------------------------

def trace_one(sc, fmt=F.DEFAULT):
    st, _ = build(sc, fmt)
    print(f"  state: N_W={sc['n_w']}, N_S={sc['n_s']}, {fmt.coef} coefficients, "
          f"{fmt.acc} accumulator")
    print(f"  tap line ({len(st.x_line)} deep): "
          + " ".join(f"{F.as_float(v):+.4f}" for v in st.x_line))
    print(f"  filtered ({len(st.xf_line)} deep): "
          + " ".join(f"{F.as_float(v):+.4f}" for v in st.xf_line))
    print()
    t = C.sample_step(st, sc["x"][sc["n_w"]], sc["e"][0])
    print(t.report())
    print(f"\n  flags {t.flags}")
    return t


def quantization_cost(sc):
    """golden at DEFAULT against the float reference: the fixed-point error itself."""
    st, (w, s_hat, fx, fxf) = build(sc, F.DEFAULT)
    pairs = list(zip(sc["x"][sc["n_w"]:], sc["e"]))
    _, traces = C.chain(st, pairs)
    refs = float_ref_chain(w, s_hat, fx, fxf, pairs)

    print(f"  {'sample':>7} {'|d x_f|':>11} {'|d y|':>11} {'|d E|':>11} "
          f"{'|d mu|':>11} {'max |d w|':>11} {'stalls':>7}")
    for i, (t, r) in enumerate(zip(traces, refs)):
        dw = max(abs(F.as_float(a) - b) for a, b in zip(t.state_out.w, r["w"]))
        print(f"  {i:>7} {abs(t.xf.value - r['x_f']):>11.3e} "
              f"{abs(t.y_value - r['y']):>11.3e} {abs(t.energy.value - r['energy']):>11.3e} "
              f"{abs(t.step.value - r['mu']):>11.3e} {dw:>11.3e} "
              f"{t.stalls:>3}/{sc['n_w']:<3}")
    f = F.DEFAULT
    print()
    print(f"  for scale: half an LSB is {f.coef.resolution / 2:.2e} on a "
          f"{f.coef} coefficient, {f.xf.resolution / 2:.2e} on {f.xf} x_f")


if __name__ == "__main__":
    sc = scenario()

    print("=" * 96)
    print("One sample, every stage, every cycle")
    print("=" * 96)
    trace_one(sc)

    print()
    print("=" * 96)
    print("Three more samples chained -- Q1.15 against the float64 reference")
    print("=" * 96)
    quantization_cost(sc)

    print()
    print("=" * 96)
    print("Checks")
    print("=" * 96)
    ck = Checks()
    check_layer_primitives(ck)
    check_windows(ck)
    check_divider(ck)
    check_wide_vs_float(ck, sc)
    check_against_ans(ck)
    check_hazards(ck, sc)
    ok = ck.report()
    print()
    if ok:
        print("  all layers agree with an independent float64 transcription "
              "and with scripts/ans.py")
    else:
        sys.exit("  CHECKS FAILED")
