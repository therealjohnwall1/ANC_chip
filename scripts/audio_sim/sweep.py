"""
What the taps actually buy: run FxLMS across filter lengths and measure it.

spans.py says how many taps the acoustics *ask* for. That is an open-loop
calculation -- it never runs the adaptive filter, so it cannot say whether the
taps do any good. This does. For each configuration it builds the two paths,
drives the reference with noise, runs the FxLMS loop from ans.py, and measures
the residual against the disturbance in the bands theory_of_op.md sets targets
for (15-25 dB over 0-1 kHz, 5-10 dB over 1-2 kHz).

Two questions:

  sweep_taps   attenuation vs N_W. Where does the curve flatten? Past the knee
               more taps cost area and buy nothing.
  sweep_shat   attenuation vs M, holding the *real* S(z) at full length while
               S_hat is truncated. This prices the decay threshold: choosing
               -40 dB over -60 dB is choosing to leave the last 20 dB of ring
               unmodeled, and the cost of that shows up here as dB of lost
               cancellation rather than as a judgement call.

Area is reported but does not gate anything -- the target is an FPGA prototype,
so tile count is future ASIC information, not a constraint. The cycle budget
still binds: the MAC chain has to finish inside a sample period on any fabric.
"""

from dataclasses import dataclass, field
import math
import os

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from scripts.ans import lms_anc
from scripts.gen_noise import generate_noise
from scripts.tune import band_attenuation
from scripts.budget_model import FPGA, budget
from scripts.audio_sim import decay as dk
from scripts.audio_sim import duct as dt
from scripts.audio_sim import spans as sp

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out")

# Categorical slots 1-4, fixed order, never cycled. Validated for line charts
# (adjacent-pair CVD dE 9.1, normal-vision 22.9). Two of them fall under 3:1 on
# a light surface, so every series also carries a direct label and the numbers
# are printed as a table.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")

# Single-hue sequential ramp, light -> dark, for the magnitude heatmap.
SEQ = LinearSegmentedColormap.from_list("seq_blue", [
    "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b",
])

INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#d9d8d4"

BANDS = [(0.0, 1000.0), (1000.0, 2000.0)]
BAND_LABELS = ("0-1000Hz", "1000-2000Hz")
TARGETS = {"0-1000Hz": (15.0, 25.0), "1000-2000Hz": (5.0, 10.0)}


# ---------------------------------------------------------------------------
# Configurations under test
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    """One acoustic configuration to sweep."""
    label: str
    duct_len: float
    g: float

    def duct(self, base: dt.Duct) -> dt.Duct:
        return sp.scaled_duct(base, self.duct_len)


DEFAULT_CASES = (
    Case("L=1.2 m, g=0.7  (as built)", 1.20, 0.70),
    Case("L=1.2 m, g=0.3  (damped)",   1.20, 0.30),
    Case("L=0.3 m, g=0.5  (short)",    0.30, 0.50),
    Case("L=0.3 m, g=0.1  (short+damped)", 0.30, 0.10),
)


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------

@dataclass
class Point:
    case: Case
    n_w: int
    m_shat: int
    atten_db: float                       # broadband
    bands: dict = field(default_factory=dict)
    tiles: int = 0
    utilization: float = 0.0
    causal_margin: int = 0
    lr: float = float("nan")
    stable: bool = True


def _paths(config: dt.Duct, g: float, floor_db: float = -60.0):
    """Real S(z) and P(z), truncated where they fall under the floor."""
    n_refl, ir_len = sp.ir_params(config, g, floor_db)
    s_ir = dt.secondary_path(config, g, n_refl, ir_len)
    p_ir = dt.primary_path(config, g, n_refl, ir_len)
    s_len = dk.measure(s_ir, config.fs, floor_db).span_taps
    p_len = dk.measure(p_ir, config.fs, floor_db).span_taps
    return s_ir[:max(1, s_len)], p_ir[:max(1, p_len)]


LR_LADDER = (0.3, 0.1, 0.03)

# Seconds of audio the filter gets to adapt in. This is a fixed budget on
# purpose, and it is what makes the sweep mean anything.
#
# With an exact S_hat and no sensor noise, FxLMS drives the residual arbitrarily
# low given arbitrarily long -- measured directly: 512 taps reaches 25 dB after
# 1.5 s and 91 dB after 50 s, still climbing. So "steady-state attenuation vs
# taps" has no steady state to report, and any sweep that lets the run length
# grow with the filter is really plotting convergence speed.
#
# A real unit has to converge during bring-up, so hold the adaptation budget
# fixed and let the tap count trade against it: too few taps cannot model the
# path, too many cannot converge inside the budget.
ADAPT_S = 2.0

