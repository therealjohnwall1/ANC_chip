"""
Anchor ANC chip — area / cycle budget model.

Relates acoustic impulse response duration and sample rate to tap counts,
flip-flop storage, TinyTapeout tile count, and MAC cycle utilization.

All the technology constants are ESTIMATES. Replace them with numbers you've
verified (OpenLane reports, TT docs) before trusting any conclusion.
"""

from dataclasses import dataclass, replace
import os

import numpy as np
import matplotlib.pyplot as plt

# Plots go next to this module, not into whatever directory it was launched
# from -- the savefig calls below used bare relative paths and followed cwd.
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


# ---------------------------------------------------------------------------
# Technology / platform constants  -- VERIFY THESE
# ---------------------------------------------------------------------------

@dataclass(frozen=True)   # constants holder; frozen so it can be a field default
class Tech:
    f_clk_hz: float = 66.5e6      # core clock
    ge_per_dff: float = 6.0       # sky130 DFF in NAND2-equivalents
    ge_multiplier: float = 1200.0 # 16x16 array multiplier
    ge_fixed: float = 800.0       # FSM + accumulator + MMIO + misc
    ge_per_tile: float = 1900.0   # TinyTapeout tile capacity  <-- softest number
    max_tiles: int | None = 16    # 8x2 shuttle limit; None = no area ceiling (FPGA)

    def ge_multiplier_scaled(self, width_bits: int) -> float:
        """Multiplier area is roughly quadratic in operand width."""
        return self.ge_multiplier * (width_bits / 16.0) ** 2


TECH = Tech()

# Prototyping on an FPGA: tile count is still reported, because it is what an
# eventual ASIC would cost, but it stops being a constraint. What still binds
# is the cycle budget -- the MAC chain has to finish inside one sample period
# no matter what fabric it runs on.
FPGA = replace(TECH, max_tiles=None)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class Budget:
    N: int              # W(z) tap count
    M: int              # S_hat tap count
    flops: int          # total storage flip-flops
    ge: float           # gate equivalents
    tiles: int          # TinyTapeout tiles required
    macs: int           # multiplies per sample
    cycles_avail: float # clock cycles per sample period
    utilization: float  # macs / cycles_avail, as a fraction
    latency_us: float   # MAC-phase compute latency
    sample_period_us: float
    tech: Tech = TECH   # the technology this budget was priced against

    @property
    def fits_area(self) -> bool:
        # None means no area ceiling (FPGA prototype), so area never binds.
        return self.tech.max_tiles is None or self.tiles <= self.tech.max_tiles

    @property
    def fits_cycles(self) -> bool:
        return self.utilization <= 1.0

    @property
    def feasible(self) -> bool:
        return self.fits_area and self.fits_cycles


def budget(tau_w_s: float,
           tau_s_s: float,
           f_s_hz: float,
           width_bits: int,
           n_multipliers: int = 1,
           tech: Tech = TECH) -> Budget:
    """
    tau_w_s : impulse response duration W(z) must span, in seconds
    tau_s_s : impulse response duration of the secondary path, in seconds
    f_s_hz  : sample rate
    width_bits : uniform word width for stored values
    """
    N = max(1, int(round(tau_w_s * f_s_hz)))
    M = max(1, int(round(tau_s_s * f_s_hz)))

    # Storage: W weights, x history, x_f history, S_hat taps
    words = 3 * N + M
    flops = words * width_bits

    ge = (flops * tech.ge_per_dff
          + n_multipliers * tech.ge_multiplier_scaled(width_bits)
          + tech.ge_fixed)
    tiles = int(np.ceil(ge / tech.ge_per_tile))

    # FIR (N) + filtered-X (M) + update (N), plus the mu*e scalar
    macs = 2 * N + M + 1
    cycles_avail = tech.f_clk_hz / f_s_hz
    mac_cycles = np.ceil(macs / n_multipliers)
    utilization = mac_cycles / cycles_avail

    return Budget(
        N=N, M=M, flops=flops, ge=ge, tiles=tiles, macs=macs,
        cycles_avail=cycles_avail, utilization=utilization,
        latency_us=mac_cycles / tech.f_clk_hz * 1e6,
        sample_period_us=1e6 / f_s_hz,
        tech=tech,
    )


