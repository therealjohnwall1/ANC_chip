"""
Layer 1: the MAC. One multiply-accumulate cycle, and a sequence of them.

This is the single piece of arithmetic hardware the whole datapath is built
from. W(z) uses it, S_hat(z) uses it, and the NLMS energy sum uses it -- the
same multiplier and the same accumulator, three different sets of operands.
Modelling it once and reusing it is not just tidiness here, it mirrors what
the RTL will actually instantiate.

Two things this layer deliberately does NOT know:

  * which samples belong in the window. `x_f[n]` includes `x[n]` and `y[n]`
    does not (golden/README.md section 3), and that offset logic lives in
    fir.py. Here the coefficient and sample arrays arrive already aligned,
    element k against element k, so an off-by-one fails a fir.py test instead
    of quietly corrupting the MAC.

  * what format the operands are stored in. It takes whatever Fxp it is given
    and lets the product grow exactly. The one place that matters is the
    energy sum, where both operands are filtered-reference samples rather than
    a coefficient and a sample -- see the note in `fir_sequence`.

What it does know is that every partial sum is worth keeping. scripts/range_check.py
sizes the accumulator against max|cumsum| rather than max|dot| precisely because
the hardware accumulates one tap at a time, so an overflow can happen at tap 9
of 32 and still land on a perfectly reasonable final answer. Returning the
partials is what lets a saturation report the cycle it happened on.

Run from the repo root:  python -m golden.mac
"""

from dataclasses import dataclass
from typing import Sequence

from fxpmath import Fxp

from golden import fmt as F


# ---------------------------------------------------------------------------
# One cycle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MacRecord:
    """
    One MAC cycle, as it would appear in a waveform viewer.

    Holds the Fxp objects rather than floats so the raw integers stay
    available -- those, not the decimal values, are what a testbench compares.
    """
    k: int              # tap index == cycle number within the sequence
    coef: Fxp
    sample: Fxp
    prod: Fxp
    acc_in: Fxp
    acc_out: Fxp

    @property
    def partial(self) -> float:
        """The running sum after this cycle, as a float, for reporting."""
        return F.as_float(self.acc_out)

    def row(self) -> str:
        return (f"  {self.k:>3}  {F.as_float(self.coef):>12.8f} {F.hexs(self.coef):>8}"
                f"  {F.as_float(self.sample):>12.8f} {F.hexs(self.sample):>8}"
                f"  {F.as_float(self.prod):>14.10f}"
                f"  {self.partial:>14.10f} {F.hexs(self.acc_out):>18}")


HEADER = (f"  {'k':>3}  {'coef':>12} {'hex':>8}  {'sample':>12} {'hex':>8}"
          f"  {'product':>14}  {'acc':>14} {'acc hex':>18}")


def mac_step(acc: Fxp, coef, sample, fmt: F.Fmt = F.DEFAULT, *, k: int = 0,
             where: str = "mac") -> tuple:
    """
    One cycle: acc <- acc + coef * sample.

    The multiply is exact (fmt.prod carries every bit a 16x16 array produces);
    the only place anything can go wrong is the accumulate, which resizes back
    into the fixed accumulator register and asserts if it does not fit.

    Bare floats are accepted for convenience and quantized by role -- `coef`
    into COEF_W, `sample` into SAMPLE_W. Pass Fxp directly when the operands
    are not that pair (the energy sum squares two filtered samples).

    Returns (acc_out, MacRecord).
    """
    if not isinstance(coef, Fxp):
        coef = F.to_coef(coef, fmt, where=f"{where} coef[{k}]")
    if not isinstance(sample, Fxp):
        sample = F.to_sample(sample, fmt, where=f"{where} sample[{k}]")

    prod = F.mul(coef, sample)
    acc_out = F.acc_add(acc, prod, fmt, where=f"{where} acc after tap {k}")
    return acc_out, MacRecord(k=k, coef=coef, sample=sample, prod=prod,
                              acc_in=acc, acc_out=acc_out)


