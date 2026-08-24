"""
Layer 3: the adaptation stage -- energy, the divide, and the weight update.

This is where Q1.15 coefficients get tested. scripts/range_check.py puts the
adaptation stall near 12 fractional bits, so Q1.15 leaves roughly three bits of
margin, and "roughly three bits" is not a comfortable number. `update_taps`
reports, per tap, whether the update actually moved the register or rounded
straight back onto the value already there -- the stall observed directly
rather than inferred from an attenuation curve that came out lower than
expected.

Three pieces, in the order the FSM runs them:

    energy(xf_window)              E[n] = sum x_f[n-1-k]^2
    step_size(E, lr, eps)          mu[n] = lr / (E[n] + eps)
    update_taps(w, xf, e, mu)      w[k] += mu[n] * e[n] * x_f[n-1-k]

The energy sum reuses the MAC from layer 1 with the same array on both
operands, and reads the SAME window the update reads (fir.update_window) -- so
in hardware it is not a second memory pass, it is the same tap walk with the
multiplier fed differently.

Run from the repo root:  python -m golden.update
"""

from dataclasses import dataclass
from typing import Sequence

from fxpmath import Fxp

from golden import fmt as F
from golden import mac as M


# Where the rounding goes in the three-way product mu * e * x_f. With exact
# intermediates the two orders are identical -- integer arithmetic is
# associative -- so this only becomes a real choice once the intermediate is
# narrowed to a register somebody has to pay for. See `tap_delta`.
ORDER_MU_ERR = "(mu*e)*x_f"
ORDER_ERR_XF = "(e*x_f)*mu"


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------

def energy(xf_window: Sequence, fmt: F.Fmt = F.DEFAULT) -> M.FirResult:
    """
    E[n] = sum over the update window of x_f^2.

    Same MAC, both operands the same array. Returns the full FirResult so the
    partial sums are available: E is monotonically increasing (every term is a
    square), so unlike the FIR passes its peak partial IS its final value --
    which makes it the one accumulator whose sizing can be reasoned about from
    the tap count alone.
    """
    return M.fir_sequence(xf_window, xf_window, fmt, where="energy")


# ---------------------------------------------------------------------------
# Step size
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StepResult:
    mu: Fxp
    divisor: Fxp          # E[n] + eps, in accumulator format
    saturated: bool       # did mu need more integer bits than fmt.step has?
    quotient_raw: int     # the divider's exact output, before saturation

    @property
    def value(self) -> float:
        return F.as_float(self.mu)


def step_size(e_acc: Fxp, lr, eps, fmt: F.Fmt = F.DEFAULT) -> StepResult:
    """
    mu[n] = lr / (E[n] + eps), as an exact fixed-point divide.

    Done in raw integers, which is what a divider actually operates on, and
    keeps the 64-bit accumulator out of float64 (whose 53-bit significand
    would quietly drop its low bits).

        mu_raw = round( lr_raw * 2^(acc_frac + step_frac - lr_frac) / div_raw )

    Rounding is half-away-from-zero on the quotient, matching the default
    coefficient rounding rather than a truncating divider -- one more thing the
    RTL has to match deliberately.

    Two failure modes it will not paper over:

      * div_raw == 0. Only reachable if eps quantized to zero in the
        accumulator format AND the reference is silent. That is the failure
        mode fmt.py's "open decision 1" is about, so it raises with that name
        attached rather than returning an infinity.

      * mu needs more integer bits than fmt.step has. Saturating here does not
        crash anything, it just silently caps the step size, so it is reported
        rather than raised -- but it means adaptation is running slower than
        the configured lr asked for.
    """
    if not isinstance(lr, Fxp):
        lr = F.to(lr, fmt.lr, fmt, where="lr")
    if not isinstance(eps, Fxp):
        eps = F.to(eps, fmt.eps, fmt, where="eps")

    divisor = F.acc_add(e_acc, eps, fmt, where="E + eps")
    div_raw = F.raw(divisor)

    if div_raw == 0:
        raise ZeroDivisionError(
            f"E + eps == 0 in {fmt.acc}: eps={F.as_float(eps):g} quantized away "
            f"and the reference is silent. Widen the energy register or raise eps "
            f"(fmt.py, open decision 1).")
    assert div_raw > 0, "energy cannot be negative -- every term is a square"

    shift = fmt.acc.frac_bits + fmt.step.frac_bits - fmt.lr.frac_bits
    assert shift >= 0, f"step format too coarse for an exact divide (shift {shift})"

    num = F.raw(lr) << shift
    q, r = divmod(abs(num), div_raw)
    if 2 * r >= div_raw:                      # round half away from zero
        q += 1
    q_raw = q if num >= 0 else -q

    fits = F.raw_fits(q_raw, fmt.step)
    mu = F.from_raw(q_raw, fmt.step, fmt, where="mu", allow_saturation=True)
    return StepResult(mu=mu, divisor=divisor, saturated=not fits, quotient_raw=q_raw)