def report(label: str, b: Budget) -> None:
    flag = "OK " if b.feasible else "XX "
    ceiling = "inf" if b.tech.max_tiles is None else str(b.tech.max_tiles)
    print(f"{flag}{label}")
    print(f"     N={b.N:4d}  M={b.M:4d}  flops={b.flops:6d}  GE={b.ge:9,.0f}")
    print(f"     tiles={b.tiles:3d}/{ceiling}   "
          f"MACs={b.macs:5d}/{b.cycles_avail:7.0f} cycles "
          f"({b.utilization*100:5.1f}%)")
    print(f"     compute latency {b.latency_us:6.2f} us  "
          f"(sample period {b.sample_period_us:6.2f} us)")


# ---------------------------------------------------------------------------
# Plot 1 — tiles and utilization vs sample rate
# ---------------------------------------------------------------------------

def plot_vs_fs(tau_w_s, tau_s_s, width_bits, n_mult=1,
               fs_range=(4e3, 200e3), ax=None):
    fs = np.linspace(*fs_range, 400)
    bs = [budget(tau_w_s, tau_s_s, f, width_bits, n_mult) for f in fs]

    if ax is None:
        _, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(fs / 1e3, [b.tiles for b in bs], color="#534AB7", label="tiles")
    ax.axhline(TECH.max_tiles, color="#E24B4A", ls="--", lw=1,
               label=f"{TECH.max_tiles}-tile limit")
    ax.set_xlabel("sample rate (kHz)")
    ax.set_ylabel("TinyTapeout tiles", color="#534AB7")
    ax.set_ylim(0, max(TECH.max_tiles * 2.5, max(b.tiles for b in bs) * 1.05))

    ax2 = ax.twinx()
    ax2.plot(fs / 1e3, [b.utilization * 100 for b in bs],
             color="#1D9E75", lw=1.2, label="MAC utilization")
    ax2.axhline(100, color="#1D9E75", ls=":", lw=1)
    ax2.set_ylabel("MAC utilization (%)", color="#1D9E75")
    ax2.set_ylim(0, 200)

    ax.set_title(f"tau_W={tau_w_s*1e3:.1f} ms, tau_S={tau_s_s*1e3:.1f} ms, "
                 f"{width_bits}-bit, {n_mult} mult")
    ax.legend(loc="upper left", fontsize=9)
    return ax


# ---------------------------------------------------------------------------
# Plot 2 — feasibility region over (sample rate, impulse response duration)
# ---------------------------------------------------------------------------

def plot_feasible_region(width_bits=16, tau_ratio=0.5, n_mult=1,
                         fs_range=(4e3, 200e3), tau_range=(0.1e-3, 20e-3),
                         ax=None):
    """
    tau_ratio: tau_S / tau_W, since the two aren't independent physically.
    Shading = tiles required. Contours mark the hard limits.
    """
    fs = np.linspace(*fs_range, 220)
    tau = np.linspace(*tau_range, 220)
    FS, TAU = np.meshgrid(fs, tau)

    tiles = np.zeros_like(FS)
    util = np.zeros_like(FS)
    for i in range(FS.shape[0]):
        for j in range(FS.shape[1]):
            b = budget(TAU[i, j], TAU[i, j] * tau_ratio,
                       FS[i, j], width_bits, n_mult)
            tiles[i, j] = b.tiles
            util[i, j] = b.utilization

    if ax is None:
        _, ax = plt.subplots(figsize=(8, 5))
    im = ax.pcolormesh(FS / 1e3, TAU * 1e3, np.clip(tiles, 0, 64),
                       cmap="viridis_r", shading="auto")
    plt.colorbar(im, ax=ax, label="tiles (clipped at 64)")

    ax.contour(FS / 1e3, TAU * 1e3, tiles, levels=[TECH.max_tiles],
               colors="#E24B4A", linewidths=2)
    ax.contour(FS / 1e3, TAU * 1e3, util, levels=[1.0],
               colors="white", linewidths=2, linestyles="--")

    ax.set_xlabel("sample rate (kHz)")
    ax.set_ylabel("impulse response duration tau_W (ms)")
    ax.set_title(f"feasible region — {width_bits}-bit, {n_mult} mult\n"
                 f"red = {TECH.max_tiles}-tile limit, "
                 f"white dashed = 100% MAC utilization")
    return ax


