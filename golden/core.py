"""
Layer 4: one sample. The FSM's entire job for a single `sample_valid_i` pulse.

Everything below this file is arithmetic; this is scheduling and state. It
holds the two tap lines, the weights, and the loaded S_hat, and it runs the six
stages in the order golden/README.md section 4 fixes them:

    1. push x[n] into the raw tap line
    2. S_hat FIR   -> x_f[n],  push into the filtered line
    3. W FIR       -> y[n]     using the OLD w[]        <- output
    4. energy      -> E[n]
    5. step size   -> mu[n]
    6. update      -> w[k]                              <- visible next sample

Stage 3 reading w[] before stage 6 writes it is a genuine read-before-write
hazard for any schedule that overlaps them. Serialized as written here it
cannot bite; it is recorded because the first thing anyone does to this
datapath is overlap the update with the next sample's FIR.

The chip never computes y_s(n). `e_sample` is an INPUT -- it arrives from the
error-mic ADC with the real acoustic S(z) already applied, which is why there
is no plant anywhere in this file. `d[n]`, `y_s(n)`, `S(z)` and `P(z)` belong
to the testbench.

State is immutable: sample_step returns a new ChipState rather than mutating
one. Comparing "the weights before" against "the weights after" is most of
what a per-sample diff against RTL consists of, so both have to still exist.

Run from the repo root:  python -m golden.core
"""

from dataclasses import dataclass, replace
from typing import Sequence

from fxpmath import Fxp

from golden import fmt as F
from golden import fir as FIR
from golden import mac as M
from golden import update as U


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChipState:
    """
    Everything the chip remembers between samples.

    w        adaptive coefficients, chip-written, MCU-readable for debug
    s_hat    secondary-path estimate, MCU-written, never adapted
    x_line   raw tap line,      x_line[k]  = x[n-k]
    xf_line  filtered line,     xf_line[k] = x_f[n-k]

    Both lines are one entry deeper than the window that reads them, because
    the entry written this sample is not read until the next one. For xf_line
    that extra word is avoidable by pushing x_f after the update instead of
    before -- a scheduling choice, noted in README section 5, not taken here.
    """
    w: tuple
    s_hat: tuple
    x_line: tuple
    xf_line: tuple
    lr: Fxp
    eps: Fxp
    fmt: F.Fmt = F.DEFAULT
    anc_enable: bool = True      # anc_enable_i  -- gates W(z) filtering
    adapt_enable: bool = True    # adapt_enable_i -- gates the update stage

    @property
    def n_w(self) -> int:
        return len(self.w)

    @property
    def n_s(self) -> int:
        return len(self.s_hat)

    @classmethod
    def init(cls, s_hat: Sequence, n_w: int, lr: float, eps: float,
             fmt: F.Fmt = F.DEFAULT, w: Sequence = None, **kw) -> "ChipState":
        """
        Post-reset state: coefficients cleared, tap lines cleared, S_hat loaded.

        Matches the INIT/PRIME split in theory_of_op.md -- S_hat comes from the
        MCU (or non-volatile storage), w starts at zero, and the tap lines are
        empty until `prime` walks enough samples in to fill them.
        """
        depth = FIR.required_depth(n_w, len(s_hat))
        zero_s = F.to_sample(0.0, fmt)
        zero_xf = F.to_xf(0.0, fmt)
        return cls(
            w=tuple(F.to_coef(v, fmt) for v in (w if w is not None else [0.0] * n_w)),
            s_hat=tuple(F.to_coef(v, fmt) for v in s_hat),
            x_line=tuple([zero_s] * depth),
            xf_line=tuple([zero_xf] * (n_w + 1)),
            lr=F.to(lr, fmt.lr, fmt, where="lr"),
            eps=F.to(eps, fmt.eps, fmt, where="eps"),
            fmt=fmt, **kw)

    def check(self) -> None:
        assert len(self.x_line) >= FIR.required_depth(self.n_w, self.n_s), (
            f"raw tap line {len(self.x_line)} deep, needs "
            f"{FIR.required_depth(self.n_w, self.n_s)}")
        assert len(self.xf_line) >= self.n_w + 1, (
            f"filtered line {len(self.xf_line)} deep, needs {self.n_w + 1}")


