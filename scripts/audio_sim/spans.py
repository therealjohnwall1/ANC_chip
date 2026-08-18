"""
Tap counts: what the acoustics ask for, against what the silicon can hold.

This is the reconciliation step. decay.py says how long each path rings;
budget_model.py says how many taps fit in 16 TinyTapeout tiles. Here they meet,
and for the duct in calcuations.md they do not meet quietly.

How the two filter lengths are set:

    M (S_hat)  = the *span* of S(z): bulk delay + ring. S_hat is an FIR sitting
                 at the DAC output, so it has to carry the propagation delay as
                 leading taps before it can model any structure. Nothing to
                 tune -- M is whatever the measurement says.

    N_W (W(z)) = causal margin + K * ring. Two separate jobs. W has to bridge
                 the delay gap between the two paths (the reference arrives
                 that much earlier than the disturbance, and W's output has to
                 wait), and on top of that it needs enough memory to cover the
                 ringing it is cancelling, with margin K for the adaptation to
                 have somewhere to work.

theory_of_op.md writes this as T_w = K * T_s, which is the second term only.
That is fine when the mics are close together, but here the causal margin is
37 taps against a ~84 tap ceiling, so leaving it out understates N_W by nearly
half the budget. `margin_only=True` reproduces the doc's simpler rule if you
want to compare.
"""

from dataclasses import dataclass, replace
import math
import os

import numpy as np
import matplotlib.pyplot as plt

from scripts.budget_model import Budget, TECH, budget, report
from scripts.audio_sim import decay as dk
from scripts.audio_sim import duct as dt

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out")

# how much longer than the analytic ring to make the buffer, so `measure`
# always has room to find the crossing instead of hitting the truncation flag
_IR_MARGIN = 2.5
_TAIL_FLOOR_DB = -60.0


# ---------------------------------------------------------------------------
# Sizing the simulation itself
# ---------------------------------------------------------------------------

def analytic_ring_s(config: dt.Duct, g: float, threshold_db: float) -> float:
    """
    Closed-form ring time, used to size buffers before the real measurement.

    One round trip is 2L/c and costs two bounces, so amplitude falls by g^2,
    i.e. 40*log10(g) dB per round trip.
    """
    if g <= 0.0:
        return 0.0
    round_trip = 2.0 * config.duct_len / config.speed_sound
    return threshold_db / (40.0 * math.log10(g)) * round_trip


def ir_params(config: dt.Duct, g: float, threshold_db: float) -> tuple[int, int]:
    """(n_reflections, ir_len) deep and long enough to resolve this threshold."""
    if g <= 0.0:
        return 1, 256
    n_refl = max(8, math.ceil(math.log(10.0 ** (_TAIL_FLOOR_DB / 20.0)) / math.log(g)))
    deepest = min(threshold_db, _TAIL_FLOOR_DB)
    ir_len = int(analytic_ring_s(config, g, deepest) * config.fs * _IR_MARGIN) + 256
    return n_refl, ir_len


# ---------------------------------------------------------------------------
# One (threshold, K) point
# ---------------------------------------------------------------------------

@dataclass
class SpanSpec:
    """One choice of decay threshold and margin, priced in tiles."""
    threshold_db: float
    K: float
    g: float
    duct_len: float
    fs: float

    s_decay: dk.Decay
    p_decay: dk.Decay
    causal_margin: int   # taps the disturbance lags the reference by

    M: int               # S_hat taps
    N_W: int             # W(z) taps
    budget: Budget

    @property
    def causal(self) -> bool:
        return self.causal_margin >= 0

    @property
    def measured(self) -> bool:
        """False if either response was truncated -- tap counts are lower bounds."""
        return self.s_decay.crossed and self.p_decay.crossed

    @property
    def round_trip_taps(self) -> float:
        return 2.0 * self.duct_len / 343.0 * self.fs

    @property
    def resolved(self) -> bool:
        """
        False once the duct is so short that reflections arrive closer together
        than the fractional-delay kernel is wide (16 taps), so they smear into
        each other and the decay curve stops describing distinct arrivals.

        Worth checking before believing any "just shrink the duct" answer: the
        area model keeps returning tap counts down to arbitrarily small L, but
        below roughly 0.17 m at 16 kHz the acoustic model underneath it has
        stopped resolving the thing it is counting.
        """
        return self.round_trip_taps >= 16.0

    @property
    def feasible(self) -> bool:
        return self.causal and self.budget.feasible

    def __str__(self) -> str:
        flag = "OK " if self.feasible else "XX "
        note = "" if self.measured else "  (truncated, lower bound)"
        note += "" if self.resolved else "  (below kernel resolution)"
        return (f"{flag}thr={self.threshold_db:+.0f} dB  K={self.K:.1f}  g={self.g:.2f}  "
                f"L={self.duct_len:.2f} m\n"
                f"     ring {self.s_decay.ring_taps:5d}  margin {self.causal_margin:3d}  ->  "
                f"M={self.M:5d}  N_W={self.N_W:5d}  "
                f"tiles={self.budget.tiles:4d}/{TECH.max_tiles}  "
                f"util={self.budget.utilization*100:5.1f}%{note}")