# ---------------------------------------------------------------------------
# Weight update
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TapUpdate:
    """One tap's update, and what the coefficient register did with it."""
    k: int
    w_in: Fxp
    w_out: Fxp
    xf: Fxp
    delta: Fxp            # the update before the coefficient register saw it
    stalled: bool         # delta was nonzero but the register did not move
    railed: bool          # the write saturated

    @property
    def moved(self) -> float:
        return F.as_float(self.w_out) - F.as_float(self.w_in)

    def row(self) -> str:
        flag = "STALL" if self.stalled else ("RAIL" if self.railed else "")
        return (f"  {self.k:>3}  {F.as_float(self.w_in):>12.8f} {F.hexs(self.w_in):>8}"
                f"  {F.as_float(self.xf):>11.7f}  {F.as_float(self.delta):>13.3e}"
                f"  {F.as_float(self.w_out):>12.8f} {F.hexs(self.w_out):>8}  {flag}")


TAP_HEADER = (f"  {'k':>3}  {'w in':>12} {'hex':>8}  {'x_f':>11}  {'delta':>13}"
              f"  {'w out':>12} {'hex':>8}")


def tap_delta(mu: Fxp, err: Fxp, xf_k: Fxp, fmt: F.Fmt = F.DEFAULT, *,
              order: str = ORDER_MU_ERR, inter_q: F.Q = None) -> Fxp:
    """
    One tap's update term, mu * e[n] * x_f[k].

    `inter_q` is the register the first product lands in. Leave it None and
    every intermediate is exact, at which point BOTH orders give bit-identical
    results and the `order` argument does nothing -- exact integer
    multiplication is associative.

    Give it a width and the two orders diverge, because they are rounding
    different quantities:

        ORDER_MU_ERR   rounds mu*e     -- a number spanning mu's 134 dB range
        ORDER_ERR_XF   rounds e*x_f    -- a number bounded by the signal, ~2

    which is the actual content of the "which order" question: not a
    preference, but where in the chain the wide dynamic range gets truncated.
    """
    if order == ORDER_MU_ERR:
        inter = F.mul(mu, err)
        if inter_q is not None:
            inter = F.to(inter, inter_q, fmt, where="mu*e", allow_saturation=True)
        return F.mul(inter, xf_k)

    if order == ORDER_ERR_XF:
        inter = F.mul(err, xf_k)
        if inter_q is not None:
            inter = F.to(inter, inter_q, fmt, where="e*x_f", allow_saturation=True)
        return F.mul(inter, mu)

    raise ValueError(f"unknown product order {order!r}")