# Uncorrelated noise at the error mic, relative to the disturbance. A sensor
# floor exists in any real system and nothing below it is cancellable, so this
# is what stops the sim reporting 90 dB of attenuation for a problem where the
# hardware target is 15-25 dB.
NOISE_FLOOR_DB = -40.0


def _run_once(case: Case, base: dt.Duct, n_w: int, m_shat, lr: float,
              n: int, seed: int, f0: float, a_tone: float, sig: float,
              tail_frac: float, noise_floor_db: float) -> Point:
    """One FxLMS run at one step size."""
    config = case.duct(base)
    s_taps, p_taps = _paths(config, case.g)
    if m_shat is None:
        m_shat = len(s_taps)
    s_hat = s_taps[:max(1, m_shat)]

    np.random.seed(seed)
    x = generate_noise(config.fs, a_tone, sig, f0, n)
    d = np.convolve(x, p_taps)[:n]      # identical to FIR_filter, vectorised

    # Error-mic sensor floor: uncorrelated with x, so no filter can remove it.
    if noise_floor_db is not None:
        rms = float(np.sqrt(np.mean(d ** 2)))
        d = d + np.random.normal(0.0, rms * 10.0 ** (noise_floor_db / 20.0), n)

    with np.errstate(over="ignore", invalid="ignore"):
        _, d_out, _, e, _ = lms_anc(x, d, n_w, lr, s_taps=s_taps, s_hat_taps=s_hat,
                                    normalize=True, keep_history=False)

    tail = max(1, int(len(e) * tail_frac))
    d_t, e_t = d_out[-tail:], e[-tail:]

    # Stability: finite, not growing over the second half, and not actively
    # making the noise worse. A run can stay finite and still be diverging
    # slowly, which a plain isfinite() check happily reports as a result.
    half = len(e) // 2
    with np.errstate(over="ignore", invalid="ignore"):
        early = float(np.sum(e[half:half + max(1, len(e) // 4)] ** 2))
        late = float(np.sum(e[-max(1, len(e) // 4):] ** 2))
        p_b, p_a = float(np.sum(d_t ** 2)), float(np.sum(e_t ** 2))
        atten = 10.0 * math.log10(p_b / p_a) if p_a > 0 and p_b > 0 and np.isfinite(p_a) else float("nan")

    stable = (bool(np.all(np.isfinite(e_t)))
              and np.isfinite(atten) and atten > -3.0
              and np.isfinite(late) and late <= early * 4.0)

    if stable:
        bands = band_attenuation(d_t, e_t, config.fs, BANDS)
    else:
        atten, bands = float("nan"), {k: float("nan") for k in BAND_LABELS}

    b = budget(n_w / config.fs, m_shat / config.fs, config.fs, 16, 1, tech=FPGA)
    s_dec = dk.measure(s_taps, config.fs, -40.0)
    p_dec = dk.measure(p_taps, config.fs, -40.0)

    return Point(case=case, n_w=n_w, m_shat=m_shat, atten_db=atten, bands=bands,
                 tiles=b.tiles, utilization=b.utilization, lr=lr,
                 causal_margin=p_dec.arrival - s_dec.arrival, stable=stable)


def run_point(case: Case, base: dt.Duct, n_w: int, m_shat: int = None,
              lr=None, n: int = None, seed: int = 0,
              f0: float = 300.0, a_tone: float = 1.0, sig: float = 0.7,
              tail_frac: float = 0.34,
              noise_floor_db: float = NOISE_FLOOR_DB) -> Point:
    """
    Best attenuation this filter length can reach, over a small step-size ladder.

    The step size is searched rather than fixed because the question is what a
    tap count is worth, not what it is worth at one particular lr. A single lr
    across the sweep measures step-size tuning as much as filter length: too
    large and the long filters diverge, too small and they have not converged
    by the end of the run, either way reading as "more taps are worse", which
    cannot be true asymptotically.

    NLMS throughout (normalize=True), so lr means the same thing at every length.

    Args:
        m_shat: S_hat length. Defaults to the full modeled S(z) -- a nearly
            perfect estimate, which isolates N_W as the only variable.
        lr: fixed step size, or None to search LR_LADDER.
        n: run length in samples; defaults to the fixed ADAPT_S budget. Held
            constant across tap counts on purpose -- see ADAPT_S.
        tail_frac: fraction at the end used for the steady-state measurement.
        noise_floor_db: error-mic sensor floor, or None for a noiseless sim.
    """
    n = n or int(ADAPT_S * (case.duct(base).fs))
    ladder = (lr,) if lr is not None else LR_LADDER

    best = None
    for step in ladder:
        pt = _run_once(case, base, n_w, m_shat, step, n, seed, f0, a_tone, sig,
                       tail_frac, noise_floor_db)
        if pt.stable and (best is None or pt.atten_db > best.atten_db):
            best = pt
    return best or _run_once(case, base, n_w, m_shat, ladder[-1], n, seed,
                             f0, a_tone, sig, tail_frac, noise_floor_db)


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------

def sweep_taps(cases=DEFAULT_CASES, base: dt.Duct = None, n_w_list=None,
               verbose: bool = True, **kw) -> list[Point]:
    """Attenuation vs W(z) length, every case, S_hat held near-perfect."""
    base = base or dt.Duct()
    n_w_list = n_w_list or [8, 16, 32, 64, 128, 256, 512, 1024]
    out = []
    for case in cases:
        for n_w in n_w_list:
            pt = run_point(case, base, n_w, **kw)
            out.append(pt)
            if verbose:
                bands = "  ".join(f"{k}={pt.bands.get(k, float('nan')):6.2f}" for k in BAND_LABELS)
                print(f"  {case.label:32} N_W={n_w:5d}  broadband={pt.atten_db:6.2f} dB  {bands}"
                      f"  lr={pt.lr:.3f}  tiles={pt.tiles:4d}  util={pt.utilization*100:5.1f}%"
                      + ("" if pt.stable else "  UNSTABLE"))
    return out


def sweep_shat(cases=DEFAULT_CASES, base: dt.Duct = None, n_w: int = 256,
               m_list=None, verbose: bool = True, **kw) -> list[Point]:
    """
    Attenuation vs S_hat length, with the real S(z) left at full length.

    This is the truncation cost. Every m corresponds to a decay threshold via
    decay.measure, so the curve converts "which threshold should I use" into
    "how many dB does each one cost".
    """
    base = base or dt.Duct()
    m_list = m_list or [4, 8, 16, 32, 64, 128, 256, 512]
    out = []
    for case in cases:
        for m in m_list:
            pt = run_point(case, base, n_w, m_shat=m, **kw)
            out.append(pt)
            if verbose:
                print(f"  {case.label:32} M={m:5d}  broadband={pt.atten_db:6.2f} dB"
                      + ("" if pt.stable else "  UNSTABLE"))
    return out


def sweep_grid(case: Case, base: dt.Duct = None, n_w_list=None, m_list=None,
               verbose: bool = True, **kw) -> tuple:
    """Broadband attenuation over the (N_W, M) plane, for one case."""
    base = base or dt.Duct()
    n_w_list = n_w_list or [16, 32, 64, 128, 256, 512]
    m_list = m_list or [4, 8, 16, 32, 64, 128]
    z = np.full((len(m_list), len(n_w_list)), np.nan)
    for i, m in enumerate(m_list):
        for j, n_w in enumerate(n_w_list):
            z[i, j] = run_point(case, base, n_w, m_shat=m, **kw).atten_db
        if verbose:
            print(f"  M={m:4d}: " + "  ".join(f"{v:6.2f}" for v in z[i]))
    return n_w_list, m_list, z


def find_knee(n_w_list, atten, tol_db: float = 0.5):
    """
    First tap count past which each doubling buys less than tol_db.

    Returns None if the curve never flattens -- which means N_W was not the
    binding constraint over the range swept.
    """
    xs, ys = list(n_w_list), list(atten)
    for i in range(len(ys) - 1):
        if all(np.isfinite(ys[j + 1]) and np.isfinite(ys[j])
               and (ys[j + 1] - ys[j]) < tol_db for j in range(i, len(ys) - 1)):
            return xs[i]
    return None


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def _style(ax):
    """Recessive frame: the data should be the only assertive thing on it."""
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.9)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)


def _band_panel(ax, points, band: str, n_w_list, title: str):
    """Attenuation vs W(z) taps, one line per configuration, for one band."""
    lo, hi = TARGETS[band]
    ax.axhspan(lo, hi, color="#1baf7a", alpha=0.10, zorder=0)
    ax.text(0.995, (lo + hi) / 2, f"target {lo:g}-{hi:g} dB ", color=INK_2,
            fontsize=8, va="center", ha="right", transform=ax.get_yaxis_transform())

    for i, case in enumerate(dict.fromkeys(p.case for p in points)):
        xs = [p.n_w for p in points if p.case is case]
        ys = [p.bands.get(band, float("nan")) for p in points if p.case is case]
        colour = SERIES[i % len(SERIES)]
        ax.plot(xs, ys, color=colour, linewidth=2.0, marker="o", markersize=5,
                markeredgecolor="white", markeredgewidth=0.8, label=case.label, zorder=3)

        finite = [(x, y) for x, y in zip(xs, ys) if np.isfinite(y)]
        if finite:
            # Peak marker: the tap count worth building, for this config.
            bx, by = max(finite, key=lambda t: t[1])
            ax.plot([bx], [by], marker="*", markersize=15, color=colour,
                    markeredgecolor="white", markeredgewidth=1.0, zorder=4)
            # Direct label -- two palette slots sit under 3:1 on a light
            # surface, so identity never rests on colour alone.
            lx, ly = finite[-1]
            ax.annotate(f" {case.label.split('(')[1].rstrip(')')}", (lx, ly),
                        color=INK, fontsize=8, va="center", ha="left", zorder=5)

    ax.set_xscale("log", base=2)
    ax.set_xticks(n_w_list)
    ax.set_xticklabels([str(v) for v in n_w_list])
    ax.set_xlabel("W(z) taps  (N_W)", color=INK_2, fontsize=9)
    ax.set_ylabel("attenuation (dB)", color=INK_2, fontsize=9)
    ax.set_title(title, color=INK, fontsize=10.5, loc="left")
    ax.set_xlim(n_w_list[0] * 0.85, n_w_list[-1] * 2.6)
    _style(ax)


def _grid_panel(ax, n_w_list, m_list, z, case_label: str):
    """Broadband attenuation over (W taps, S_hat taps): three variables, one plane."""
    # NaN means the run diverged, which is a result about S_hat being too
    # short to keep FxLMS stable -- render it as a labelled state rather than
    # as white space that reads like missing data.
    cmap = SEQ.copy()
    cmap.set_bad("#e3e2df")
    mesh = ax.pcolormesh(np.arange(len(n_w_list) + 1), np.arange(len(m_list) + 1),
                         np.ma.masked_invalid(z), cmap=cmap, shading="flat")
    finite = z[np.isfinite(z)]
    mid = (np.nanmax(finite) + np.nanmin(finite)) / 2 if finite.size else 0.0
    for i in range(len(m_list)):
        for j in range(len(n_w_list)):
            if np.isfinite(z[i, j]):
                ax.text(j + 0.5, i + 0.5, f"{z[i, j]:.0f}", ha="center", va="center",
                        fontsize=8, color="white" if z[i, j] > mid else INK)
            else:
                ax.text(j + 0.5, i + 0.5, "diverged", ha="center", va="center",
                        fontsize=6.5, color=INK_2, style="italic")

    ax.set_xticks(np.arange(len(n_w_list)) + 0.5)
    ax.set_xticklabels([str(v) for v in n_w_list])
    ax.set_yticks(np.arange(len(m_list)) + 0.5)
    ax.set_yticklabels([str(v) for v in m_list])
    ax.set_xlabel("W(z) taps  (N_W)", color=INK_2, fontsize=9)
    ax.set_ylabel("S_hat taps  (M)", color=INK_2, fontsize=9)
    ax.set_title(f"Broadband attenuation over both filter lengths\n{case_label}"
                 "   -- grey = FxLMS unstable, S_hat too short",
                 color=INK, fontsize=10.5, loc="left")
    cb = plt.colorbar(mesh, ax=ax)
    cb.set_label("attenuation (dB)", color=INK_2, fontsize=9)
    cb.ax.tick_params(colors=INK_2, labelsize=8)
    ax.tick_params(colors=INK_2, labelsize=9)


def _shat_panel(ax, points, m_list, n_w: int):
    """Attenuation vs S_hat length, real S(z) left intact: the truncation cost."""
    for i, case in enumerate(dict.fromkeys(p.case for p in points)):
        xs = [p.m_shat for p in points if p.case is case]
        ys = [p.atten_db for p in points if p.case is case]
        colour = SERIES[i % len(SERIES)]
        ax.plot(xs, ys, color=colour, linewidth=2.0, marker="o", markersize=5,
                markeredgecolor="white", markeredgewidth=0.8, label=case.label, zorder=3)
        finite = [(x, y) for x, y in zip(xs, ys) if np.isfinite(y)]
        if finite:
            lx, ly = finite[-1]
            ax.annotate(f" {case.label.split('(')[1].rstrip(')')}", (lx, ly),
                        color=INK, fontsize=8, va="center", ha="left", zorder=5)

    ax.set_xscale("log", base=2)
    ax.set_xticks(m_list)
    ax.set_xticklabels([str(v) for v in m_list])
    ax.set_xlabel("S_hat taps  (M)   -- real S(z) left at full length", color=INK_2, fontsize=9)
    ax.set_ylabel("broadband attenuation (dB)", color=INK_2, fontsize=9)
    ax.set_title(f"Cost of truncating the secondary-path estimate  (N_W = {n_w})",
                 color=INK, fontsize=10.5, loc="left")
    ax.set_xlim(m_list[0] * 0.85, m_list[-1] * 2.6)
    _style(ax)


def plot_all(taps_points, shat_points, grid, n_w_list, m_list, shat_n_w,
             grid_case_label: str, path: str = None):
    """The four panels, one figure."""
    fig, axes = plt.subplots(2, 2, figsize=(15.5, 11))
    fig.patch.set_facecolor("#fcfcfb")

    _band_panel(axes[0][0], taps_points, "0-1000Hz", n_w_list,
                "0-1 kHz band: attenuation vs W(z) length")
    _band_panel(axes[0][1], taps_points, "1000-2000Hz", n_w_list,
                "1-2 kHz band: attenuation vs W(z) length")
    _grid_panel(axes[1][0], *grid, grid_case_label)
    _shat_panel(axes[1][1], shat_points, m_list, shat_n_w)

    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, frameon=False,
               fontsize=9.5, bbox_to_anchor=(0.5, 0.005))
    fig.suptitle("FxLMS attenuation vs filter length  "
                 f"(16 kHz, NLMS, {ADAPT_S:g} s adaptation budget, "
                 f"{NOISE_FLOOR_DB:g} dB error-mic floor)\n"
                 "star = best tap count for that configuration",
                 color=INK, fontsize=12.5, y=0.985)
    fig.tight_layout(rect=(0, 0.045, 1, 0.955))

    if path:
        fig.savefig(path, dpi=140, facecolor=fig.get_facecolor())
    return fig


if __name__ == "__main__":
    base = dt.Duct()
    n_w_list = [8, 16, 32, 64, 128, 256, 512]
    m_list = [4, 8, 16, 32, 64, 128, 256]
    grid_n_w = [16, 32, 64, 128, 256, 512]
    grid_m = [4, 8, 16, 32, 64, 128]
    shat_n_w = 128

    print("=" * 96)
    print("Sweep A -- attenuation vs W(z) taps (S_hat near-perfect)")
    print("=" * 96)
    taps_points = sweep_taps(base=base, n_w_list=n_w_list)

    print("\n" + "=" * 96)
    print(f"Sweep B -- attenuation vs S_hat taps, real S(z) intact (N_W = {shat_n_w})")
    print("=" * 96)
    shat_points = sweep_shat(base=base, n_w=shat_n_w, m_list=m_list)

    print("\n" + "=" * 96)
    print(f"Sweep C -- (N_W, M) plane for {DEFAULT_CASES[0].label}")
    print("=" * 96)
    grid = sweep_grid(DEFAULT_CASES[0], base=base, n_w_list=grid_n_w, m_list=grid_m)

    print("\n" + "=" * 96)
    print("Best tap count per configuration")
    print("=" * 96)
    for case in DEFAULT_CASES:
        pts = [p for p in taps_points if p.case is case]
        ok = [p for p in pts if np.isfinite(p.atten_db)]
        if not ok:
            print(f"  {case.label:32} no stable point")
            continue
        best = max(ok, key=lambda p: p.atten_db)
        knee = find_knee([p.n_w for p in pts], [p.atten_db for p in pts])
        print(f"  {case.label:32} best N_W={best.n_w:4d} -> {best.atten_db:6.2f} dB broadband, "
              f"{best.bands.get('0-1000Hz', float('nan')):6.2f} dB @0-1k, "
              f"{best.bands.get('1000-2000Hz', float('nan')):6.2f} dB @1-2k   "
              f"(knee {knee}, tiles {best.tiles}, util {best.utilization*100:.1f}%)")

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "sweep_attenuation.png")
    plot_all(taps_points, shat_points, grid, n_w_list, m_list, shat_n_w,
             DEFAULT_CASES[0].label, path=out)
    print(f"\nwrote {os.path.normpath(out)}")
