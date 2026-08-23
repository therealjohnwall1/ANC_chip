"""
Layer 0: word formats and the arithmetic primitives every other layer uses.

Every quantization decision in golden/ lives here. Changing COEF_W from Q1.15
to Q1.23 to see what it buys should be one field in one dataclass, not an edit
in five files -- and the fxpmath rounding/overflow settings that stand in for
"what the RTL rounder actually does" should be visible in one place instead of
scattered across call sites.

Two rules the rest of the model depends on:

1. Nothing round-trips through a Python float. float64 carries a 53-bit
   mantissa and the accumulator here is 64 bits wide, so `float(acc)` silently
   drops bits off exactly the value the accumulator exists to protect. All
   arithmetic stays in the Fxp domain; floats appear only at the boundaries
   (a test vector going in, a printed number coming out).

2. Saturation is an assertion, not a behavior. With a 64-bit accumulator the
   partial sums measured in scripts/range_check.py (peak 1.33) cannot come
   close to overflowing, so if one ever does, that is a bug to be reported at
   the tap where it happened -- not something to quietly clamp and carry on
   with. Use `mul` / `acc_add`, which check, rather than raw operators.

Q notation follows the repo convention (scripts/range_check.py `q_format`):
the leading number INCLUDES the sign bit, so Q1.15 is a 16-bit signed word
with 15 fractional bits, range [-1, 1).

Run from the repo root:  python -m golden.fmt
"""

from dataclasses import dataclass, replace
import math

from fxpmath import Fxp

# fxpmath's rounding modes
ROUND_NEAREST = "around"  
TRUNCATE = "trunc"       
FLOOR = "floor"         

SATURATE = "saturate"
WRAP = "wrap"


# ---------------------------------------------------------------------------
# Q formats
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Q:
    """
    A signed fixed-point word, Q<int_bits>.<frac_bits>
    """
    int_bits: int
    frac_bits: int

    def __post_init__(self):
        assert self.int_bits >= 1, "need at least a sign bit"
        assert self.frac_bits >= 0

    @property
    def n_word(self) -> int:
        return self.int_bits + self.frac_bits

    @property
    def resolution(self) -> float:
        """One LSB."""
        return 2.0 ** -self.frac_bits

    @property
    def upper(self) -> float:
        return 2.0 ** (self.int_bits - 1) - self.resolution

    @property
    def lower(self) -> float:
        return -(2.0 ** (self.int_bits - 1))

    def __str__(self) -> str:
        return f"Q{self.int_bits}.{self.frac_bits}"

    @classmethod
    def parse(cls, text: str) -> "Q":
        i, f = text.lower().lstrip("q").split(".")
        return cls(int(i), int(f))


def int_bits_for(peak: float) -> int:
    """
    Integer bits (EXCLUDING sign) needed to hold `peak` without saturating
    """
    if peak <= 0.0 or not math.isfinite(peak):
        return 0
    return max(0, math.ceil(math.log2(peak)))


def q_holding(peak: float, frac_bits: int) -> Q:
    """Narrowest Q with `frac_bits` of fraction that still holds `peak`."""
    return Q(int_bits_for(peak) + 1, frac_bits)


# defined in theory_of_op, accumulator width
ACC_W = 64


@dataclass(frozen=True)
class Fmt:
    """
    Every word format in the datapath, plus the rounding/overflow policy.

    `acc` and `prod` are derived rather than stored: a MAC that rounds its own
    products is a different piece of hardware than one that does not, and the
    accumulator has to carry the full product fraction or the derivation stops
    meaning anything. Deriving them keeps that relationship from drifting when
    `sample` or `coef` is changed.
    """
    sample: Q = Q(1, 15)     # SAMPLE_W, theory_of_op.md
    coef: Q = Q(1, 15)       # COEF_W, theory_of_op.md
    energy: Q = Q(34, 30)    # see notes below -- shares the accumulator
    step: Q = Q(15, 17)      # OPEN, see notes below
    acc_w: int = ACC_W

    rounding: str = ROUND_NEAREST
    overflow: str = SATURATE

    # ---- derived ----------------------------------------------------------

    @property
    def prod(self) -> Q:
        """
        Exact sample x coef product. A 16x16 multiplier produces 32 bits and
        loses nothing; this format is what that array actually emits.
        """
        return Q(self.sample.int_bits + self.coef.int_bits,
                 self.sample.frac_bits + self.coef.frac_bits)

    @property
    def acc(self) -> Q:
        """
        MAC accumulator. Fraction must match `prod` exactly -- an accumulator
        narrower in the fraction is rounding every partial sum, which is a
        design choice nobody has made here. Everything left over after the
        fraction is integer headroom.
        """
        frac = self.prod.frac_bits
        assert self.acc_w > frac, f"acc_w={self.acc_w} cannot hold {frac} fractional bits"
        return Q(self.acc_w - frac, frac)

    # ---- templates --------------------------------------------------------

    def template(self, q: Q) -> Fxp:
        """A zero-valued Fxp in format `q`, for use as `like=`."""
        return Fxp(0.0, signed=True, n_word=q.n_word, n_frac=q.frac_bits,
                   rounding=self.rounding, overflow=self.overflow)