def update_taps(w: Sequence, xf_window: Sequence, err, mu: Fxp,
                fmt: F.Fmt = F.DEFAULT, *, order: str = ORDER_MU_ERR,
                inter_q: F.Q = None) -> tuple:
    """
    w[k] += mu[n] * e[n] * x_f[n-1-k], for every tap.

    The add and the round into COEF_W happen together, in one write: the
    coefficient register is the only place precision is deliberately thrown
    away, and `stalled` marks the taps where the update was smaller than half
    an LSB and the write therefore changed nothing.

    Returns (w_out, records).
    """
    assert len(w) == len(xf_window), (
        f"{len(w)} weights against {len(xf_window)} filtered samples")
    if not isinstance(err, Fxp):
        err = F.to_sample(err, fmt, where="e[n]")

    w_out, records = [], []
    for k, (w_k, xf_k) in enumerate(zip(w, xf_window)):
        if not isinstance(w_k, Fxp):
            w_k = F.to_coef(w_k, fmt, where=f"w[{k}]")

        delta = tap_delta(mu, err, xf_k, fmt, order=order, inter_q=inter_q)
        new = F.to_coef(w_k + delta, fmt, where=f"w[{k}] write")

        stalled = F.raw(new) == F.raw(w_k) and F.raw(delta) != 0
        records.append(TapUpdate(k=k, w_in=w_k, w_out=new, xf=xf_k, delta=delta,
                                 stalled=stalled, railed=F.saturated(new)))
        w_out.append(new)

    return w_out, tuple(records)


def stall_count(records: Sequence) -> int:
    return sum(1 for r in records if r.stalled)


def trace(records: Sequence) -> str:
    return "\n".join([TAP_HEADER] + [r.row() for r in records])