def spec_from_duct(config: dt.Duct,
                   g: float,
                   threshold_db: float = -40.0,
                   K: float = 2.0,
                   width_bits: int = 16,
                   n_multipliers: int = 1,
                   margin_only: bool = False) -> SpanSpec:
    """
    Full step 1 -> 3 chain for one operating point.

    Args:
        config: duct geometry and sample rate
        g: reflection coefficient at both ends
        threshold_db: decay threshold defining "the response has ended"
        K: margin multiplier on the ring for W(z)
        width_bits: word width for stored values
        n_multipliers: MAC units, for the cycle side of the budget
        margin_only: use theory_of_op.md's plain N_W = K * ring instead of
            including the causal margin. Understates N_W when the mics are
            far apart; here for comparison.
    """
    n_refl, ir_len = ir_params(config, g, threshold_db)
    s_ir = dt.secondary_path(config, g, n_refl, ir_len)
    p_ir = dt.primary_path(config, g, n_refl, ir_len)

    s_decay = dk.measure(s_ir, config.fs, threshold_db)
    p_decay = dk.measure(p_ir, config.fs, threshold_db)
    margin = p_decay.arrival - s_decay.arrival

    M = s_decay.span_taps
    ring = int(round(K * s_decay.ring_taps))
    N_W = max(1, ring if margin_only else max(margin, 0) + ring)

    return SpanSpec(
        threshold_db=threshold_db, K=K, g=g,
        duct_len=config.duct_len, fs=config.fs,
        s_decay=s_decay, p_decay=p_decay, causal_margin=margin,
        M=M, N_W=N_W,
        budget=budget(N_W / config.fs, M / config.fs,
                      config.fs, width_bits, n_multipliers),
    )


# ---------------------------------------------------------------------------
# Lever grid: the two knobs you can turn without touching the hardware
# ---------------------------------------------------------------------------

def sweep_levers(config: dt.Duct,
                 g: float,
                 thresholds=(-20.0, -30.0, -40.0, -60.0),
                 margins=(1.0, 1.5, 2.0, 3.0),
                 width_bits: int = 16,
                 verbose: bool = True) -> list[SpanSpec]:
    """
    Every (threshold, K) combination, flagged feasible or not.

    These are levers 1 and 2 from the design loop. Both trade away ring-down
    coverage, so if the ring alone already exceeds the tap ceiling neither one
    can reach a feasible point and the grid comes back uniformly infeasible --
    which is the answer, and it points at lever 3 (change the acoustics).
    """
    specs = [spec_from_duct(config, g, thr, K, width_bits)
             for thr in thresholds for K in margins]
    if verbose:
        print(f"lever grid: L={config.duct_len} m, g={g}, fs={config.fs/1e3:.0f} kHz, "
              f"{width_bits}-bit, ceiling {TECH.max_tiles} tiles")
        for s in specs:
            print(s)
        n_ok = sum(s.feasible for s in specs)
        print(f"\n{n_ok}/{len(specs)} feasible"
              + ("" if n_ok else "  -- no (threshold, K) fits; the acoustics have to change"))
    return specs


def max_feasible_taps(width_bits: int = 16, tau_ratio: float = 0.5,
                      n_multipliers: int = 1) -> int:
    """
    Largest N_W that still fits the tile ceiling, at M = tau_ratio * N_W.

    The vertical line to draw on any attenuation-vs-taps plot: past it the
    design does not fit on the shuttle, whatever the acoustics want.
    """
    best = 0
    for n in range(1, 4000):
        m = max(1, int(round(n * tau_ratio)))
        b = budget(n / 16e3, m / 16e3, 16e3, width_bits, n_multipliers)
        if not b.feasible:
            break
        best = n
    return best


# ---------------------------------------------------------------------------
# Lever 3: what geometry would actually fit
# ---------------------------------------------------------------------------

def scaled_duct(config: dt.Duct, duct_len: float) -> dt.Duct:
    """
    Resize the enclosure, carrying the mics and speaker with it.

    Shrinking duct_len on its own would leave the speaker at 0.8 m and the error
    mic at 1.0 m sitting outside a 0.5 m duct, and the image sum would happily
    return numbers for it. Positions are held at their current fractions of the
    length instead, so the layout stays a scaled copy of the real one.

    Note this scales the causal margin too (it is spk_position / c), so very
    short ducts buy their cheap ring back by shrinking the delay budget FxLMS
    has to work in.
    """
    k = duct_len / config.duct_len
    return replace(config,
                   duct_len=duct_len,
                   ref_mic=config.ref_mic * k,
                   speaker=config.speaker * k,
                   error_mic=config.error_mic * k)