DEFAULT = Fmt()

# A deliberately over-wide format. Layer tests run golden at WIDE and compare
# against plain float64: at this precision quantization is far below float64's
# own error, so any disagreement is a structural bug in the layer split, not a
# fixed-point effect. That separation is the whole point of having it.
# Sized so its own grid (2^-56) is finer than float64's spacing near 1.0
# (~2^-52), which is what makes "golden at WIDE == the float reference" a
# statement about the layer split rather than about this format's precision.
WIDE = Fmt(sample=Q(8, 56), coef=Q(8, 56), energy=Q(16, 112), step=Q(24, 72),
           acc_w=128)


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

class SaturationError(AssertionError):
    """A value did not fit its format. Always a bug, never a runtime condition."""


def to(value, q: Q, fmt: Fmt = DEFAULT, *, where: str = "value",
       allow_saturation: bool = False) -> Fxp:
    """
    Quantize `value` (float, int, or Fxp) into format `q`.

    Saturation raises unless explicitly allowed. The one place it is legitimately
    allowed is the ADC boundary, where a real converter does clip.
    """
    out = Fxp(value, like=fmt.template(q))
    if not allow_saturation:
        assert_no_saturation(out, where=where, q=q)
    return out


def to_sample(value, fmt: Fmt = DEFAULT, *, where: str = "sample") -> Fxp:
    """Quantize into SAMPLE_W. Saturation is permitted -- the ADC clips."""
    return to(value, fmt.sample, fmt, where=where, allow_saturation=True)


def to_coef(value, fmt: Fmt = DEFAULT, *, where: str = "coef") -> Fxp:
    """
    Quantize into COEF_W. This is the coefficient-register write, and it is
    where the adaptation stall lives: once an update is smaller than half an
    LSB it rounds back to the value already stored and the tap stops moving.

    Saturation is permitted because a diverging filter genuinely does drive
    weights past the rail, and clamping is what the hardware would do. Callers
    that want to know check `saturated()`.
    """
    return to(value, fmt.coef, fmt, where=where, allow_saturation=True)


def mul(a: Fxp, b: Fxp) -> Fxp:
    """
    One multiplier. Exact: the output format carries every bit of the product,
    so nothing is lost here and any precision loss in the datapath is
    attributable to a later, deliberate resize.
    """
    p = a * b
    assert p.n_frac == a.n_frac + b.n_frac, (
        f"product lost fraction: {p.n_frac} != {a.n_frac} + {b.n_frac}")
    return p


def acc_add(acc: Fxp, addend: Fxp, fmt: Fmt = DEFAULT, *,
            where: str = "accumulator") -> Fxp:
    """
    One accumulate step, resized back into the accumulator format.

    fxpmath grows a sum by a bit to stay exact; real hardware does not, it has
    a fixed register. Resizing here is what makes the model honest about that,
    and the saturation check is what turns an overflow into a located bug
    instead of a wrong number three layers downstream.
    """
    out = Fxp(acc + addend, like=fmt.template(fmt.acc))
    assert_no_saturation(out, where=where, q=fmt.acc)
    return out


def acc_zero(fmt: Fmt = DEFAULT) -> Fxp:
    """A cleared accumulator."""
    return fmt.template(fmt.acc)


def saturated(x: Fxp) -> bool:
    return bool(x.status.get("overflow", False))


def assert_no_saturation(x: Fxp, *, where: str = "value", q: Q = None) -> None:
    if saturated(x):
        rng = f" (range [{q.lower}, {q.upper}])" if q is not None else ""
        raise SaturationError(f"{where} saturated in {q or 'format'}{rng}")


def raw(x: Fxp) -> int:
    """The stored integer -- what a waveform viewer shows."""
    return int(x.raw())


def hexs(x: Fxp) -> str:
    """The stored integer as hex, for diffing against RTL."""
    return str(x.hex())


def as_float(x: Fxp) -> float:
    """
    Escape hatch to float64, for printing and for comparing against the float
    reference. Lossy above 53 bits of significand -- never use it inside the
    datapath.
    """
    return float(x.astype(float))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

BACKING = {
    "sample": "theory_of_op.md (16-bit, Q1.15)",
    "coef": "theory_of_op.md (16-bit floor)",
    "acc": "theory_of_op.md (64-bit, 'play it safe')",
    "prod": "derived: sample x coef, exact",
    "energy": "OPEN -- no doc-assigned width",
    "step": "OPEN -- no doc-assigned width",
}


def describe(fmt: Fmt = DEFAULT) -> str:
    rows = [("sample", fmt.sample), ("coef", fmt.coef), ("prod", fmt.prod),
            ("acc", fmt.acc), ("energy", fmt.energy), ("step", fmt.step)]
    out = [f"  {'field':<8} {'format':>10} {'bits':>5} {'resolution':>12} "
           f"{'max':>16}   backing",
           f"  {'-'*8} {'-'*10:>10} {'-'*5:>5} {'-'*12:>12} {'-'*16:>16}   {'-'*40}"]
    for name, q in rows:
        out.append(f"  {name:<8} {str(q):>10} {q.n_word:>5} {q.resolution:>12.3e} "
                   f"{q.upper:>16.6g}   {BACKING[name]}")
    out.append("")
    out.append(f"  rounding={fmt.rounding}   overflow={fmt.overflow}")
    return "\n".join(out)