# ---------------------------------------------------------------------------
# A sequence of cycles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FirResult:
    """The outcome of one MAC sequence, plus every cycle that produced it."""
    acc: Fxp                    # final accumulator, full accumulator width
    records: tuple              # tuple[MacRecord], one per tap

    @property
    def value(self) -> float:
        return F.as_float(self.acc)

    @property
    def partials(self) -> tuple:
        return tuple(r.partial for r in self.records)

    @property
    def peak_partial(self) -> float:
        """
        max|running sum| over the sequence -- the quantity the accumulator has
        to hold. Directly comparable to `accumulator_peak` in
        scripts/range_check.py, which measures the same thing in float64.
        """
        return max((abs(p) for p in self.partials), default=0.0)

    def trace(self) -> str:
        return "\n".join([HEADER] + [r.row() for r in self.records])


def fir_sequence(coefs: Sequence, window: Sequence, fmt: F.Fmt = F.DEFAULT, *,
                 where: str = "fir") -> FirResult:
    """
    MAC `coefs` against `window`, element k against element k, and keep every
    partial sum.

    The two arrays must already be aligned by the caller -- this does not
    reverse, offset, or index anything. `window[k]` is whatever sample the
    coefficient `coefs[k]` is supposed to multiply.

    Accumulation runs in tap order, k = 0 upward, the way a serial MAC would.
    With `fmt.acc` carrying the full product fraction (see fmt.Fmt.acc) that
    ordering does not change the result -- there is no rounding between
    cycles, so the sum is exact and reassociation is free. That stops being
    true the moment the accumulator is narrowed, at which point tap order
    becomes part of the specification rather than an implementation detail.

    Reused for the NLMS energy by passing the same array twice: E = sum
    x_f[k]^2 is a MAC whose two operands happen to be equal. Pass those as Fxp,
    since a filtered-reference sample is not a coefficient and should not be
    quantized as one.
    """
    assert len(coefs) == len(window), (
        f"{where}: {len(coefs)} coefficients against {len(window)} samples")

    acc = F.acc_zero(fmt)
    records = []
    for k, (c, s) in enumerate(zip(coefs, window)):
        acc, rec = mac_step(acc, c, s, fmt, k=k, where=where)
        records.append(rec)

    return FirResult(acc=acc, records=tuple(records))


if __name__ == "__main__":
    import numpy as np

    rng = np.random.default_rng(11)
    N = 8
    coefs = rng.uniform(-0.8, 0.8, N)
    window = rng.uniform(-0.9, 0.9, N)

    print("=" * 96)
    print(f"One {N}-tap MAC sequence at {F.DEFAULT.sample} samples / "
          f"{F.DEFAULT.coef} coefficients, {F.DEFAULT.acc} accumulator")
    print("=" * 96)
    r = fir_sequence(coefs, window)
    print(r.trace())
    print()
    print(f"  result      {r.value:.12f}   {F.hexs(r.acc)}")
    print(f"  peak|acc|   {r.peak_partial:.12f}   <- what the accumulator must hold")
    print(f"  final|acc|  {abs(r.value):.12f}   <- what max|dot| would have reported")
    if r.peak_partial > abs(r.value):
        print(f"  the running sum goes {r.peak_partial / max(abs(r.value), 1e-12):.2f}x "
              f"past the final answer mid-sequence")

    print()
    print("=" * 96)
    print("Same hardware, three jobs")
    print("=" * 96)
    x_f = [F.to_sample(v) for v in window]
    e = fir_sequence(x_f, x_f, where="energy")
    xf_f = [F.as_float(v) for v in x_f]
    print(f"  W(z) / S_hat(z)   coefs . window   -> {r.value:.10f}")
    print(f"  NLMS energy       window . window  -> {e.value:.10f}"
          f"   (float64 {float(np.dot(xf_f, xf_f)):.10f})")
    print(f"  every one of them is {N} cycles of the same multiplier and accumulator")