# ---------------------------------------------------------------------------
# One sample
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SampleTrace:
    """Everything one sample did, at every layer, for diffing against a waveform."""
    x: Fxp
    e: Fxp
    xf: FIR.FirOutput
    y: FIR.FirOutput
    energy: M.FirResult
    step: U.StepResult
    taps: tuple                 # tuple[U.TapUpdate], empty if adaptation is gated
    state_in: ChipState
    state_out: ChipState

    @property
    def y_value(self) -> float:
        return F.as_float(self.y.out)

    @property
    def stalls(self) -> int:
        return U.stall_count(self.taps)

    @property
    def flags(self) -> dict:
        """The status bits r_interfaces.md would latch for this sample."""
        return {
            "clip": self.y.clipped,
            "mu_saturated": self.step.saturated,
            "coef_railed": any(t.railed for t in self.taps),
            "stalled_taps": self.stalls,
        }

    def report(self, *, taps: bool = True) -> str:
        f = self.state_in.fmt
        out = [
            f"  in    x[n] = {F.as_float(self.x):>12.8f} {F.hexs(self.x):>8}"
            f"    e[n] = {F.as_float(self.e):>12.8f} {F.hexs(self.e):>8}",
            "",
            f"  1-2   S_hat FIR ({self.state_in.n_s} taps)",
            self.xf.trace(),
            f"        x_f[n] = {self.xf.value:>12.8f} {F.hexs(self.xf.out):>8}  ({f.xf})"
            f"   acc {self.xf.acc_value:.10f}",
            "",
            f"  3     W FIR ({self.state_in.n_w} taps), old weights",
            self.y.trace(),
            f"        y[n]   = {self.y.value:>12.8f} {F.hexs(self.y.out):>8}  ({f.dac})"
            f"   acc {self.y.acc_value:.10f}"
            + ("   CLIPPED" if self.y.clipped else ""),
            "",
            f"  4     E[n]  = {self.energy.value:>12.8f}  {F.hexs(self.energy.acc)}  ({f.acc})",
            f"  5     mu[n] = {self.step.value:>12.8f}  {F.hexs(self.step.mu)}  ({f.step})"
            + ("   SATURATED" if self.step.saturated else ""),
            "",
        ]
        if not self.taps:
            out.append("  6     update gated (adapt_enable=0), weights held")
        elif taps:
            out.append(f"  6     update, {self.stalls}/{self.state_in.n_w} taps stalled")
            out.append(U.trace(self.taps))
        else:
            out.append(f"  6     update, {self.stalls}/{self.state_in.n_w} taps stalled")
        return "\n".join(x for x in out if x is not None)


def sample_step(state: ChipState, x_sample, e_sample) -> SampleTrace:
    """
    One sample_valid_i pulse.

    Args:
        state: the chip as it stands entering this sample
        x_sample: x[n], reference mic (ref_sample_i)
        e_sample: e[n], error mic (err_sample_i) -- already carries real S(z)

    Returns a SampleTrace holding y[n], the new state, and every intermediate.
    """
    state.check()
    fmt = state.fmt

    x = x_sample if isinstance(x_sample, Fxp) else F.to_sample(x_sample, fmt, where="x[n]")
    e = e_sample if isinstance(e_sample, Fxp) else F.to_sample(e_sample, fmt, where="e[n]")

    # 1. push x[n]
    x_line = (x,) + state.x_line[:-1]

    # 2. S_hat FIR -> x_f[n], push
    xf = FIR.filter_shat(state.s_hat, x_line, fmt)
    xf_line = (xf.out,) + state.xf_line[:-1]

    # 3. W FIR -> y[n], with the weights held on entry
    y = FIR.filter_w(state.w, x_line, fmt)
    if not state.anc_enable:
        y = replace(y, out=F.to_dac(0.0, fmt), clipped=False)

    # 4-5. energy and step size, over the update window
    win = FIR.update_window(xf_line, state.n_w)
    e_acc = U.energy(win, fmt)
    step = U.step_size(e_acc.acc, state.lr, state.eps, fmt)

    # 6. update, unless adaptation is gated
    if state.adapt_enable:
        w_new, taps = U.update_taps(state.w, win, e, step.mu, fmt)
        w_new = tuple(w_new)
    else:
        w_new, taps = state.w, ()

    out_state = replace(state, w=w_new, x_line=x_line, xf_line=xf_line)
    return SampleTrace(x=x, e=e, xf=xf, y=y, energy=e_acc, step=step, taps=taps,
                       state_in=state, state_out=out_state)