def step_range(lr: float, eps: float, energy_peak: float) -> dict:
    """
    What dynamic range does the NLMS step size actually need?

    mu[n] = lr / (E[n] + eps), so the two ends are set by the two ends of E:

      E -> 0     (reference goes quiet)  =>  mu -> lr/eps, the LARGEST step
      E -> peak  (reference is loud)     =>  mu -> lr/peak, the SMALLEST

    Which means eps is not a numerical nicety here, it is the only thing
    bounding the step size, and it therefore sets the integer width of the
    divider output. Pick eps smaller to protect the divide and the step
    register gets wider.
    """
    mu_max = lr / eps
    mu_min = lr / energy_peak
    return {
        "mu_max": mu_max,
        "mu_min": mu_min,
        "int_bits": int_bits_for(mu_max) + 1,
        "dynamic_range_db": 20.0 * math.log10(mu_max / mu_min),
    }


if __name__ == "__main__":
    LR, EPS = 0.1, 0.00001          # lr from LR_LADDER, eps from ans.py
    E_PEAK = 50.0                   # sum of x_f^2 over 32 taps, from range_check.py

    print("=" * 78)
    print("Layer 0 formats")
    print("=" * 78)
    print(describe(DEFAULT))

    print()
    print("=" * 78)
    print("Open decision 1: how wide does the energy accumulator have to be?")
    print("=" * 78)
    print(f"  eps = {EPS:g} is added to E[n] before the divide. If eps falls under")
    print(f"  half an LSB of whatever register holds E, it rounds away and the")
    print(f"  regularizer is gone -- mu is then unbounded when the reference goes quiet.")
    print()
    print(f"  E[n] peaks near {E_PEAK:g} (sum of x_f^2 over 32 taps), needing "
          f"{int_bits_for(E_PEAK) + 1} int bits incl sign.")
    print()
    print(f"    {'total bits':>10}  {'format':>10}  {'LSB':>11}  {'eps survives?':>14}")
    for total in (16, 24, 32, 48, 64):
        q = Q(int_bits_for(E_PEAK) + 1, total - int_bits_for(E_PEAK) - 1)
        lives = "yes" if EPS >= q.resolution / 2 else "NO -- rounds to 0"
        print(f"    {total:>10}  {str(q):>10}  {q.resolution:>11.3e}  {lives:>14}")
    print()
    print(f"  Default here reuses the {ACC_W}-bit MAC accumulator for E, which makes")
    print(f"  the question moot -- energy is just another MAC, so it costs no extra")
    print(f"  register. Narrowing it to save area is where eps starts to matter.")

    print()
    print("=" * 78)
    print("Open decision 2: how wide does the step-size register have to be?")
    print("=" * 78)
    r = step_range(LR, EPS, E_PEAK)
    print(f"  lr={LR:g}  eps={EPS:g}  E_peak={E_PEAK:g}")
    print(f"    mu at E->0    (quiet reference) : {r['mu_max']:12.4f}   <- largest")
    print(f"    mu at E->peak (loud reference)  : {r['mu_min']:12.6f}   <- smallest")
    print(f"    dynamic range                   : {r['dynamic_range_db']:12.1f} dB")
    print(f"    integer bits needed (incl sign) : {r['int_bits']:12d}")
    print()
    print(f"  So mu is NOT a small number in fixed point. eps alone bounds it, and")
    print(f"  a Q1.x step register -- the obvious first guess -- saturates by 4 orders")
    print(f"  of magnitude the moment the reference goes quiet.")
    print(f"  Current default {str(DEFAULT.step)} holds up to {DEFAULT.step.upper:.0f}.")

    print()
    print("=" * 78)
    print("Open decision 3: rounding mode on the coefficient write")
    print("=" * 78)
    print(f"  Half an LSB of {str(DEFAULT.coef)} is {DEFAULT.coef.resolution / 2:.3e}.")
    print(f"  range_check.py puts the adaptation stall near 12 fractional bits, so")
    print(f"  {str(DEFAULT.coef)} leaves ~3 bits of margin -- but truncation biases every")
    print(f"  update toward zero rather than splitting the error, which eats into")
    print(f"  that margin asymmetrically. Default is {DEFAULT.rounding}; the RTL has to")
    print(f"  implement whichever this ends up being.")
    print()
    val = 1.0 / 3
    for mode in (ROUND_NEAREST, TRUNCATE, FLOOR):
        f = replace(DEFAULT, rounding=mode)
        q = to_coef(val, f)
        print(f"    {mode:<8} 1/3 -> {as_float(q):.10f}  raw {raw(q):>6}  {hexs(q)}")
