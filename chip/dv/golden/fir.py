"""
Layer 2: the two FIR passes, and the window offsets that separate them.

mac.py does the arithmetic and knows nothing about indexing. This layer is
where the indexing lives, because the two filters do NOT read the tap line the
same way (golden/README.md section 3):

    x_f[n] = sum(k=0..N_S-1) s_hat[k] * x[n-k]      <-- INCLUDES x[n]
    y[n]   = sum(k=0..N_W-1) w[k]     * x[n-1-k]    <-- EXCLUDES x[n]

y[n] is one sample behind because lms_anc slices `x[i-taps:i]`, a half-open
range that stops one short of i. x_f[n] keeps the current sample because
np.convolve at index n runs k=0..N_S-1 over x[n-k]. Getting that backwards
costs one sample of alignment and is indistinguishable from a quantization
effect once the whole model is running -- which is why it is isolated here,
where a unit test can catch it, instead of inlined at the call site.

Tap line convention: `x_line[k] = x[n-k]`, index 0 is the newest sample.

Both filters also have an OUTPUT REGISTER, and neither of them is Q1.15:

    x_f  the accumulator is 64 bits, but the filtered line is 33 deep and
         nobody builds 33 x 64-bit registers. It gets rounded down to a real
         register on the way in. Measured peak is 1.29 (row G), so that
         register needs an integer bit -- Q1.x saturates.

    y    goes out the DAC as anti_noise_o, specified as SAMPLE_W. Measured
         peak is 1.13, so at Q1.15 it clips on peaks. Modelled as a clip with
         a flag rather than an exception, because that is what a converter
         does, and r_interfaces.md already has a CLIP status bit for it.

Run from the repo root:  python -m golden.fir
"""

from dataclasses import dataclass
from typing import Sequence

from fxpmath import Fxp

from golden import fmt as F
from golden import mac as M


# ---------------------------------------------------------------------------
# Tap line
# ---------------------------------------------------------------------------

def required_depth(n_w: int, n_s: int) -> int:
    """
    How deep the raw tap line has to be to feed both filters.

    W reads indices 1..n_w, S_hat reads 0..n_s-1, so the deepest index touched
    is max(n_w, n_s - 1) and the line needs one more entry than that. Not
    visible in ans.py at all, because the precomputed convolve hides it.
    """
    return max(n_w + 1, n_s)


def w_window(x_line: Sequence, n_w: int) -> list:
    """The samples y[n] is built from: x[n-1] .. x[n-N_W], oldest last."""
    assert len(x_line) >= n_w + 1, (
        f"W window needs {n_w + 1} entries of tap line, got {len(x_line)}")
    return [x_line[k + 1] for k in range(n_w)]


def shat_window(x_line: Sequence, n_s: int) -> list:
    """The samples x_f[n] is built from: x[n] .. x[n-N_S+1], oldest last."""
    assert len(x_line) >= n_s, (
        f"S_hat window needs {n_s} entries of tap line, got {len(x_line)}")
    return [x_line[k] for k in range(n_s)]


def update_window(xf_line: Sequence, n_w: int) -> list:
    """
    The filtered samples the weight update is built from: x_f[n-1] .. x_f[n-N_W].

    Same offset as `w_window`, different signal. Lives here rather than in
    update.py so that all three windows sit side by side and the one that is
    different is obvious.
    """
    assert len(xf_line) >= n_w + 1, (
        f"update window needs {n_w + 1} entries of filtered line, got {len(xf_line)}")
    return [xf_line[k + 1] for k in range(n_w)]


# ---------------------------------------------------------------------------
# FIR passes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FirOutput:
    """A FIR pass: the MAC sequence, plus the register its result lands in."""
    seq: M.FirResult
    out: Fxp                 # accumulator narrowed into the output register
    clipped: bool            # did that narrowing saturate?

    @property
    def value(self) -> float:
        """The stored result -- what the next stage actually sees."""
        return F.as_float(self.out)

    @property
    def acc_value(self) -> float:
        """The full-width accumulator, before it was narrowed."""
        return F.as_float(self.seq.acc)

    @property
    def rounding_loss(self) -> float:
        """How much the output register threw away."""
        return abs(self.acc_value - self.value)

    def trace(self) -> str:
        return self.seq.trace()