def prime(state: ChipState, x_samples: Sequence) -> ChipState:
    """
    Walk samples into the tap lines without producing output or adapting.

    This is the PRIME state theory_of_op.md describes as "filling the MAC to
    allow for rolling multiplications on the first iteration", and the reason
    lms_anc starts its loop at i = taps rather than 0: until the line is full
    there is no valid window to emit from.

    Only stages 1 and 2 run -- x_f has to be built during priming too, or the
    filtered line is still empty when the first real sample arrives.
    """
    fmt = state.fmt
    for v in x_samples:
        x = v if isinstance(v, Fxp) else F.to_sample(v, fmt, where="prime x")
        x_line = (x,) + state.x_line[:-1]
        xf = FIR.filter_shat(state.s_hat, x_line, fmt)
        state = replace(state, x_line=x_line, xf_line=(xf.out,) + state.xf_line[:-1])
    return state


def chain(state: ChipState, pairs: Sequence) -> tuple:
    """
    Run several samples back to back. Returns (final_state, [SampleTrace]).

    `pairs` is a sequence of (x[n], e[n]). Chaining is what catches tap-line
    shift bugs -- a state that is wrong by one sample looks perfectly fine for
    exactly one step.
    """
    traces = []
    for x, e in pairs:
        t = sample_step(state, x, e)
        traces.append(t)
        state = t.state_out
    return state, tuple(traces)


if __name__ == "__main__":
    import numpy as np

    N_W, N_S = 4, 3
    LR, EPS = 0.1, 0.00001
    fmt = F.DEFAULT
    rng = np.random.default_rng(41)

    s_hat = rng.uniform(-0.5, 0.5, N_S)
    st = ChipState.init(s_hat, N_W, LR, EPS, fmt,
                        w=rng.uniform(-0.3, 0.3, N_W))

    print("=" * 96)
    print(f"ChipState: N_W={N_W}, N_S={N_S}, {fmt.coef} coefficients")
    print("=" * 96)
    print(f"  raw tap line depth  {len(st.x_line):>3}   = max(N_W+1, N_S)")
    print(f"  filtered line depth {len(st.xf_line):>3}   = N_W+1")
    print(f"  s_hat  " + "  ".join(f"{F.as_float(v):+.5f}" for v in st.s_hat))
    print(f"  w      " + "  ".join(f"{F.as_float(v):+.5f}" for v in st.w))

    x = rng.uniform(-0.8, 0.8, N_W + 3)
    st = prime(st, x[:N_W + 1])
    print(f"\n  primed with {N_W + 1} samples; tap line now "
          + " ".join(f"{F.as_float(v):+.4f}" for v in st.x_line))

    print()
    print("=" * 96)
    print("One sample, every stage")
    print("=" * 96)
    t = sample_step(st, x[N_W + 1], 0.05)
    print(t.report())
    print(f"\n  flags {t.flags}")

    print()
    print("=" * 96)
    print("Three samples chained -- weights carry, tap lines shift")
    print("=" * 96)
    st2 = ChipState.init(s_hat, N_W, LR, EPS, fmt, w=[0.0] * N_W)
    st2 = prime(st2, x[:N_W + 1])
    _, traces = chain(st2, [(x[N_W + 1], 0.05), (x[N_W + 2], 0.03), (0.4, 0.01)])
    print(f"  {'n':>2}  {'x[n]':>10} {'e[n]':>8} {'x_f[n]':>11} {'y[n]':>11} "
          f"{'E[n]':>10} {'mu[n]':>10}  w after")
    for i, tr in enumerate(traces):
        ws = " ".join(f"{F.as_float(v):+.5f}" for v in tr.state_out.w)
        print(f"  {i:>2}  {F.as_float(tr.x):>10.6f} {F.as_float(tr.e):>8.4f} "
              f"{tr.xf.value:>11.7f} {tr.y_value:>11.7f} {tr.energy.value:>10.6f} "
              f"{tr.step.value:>10.6f}  {ws}")
    print()
    print("  y[n] is zero on the first sample only because w started at zero;")
    print("  it is nonzero from sample 1 on, which is the update from sample 0 arriving.")

    print()
    print("=" * 96)
    print("The two enables")
    print("=" * 96)
    for anc, adapt in ((True, True), (False, True), (True, False)):
        s = replace(st, anc_enable=anc, adapt_enable=adapt)
        tr = sample_step(s, 0.4, 0.05)
        moved = any(F.raw(a) != F.raw(b) for a, b in zip(s.w, tr.state_out.w))
        print(f"  anc_enable={str(anc):<5} adapt_enable={str(adapt):<5} -> "
              f"y[n]={tr.y_value:+.7f}  weights moved: {moved}")
    print()
    print("  Note anc_enable=0 mutes y[n] but adaptation still runs, on an e[n] that")
    print("  no longer contains any anti-noise. Whether that is wanted at bring-up is a")
    print("  real decision -- r_interfaces.md gates the two independently, so the")
    print("  combination is reachable.")