def geometry_grid(config: dt.Duct,
                  lengths=None,
                  gs=None,
                  threshold_db: float = -40.0,
                  K: float = 2.0,
                  width_bits: int = 16):
    """
    Tiles required across (duct length, reflection coefficient).

    Lever 3. Returns (lengths, gs, tiles) with tiles[i, j] for gs[i], lengths[j].
    """
    lengths = np.linspace(0.05, 1.3, 24) if lengths is None else np.asarray(lengths)
    gs = np.linspace(0.05, 0.9, 22) if gs is None else np.asarray(gs)

    tiles = np.zeros((len(gs), len(lengths)))
    for i, g in enumerate(gs):
        for j, L in enumerate(lengths):
            spec = spec_from_duct(scaled_duct(config, float(L)), float(g),
                                  threshold_db, K, width_bits)
            tiles[i, j] = spec.budget.tiles
    return lengths, gs, tiles


def plot_geometry_grid(config: dt.Duct, threshold_db: float = -40.0, K: float = 2.0,
                       width_bits: int = 16, ax=None):
    """Feasible region in (duct length, g), with the 16-tile contour drawn in."""
    lengths, gs, tiles = geometry_grid(config, threshold_db=threshold_db, K=K,
                                       width_bits=width_bits)
    if ax is None:
        _, ax = plt.subplots(figsize=(8, 5.5))

    mesh = ax.pcolormesh(lengths, gs, np.log10(tiles), cmap="viridis", shading="auto")
    cs = ax.contour(lengths, gs, tiles, levels=[TECH.max_tiles],
                    colors="#E24B4A", linewidths=2.5)
    ax.clabel(cs, fmt={TECH.max_tiles: f"{TECH.max_tiles} tiles"}, fontsize=9)

    # Part of the feasible wedge sits where the duct is shorter than the
    # fractional-delay kernel, so the acoustic model is no longer resolving the
    # arrivals whose decay it is counting. Mark it rather than let the contour
    # imply the whole region is usable.
    l_min = 16.0 / (2.0 / config.speed_sound * config.fs)
    if l_min > lengths.min():
        ax.axvspan(lengths.min(), l_min, facecolor="none", edgecolor="white",
                   hatch="///", linewidth=0.0, alpha=0.35, zorder=3)
        ax.axvline(l_min, color="white", linestyle=":", linewidth=1.5, zorder=4)
        ax.text(l_min, gs.max(), " below kernel resolution", color="white",
                fontsize=8, va="top", ha="left", zorder=5)

    ax.plot([config.duct_len], [0.7], marker="*", markersize=16,
            color="#E24B4A", markeredgecolor="white", markeredgewidth=1.2,
            linestyle="none", label=f"current: L={config.duct_len} m, g=0.7")

    cb = plt.colorbar(mesh, ax=ax)
    cb.set_label("log10(tiles required)")
    ax.set_xlabel("duct length L (m)")
    ax.set_ylabel("reflection coefficient g")
    ax.set_title(f"Area cost vs geometry  (thr={threshold_db:+.0f} dB, K={K}, "
                 f"{width_bits}-bit)\nfeasible region is below/left of the red contour")
    ax.legend(loc="upper right", fontsize=8)
    return ax


if __name__ == "__main__":
    config = dt.Duct()
    g = 0.7

    print("=" * 74)
    print("Ceilings")
    print("=" * 74)
    n_max = max_feasible_taps()
    print(f"  largest N_W that fits {TECH.max_tiles} tiles (16-bit, M=N/2): {n_max} taps "
          f"= {n_max / config.fs * 1e3:.2f} ms")
    print(f"  duct round trip 2L/c = {2*config.duct_len/config.speed_sound*1e3:.2f} ms "
          f"= {2*config.duct_len/config.speed_sound*config.fs:.0f} taps")
    print("  -> the first reflection lands past the entire tap budget\n")

    print("=" * 74)
    print("Levers 1 and 2: decay threshold and margin K")
    print("=" * 74)
    sweep_levers(config, g)

    print("\n" + "=" * 74)
    print("Lever 3: what geometry would fit (thr=-40 dB, K=2)")
    print("=" * 74)
    lengths, gs, tiles = geometry_grid(config, threshold_db=-40.0, K=2.0)
    l_min = 16.0 / (2.0 / config.speed_sound * config.fs)  # kernel-resolution floor
    print(f"  (model resolves arrivals only for L >= {l_min:.2f} m at {config.fs/1e3:.0f} kHz)")
    for gi in (0.1, 0.3, 0.5, 0.7):
        i = int(np.argmin(np.abs(gs - gi)))
        ok = lengths[tiles[i] <= TECH.max_tiles]
        if not ok.size:
            print(f"  g={gs[i]:.2f}: no length fits")
            continue
        mark = "" if ok.max() >= l_min else "   <-- below resolution, not trustworthy"
        print(f"  g={gs[i]:.2f}: L <= {ok.max():.2f} m{mark}")

    os.makedirs(OUT_DIR, exist_ok=True)
    plot_geometry_grid(config)
    plt.tight_layout()
    out = os.path.join(OUT_DIR, "spans_geometry.png")
    plt.savefig(out, dpi=140)
    print(f"\nwrote {os.path.normpath(out)}")