def filter_shat(s_hat: Sequence, x_line: Sequence, fmt: F.Fmt = F.DEFAULT) -> FirOutput:
    """
    S_hat(z) pass: x_f[n] = sum s_hat[k] * x[n-k], INCLUDING x[n].

    Result is rounded into the filtered-reference register (fmt.xf), which is
    what the tap line stores and what the update stage later multiplies by.
    Saturation here raises -- see fmt.to_xf for why clipping the gradient's
    multiplicand is worse than clipping an output.
    """
    win = shat_window(x_line, len(s_hat))
    seq = M.fir_sequence(s_hat, win, fmt, where="S_hat")
    out = F.to_xf(seq.acc, fmt)
    return FirOutput(seq=seq, out=out, clipped=False)


def filter_w(w: Sequence, x_line: Sequence, fmt: F.Fmt = F.DEFAULT) -> FirOutput:
    """
    W(z) pass: y[n] = sum w[k] * x[n-1-k], EXCLUDING x[n].

    Result is rounded into the DAC register (fmt.dac). Saturation is allowed
    and reported as `clipped` rather than raised, because a converter clips.
    """
    win = w_window(x_line, len(w))
    seq = M.fir_sequence(w, win, fmt, where="W")
    out = F.to_dac(seq.acc, fmt)
    return FirOutput(seq=seq, out=out, clipped=F.saturated(out))


if __name__ == "__main__":
    import numpy as np

    N_W, N_S = 4, 3
    fmt = F.DEFAULT

    # A tap line with each entry individually identifiable, so the window a
    # filter reads can be read straight off the output.
    marks = [0.10, 0.20, 0.30, 0.40, 0.50]     # x[n], x[n-1], ... x[n-4]
    x_line = [F.to_sample(v) for v in marks]

    print("=" * 78)
    print("Window offsets -- which sample does each filter reach for?")
    print("=" * 78)
    print(f"  tap line   " + "  ".join(f"x[n-{k}]={v:.2f}" for k, v in enumerate(marks)))
    print(f"  depth needed for N_W={N_W}, N_S={N_S}: {required_depth(N_W, N_S)}")
    print()

    # A unit impulse for a coefficient vector picks out exactly one tap.
    for pos in range(N_S):
        s_hat = [1.0 if k == pos else 0.0 for k in range(N_S)]
        got = filter_shat(s_hat, x_line, fmt).value
        print(f"  s_hat[{pos}]=1  ->  x_f[n] = {got:.4f}   = x[n-{pos}]")
    print()
    for pos in range(N_W):
        w = [1.0 if k == pos else 0.0 for k in range(N_W)]
        got = filter_w(w, x_line, fmt).value
        print(f"  w[{pos}]=1      ->  y[n]   = {got:.4f}   = x[n-{pos + 1}]")
    print()
    print("  s_hat[0] reaches the NEWEST sample, w[0] reaches one BEHIND it.")
    print("  That one-sample gap is the whole of section 3 of the README.")

    print()
    print("=" * 78)
    print("Output registers -- what the measured peaks do to them")
    print("=" * 78)
    print(f"  {'signal':>6} {'register':>10} {'peak (measured)':>16} {'fits?':>22}")
    for name, q, peak, row in (("x_f", fmt.xf, 1.2937, "G"), ("x_f", fmt.xf, 0.9966, "I"),
                               ("y", fmt.dac, 1.1330, "G"), ("y", fmt.dac, 1.0696, "I")):
        fits = "yes" if peak <= q.upper else f"NO -- clips at {q.upper:.4f}"
        print(f"  {name:>6} {str(q):>10} {peak:>13.4f} ({row}) {fits:>22}")
    print()
    print(f"  x_f at Q1.15 would saturate on row G, and sits {(1.0 - 0.9966) * 100:.1f}% "
          f"under the rail on row I.")
    print(f"  Cost of the integer bit: resolution {F.Q(1,15).resolution:.2e} -> "
          f"{fmt.xf.resolution:.2e}.")

    print()
    print("=" * 78)
    print("A real pass, traced")
    print("=" * 78)
    rng = np.random.default_rng(2)
    s_hat = rng.uniform(-0.5, 0.5, N_S)
    line = [F.to_sample(v) for v in rng.uniform(-0.9, 0.9, required_depth(N_W, N_S))]
    r = filter_shat(s_hat, line, fmt)
    print(r.trace())
    print(f"\n  accumulator  {r.acc_value:.12f}  {F.hexs(r.seq.acc)}")
    print(f"  x_f register {r.value:.12f}  {F.hexs(r.out)}   ({fmt.xf})")
    print(f"  lost to the output register: {r.rounding_loss:.3e} "
          f"(half an LSB is {fmt.xf.resolution / 2:.3e})")