if __name__ == "__main__":
    import numpy as np
    from dataclasses import replace

    LR, EPS = 0.1, 0.00001
    N_W = 8
    fmt = F.DEFAULT
    rng = np.random.default_rng(21)

    xf = [F.to_xf(v) for v in rng.uniform(-0.6, 0.6, N_W)]
    w = [F.to_coef(v) for v in rng.uniform(-0.5, 0.5, N_W)]

    print("=" * 92)
    print("Energy, and the step size it produces")
    print("=" * 92)
    e_res = energy(xf, fmt)
    st = step_size(e_res.acc, LR, EPS, fmt)
    print(f"  E[n]      {e_res.value:.10f}   {F.hexs(e_res.acc)}   ({fmt.acc})")
    print(f"  E + eps   {F.as_float(st.divisor):.10f}")
    print(f"  mu[n]     {st.value:.10f}   {F.hexs(st.mu)}   ({fmt.step})")
    print(f"  check     lr/(E+eps) in float = {LR / (e_res.value + EPS):.10f}")
    print(f"  saturated {st.saturated}")

    print()
    print("=" * 92)
    print("mu across the range of reference levels -- why the step register is wide")
    print("=" * 92)
    print(f"  {'|x_f| scale':>12} {'E[n]':>14} {'mu[n]':>16} {'saturated':>10}")
    for scale in (1.0, 0.1, 0.01, 1e-3, 1e-4, 0.0):
        win = [F.to_xf(F.as_float(v) * scale) for v in xf]
        er = energy(win, fmt)
        s = step_size(er.acc, LR, EPS, fmt)
        print(f"  {scale:>12g} {er.value:>14.3e} {s.value:>16.4f} {str(s.saturated):>10}")
    print(f"  a silent reference drives mu to lr/eps = {LR / EPS:.0f}; only eps bounds it")

    print()
    print("=" * 92)
    print("The weight update, per tap")
    print("=" * 92)
    err = F.to_sample(0.02)
    w_new, recs = update_taps(w, xf, err, st.mu, fmt)
    print(f"  e[n] = {F.as_float(err):.6f},  mu = {st.value:.6f}")
    print(trace(recs))
    print(f"\n  taps stalled: {stall_count(recs)}/{N_W}")

    print()
    print("=" * 92)
    print("Where the stall bites: coefficient width vs error level")
    print("=" * 92)
    print(f"  half an LSB: Q1.15 {F.Q(1,15).resolution/2:.2e}   "
          f"Q1.19 {F.Q(1,19).resolution/2:.2e}   Q1.23 {F.Q(1,23).resolution/2:.2e}")
    print()
    print(f"  {'e[n] asked':>11} {'e[n] stored':>12} "
          + "".join(f"{'Q1.'+str(b):>10}" for b in (15, 19, 23)))
    for e_val in (1e-1, 1e-2, 1e-3, 1e-4, 1e-5):
        e_q = F.to_sample(e_val, fmt)
        cells = []
        for bits in (15, 19, 23):
            f2 = replace(fmt, coef=F.Q(1, bits))
            xf2 = [F.to_xf(F.as_float(v), f2) for v in xf]
            w2 = [F.to_coef(F.as_float(v), f2) for v in w]
            s2 = step_size(energy(xf2, f2).acc, LR, EPS, f2)
            _, r2 = update_taps(w2, xf2, F.to_sample(e_val, f2), s2.mu, f2)
            dead = all(F.raw(r.delta) == 0 for r in r2)
            cells.append("DEAD" if dead else f"{stall_count(r2)}/{N_W}")
        print(f"  {e_val:>11.0e} {F.as_float(e_q):>12.2e} "
              + "".join(f"{c:>10}" for c in cells))
    print()
    print(f"  n/{N_W} = taps whose update rounded away at the coefficient write.")
    print(f"  DEAD  = e[n] itself quantized to zero in {fmt.sample}, so every delta is")
    print(f"          exactly 0 and NO coefficient width helps -- that floor is set by")
    print(f"          SAMPLE_W, not COEF_W. Half an LSB of {fmt.sample} is "
          f"{fmt.sample.resolution / 2:.2e}.")
    print(f"  The residual shrinks as the filter converges, so reading down a column is")
    print(f"  watching adaptation grind to a halt -- and the last row is it stopping dead.")

    print()
    print("=" * 92)
    print("Product order: only a question once the intermediate is narrowed")
    print("=" * 92)
    def spread(f2, inter_q):
        xf2 = [F.to_xf(F.as_float(v), f2) for v in xf]
        w2 = [F.to_coef(F.as_float(v), f2) for v in w]
        s2 = step_size(energy(xf2, f2).acc, LR, EPS, f2)
        e2 = F.to_sample(0.02, f2)
        outs = [update_taps(w2, xf2, e2, s2.mu, f2, order=o, inter_q=inter_q)
                for o in (ORDER_MU_ERR, ORDER_ERR_XF)]
        d_delta = max(abs(F.as_float(p.delta) - F.as_float(q.delta))
                      for p, q in zip(outs[0][1], outs[1][1]))
        d_w = max(abs(F.as_float(p) - F.as_float(q))
                  for p, q in zip(outs[0][0], outs[1][0]))
        return d_delta, d_w

    d_delta, d_w = spread(fmt, None)
    print(f"  exact intermediates, {fmt.coef} coefficients")
    print(f"    max |delta| difference between orders : {d_delta:.3e}")
    print(f"    max |w|     difference between orders : {d_w:.3e}   <- orders identical")
    print()
    narrow = F.Q(18, 14)
    d_delta, d_w = spread(fmt, narrow)
    print(f"  intermediate rounded to {narrow}, {fmt.coef} coefficients")
    print(f"    max |delta| difference between orders : {d_delta:.3e}   <- they DO diverge")
    print(f"    max |w|     difference between orders : {d_w:.3e}   <- but the write hides it")
    print(f"    half an LSB of {fmt.coef} is {fmt.coef.resolution / 2:.2e}, larger than the")
    print(f"    divergence, so at this coefficient width the order genuinely does not matter.")
    print()
    wide_coef = replace(fmt, coef=F.Q(1, 23))
    d_delta, d_w = spread(wide_coef, narrow)
    print(f"  intermediate rounded to {narrow}, {wide_coef.coef} coefficients")
    print(f"    max |delta| difference between orders : {d_delta:.3e}")
    print(f"    max |w|     difference between orders : {d_w:.3e}   <- now it survives the write")
    print()
    print(f"  So the order is not a free choice and not always a real one: it matters only")
    print(f"  when the intermediate is narrowed AND the coefficient register is fine enough")
    print(f"  to resolve the difference. {ORDER_MU_ERR} rounds a value spanning mu's 134 dB")
    print(f"  range; {ORDER_ERR_XF} rounds one bounded by the signal.")