# ---------------------------------------------------------------------------
# Plot 3 — marginal cost of a tap vs marginal cost of a bit
# ---------------------------------------------------------------------------

def plot_taps_vs_width(f_s_hz=96e3, tau_ratio=0.5, n_mult=1, ax=None):
    widths = np.arange(8, 33)
    taus = np.linspace(0.1e-3, 8e-3, 200)
    W, T = np.meshgrid(widths, taus)

    tiles = np.zeros_like(W, dtype=float)
    for i in range(W.shape[0]):
        for j in range(W.shape[1]):
            tiles[i, j] = budget(T[i, j], T[i, j] * tau_ratio,
                                 f_s_hz, int(W[i, j]), n_mult).tiles

    if ax is None:
        _, ax = plt.subplots(figsize=(8, 5))
    cs = ax.contour(W, T * 1e3, tiles,
                    levels=[4, 8, 16, 24, 32, 48], cmap="viridis_r")
    ax.clabel(cs, inline=True, fontsize=8, fmt="%d tiles")
    ax.contour(W, T * 1e3, tiles, levels=[TECH.max_tiles],
               colors="#E24B4A", linewidths=2)
    ax.set_xlabel("word width (bits)")
    ax.set_ylabel("impulse response duration tau_W (ms)")
    ax.set_title(f"constant-area contours at f_s = {f_s_hz/1e3:.0f} kHz\n"
                 "slope tells you bits-per-tap exchange rate")
    return ax


# ---------------------------------------------------------------------------
# Utility: largest feasible tau at a given sample rate
# ---------------------------------------------------------------------------

def max_feasible_tau(f_s_hz, width_bits=16, tau_ratio=0.5, n_mult=1,
                     hi=50e-3, tol=1e-6):
    """Binary search for the longest impulse response that still fits."""
    lo = 0.0
    if not budget(tol, tol * tau_ratio, f_s_hz, width_bits, n_mult).feasible:
        return 0.0
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if budget(mid, mid * tau_ratio, f_s_hz, width_bits, n_mult).feasible:
            lo = mid
        else:
            hi = mid
    return lo


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 68)
    print("Point checks")
    print("=" * 68)
    for fs, tw, ts, w in [
        (16e3, 2e-3, 1e-3, 16),
        (48e3, 2e-3, 1e-3, 16),
        (96e3, 2e-3, 1e-3, 16),
        (96e3, 0.4e-3, 0.2e-3, 16),   # short earbud-scale cavity
        (96e3, 0.4e-3, 0.2e-3, 12),
        (192e3, 0.4e-3, 0.2e-3, 16),
    ]:
        report(f"f_s={fs/1e3:5.0f} kHz  tau_W={tw*1e3:.1f} ms  "
               f"tau_S={ts*1e3:.1f} ms  {w}-bit",
               budget(tw, ts, fs, w))
        print()

    print("=" * 68)
    print("Longest impulse response that fits, by sample rate (16-bit)")
    print("=" * 68)
    for fs in [8e3, 16e3, 48e3, 96e3, 192e3, 384e3]:
        t = max_feasible_tau(fs)
        b = budget(t, t * 0.5, fs, 16) if t > 0 else None
        n = b.N if b else 0
        print(f"  f_s={fs/1e3:6.0f} kHz -> tau_max={t*1e3:6.3f} ms  (N={n})")

    os.makedirs(OUT_DIR, exist_ok=True)

    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    plot_vs_fs(2e-3, 1e-3, 16, ax=axes[0])
    plot_vs_fs(0.4e-3, 0.2e-3, 16, ax=axes[1])
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, "budget_vs_fs.png"), dpi=140)

    plot_feasible_region()
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, "budget_feasible_region.png"), dpi=140)

    plot_taps_vs_width()
    plt.tight_layout()
    plt.savefig(os.path.join(OUT_DIR, "budget_taps_vs_width.png"), dpi=140)

    print("\nwrote budget_vs_fs.png, budget_feasible_region.png, "
          "budget_taps_vs_width.png")
