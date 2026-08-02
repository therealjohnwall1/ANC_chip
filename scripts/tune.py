import numpy as np
import matplotlib.pyplot as plt

from scripts.ans import lms_anc
from scripts.wiener import autocorrelation


def _input_autocorr_matrix(x: np.ndarray, taps: int) -> np.ndarray:
    """
    taps x taps (normalized) Toeplitz autocorrelation matrix of x, sized to
    match the filter order -- this is the R in the LMS stability bound
    0 < lr < 2 / (taps * P_x), not scripts.wiener's full-signal-length matrix.
    """
    r = np.array([autocorrelation(x, lag) for lag in range(taps)])
    R = np.empty((taps, taps))
    for i in range(taps):
        for j in range(taps):
            R[i, j] = r[abs(i - j)]
    return R


def eigenvalue_spread(x: np.ndarray, taps: int) -> tuple[np.ndarray, float]:
    """
    Eigenvalues of the taps x taps input autocorrelation matrix and their
    spread (lambda_max / lambda_min). Large spread -> uneven convergence
    speed across weight-space directions.
    """
    R = _input_autocorr_matrix(x, taps)
    eigvals = np.linalg.eigvalsh(R)
    eigvals = np.clip(eigvals, 1e-12, None)  # guard tiny/negative numerical noise
    spread = eigvals.max() / eigvals.min()
    return eigvals, spread


def _rolling_mse(e: np.ndarray, window: int) -> np.ndarray:
    window = max(1, min(window, len(e)))
    kernel = np.ones(window) / window
    return np.convolve(e ** 2, kernel, mode="valid")


def _convergence_index(mse_smoothed: np.ndarray, steady_state: float, tol: float) -> int:
    """
    First index after which mse_smoothed stays within `tol`x the steady-state
    floor for the rest of the run (a one-off dip early on doesn't count).
    Returns len(mse_smoothed) if it never settles.
    """
    below = mse_smoothed <= steady_state * tol
    for i in range(len(below)):
        if below[i:].all():
            return i
    return len(mse_smoothed)


def _label(taps: int, lr: float) -> str:
    return f"taps={taps}, lr={lr}"


def _safe_attenuation(x: np.ndarray, res: np.ndarray) -> float:
    """
    calc_attenuation, tolerant of the overflow/zero-power edge cases that
    show up once an intentionally-unstable lr blows the filter up (res -> inf
    makes the ratio 0.0, which calc_attenuation's math.log10 rejects).
    """
    with np.errstate(over="ignore", invalid="ignore"):
        p_before = np.sum(np.abs(x) ** 2)
        p_after = np.sum(np.abs(res) ** 2)
        ratio = p_before / p_after
    if not np.isfinite(ratio) or ratio <= 0:
        return np.nan
    return float(np.log10(ratio))


def tune_anc(
    x_ref: np.ndarray,
    d: np.ndarray,
    taps_list: list[int],
    lr_list: list[float],
    true_path: np.ndarray | None = None,
    window: int = 50,
    tail_frac: float = 0.2,
    conv_tol: float = 1.5,
    normalize: bool = False,
):
    """
    Tune any noise canceling algorithm hyperparameters for optimitizing the following:
    - attenuation (dB)
    - misadjustment
    - convergence time
    - stability margin
    - MSD
    - tracking

    Sweeps every (taps, lr) combo through lms_anc and plots all of the above,
    plus the input autocorrelation matrix's eigenvalue spread, on one figure
    so a tuning choice can be read off directly instead of from the formula
    alone.

    Args:
        x_ref: reference mic signal
        d: noise as it arrives at the error mic (desired/target signal)
        taps_list: filter lengths to sweep
        lr_list: learning rates to sweep
        true_path: ground-truth primary-path FIR coefficients, if known
                   (simulation only) -- enables the MSD plot
        window: samples per rolling attenuation/MSE estimate
        tail_frac: trailing fraction of the run treated as "steady state"
                   for misadjustment/attenuation
        conv_tol: convergence band, as a multiple of the steady-state MSE
        normalize: NLMS mode, passed straight through to lms_anc -- same
                   sweep/metrics/plots, just with per-step normalized lr

    Returns:
        dict keyed by (taps, lr) with the raw per-run metrics/curves
    """
    configs = [(taps, lr) for taps in taps_list for lr in lr_list]
    results = {}

    for taps, lr in configs:
        x_out, d_out, y, e, w_history = lms_anc(x_ref, d, taps, lr, normalize=normalize)
        w_history = np.array(w_history[1:])  # drop initial-zeros entry, align with e

        tail = max(1, int(len(e) * tail_frac))
        steady_state_mse = float(np.mean(e[-tail:] ** 2))
        steady_state_attenuation = _safe_attenuation(d_out[-tail:], e[-tail:])

        mse_smoothed = _rolling_mse(e, window)
        conv_idx = _convergence_index(mse_smoothed, steady_state_mse, conv_tol)

        n_chunks = len(e) // window
        rolling_attenuation = [
            _safe_attenuation(d_out[i * window:(i + 1) * window], e[i * window:(i + 1) * window])
            for i in range(n_chunks)
        ]

        w_norm = np.linalg.norm(w_history, axis=1)
        quarter = max(1, len(w_norm) // 4)
        stable = bool(
            np.all(np.isfinite(w_norm))
            and w_norm[-quarter:].mean() < 5 * max(w_norm[:quarter].mean(), 1e-6)
        )

        msd = None
        if true_path is not None and taps >= len(true_path):
            # zero-pad true_path so w_opt lives in the same taps-dim space;
            # taps < len(true_path) is underparameterized, no valid w_opt to compare to
            w_opt = np.zeros(taps)
            w_opt[: len(true_path)] = true_path
            msd = np.sum((w_history - w_opt) ** 2, axis=1)

        eigvals, spread = eigenvalue_spread(x_ref, taps)

        results[(taps, lr)] = dict(
            e=e,
            mse_smoothed=mse_smoothed,
            rolling_attenuation=rolling_attenuation,
            steady_state_mse=steady_state_mse,
            steady_state_attenuation=steady_state_attenuation,
            conv_idx=conv_idx,
            w_norm=w_norm,
            stable=stable,
            msd=msd,
            eigvals=eigvals,
            spread=spread,
        )

    mode = "NLMS" if normalize else "LMS"
    _plot_tuning_table(results, window=window, mode=mode)

    return results


def _plot_tuning_table(results: dict, window: int, mode: str = "LMS"):
    fig, axes = plt.subplots(2, 4, figsize=(20, 9))
    ax_atten, ax_mse, ax_wnorm, ax_msd = axes[0]
    ax_atten_bar, ax_misadj_bar, ax_conv_bar, ax_eig = axes[1]

    configs = list(results.keys())
    colors = plt.cm.viridis(np.linspace(0, 0.9, max(len(configs), 1)))
    has_msd = any(r["msd"] is not None for r in results.values())

    for (taps, lr), color in zip(configs, colors):
        r = results[(taps, lr)]
        label = _label(taps, lr) + ("" if r["stable"] else " [UNSTABLE]")
        style = "-" if r["stable"] else "--"

        ax_atten.plot(r["rolling_attenuation"], style, label=label, color=color)
        ax_mse.plot(r["mse_smoothed"], style, label=label, color=color)
        ax_mse.axvline(r["conv_idx"], color=color, linestyle=":", alpha=0.5)
        ax_wnorm.plot(r["w_norm"], style, label=label, color=color)

        if r["msd"] is not None:
            ax_msd.plot(r["msd"], style, label=label, color=color)

    ax_atten.set_title("Attenuation over time (rolling)")
    ax_atten.set_xlabel(f"Chunk (window={window} samples)")
    ax_atten.set_ylabel("Attenuation (log10 P_d/P_e)")
    ax_atten.legend(fontsize=7)

    ax_mse.set_title("Learning curve: MSE vs iteration (dotted = convergence pt)")
    ax_mse.set_xlabel("Iteration")
    ax_mse.set_ylabel("MSE (rolling)")
    ax_mse.set_yscale("log")
    ax_mse.legend(fontsize=7)

    ax_wnorm.set_title("Stability margin: ||w_n||")
    ax_wnorm.set_xlabel("Iteration")
    ax_wnorm.set_ylabel("||w_n||")
    ax_wnorm.set_yscale("log")
    ax_wnorm.legend(fontsize=7)

    if has_msd:
        ax_msd.set_title("MSD: ||w_n - w_opt||^2")
        ax_msd.set_xlabel("Iteration")
        ax_msd.set_ylabel("MSD")
        ax_msd.set_yscale("log")
        ax_msd.legend(fontsize=7)
    else:
        ax_msd.set_title("MSD (pass true_path to enable)")
        ax_msd.axis("off")

    x_pos = np.arange(len(configs))
    labels = [_label(t, lr) for t, lr in configs]

    ax_atten_bar.bar(x_pos, [results[c]["steady_state_attenuation"] for c in configs], color=colors)
    ax_atten_bar.set_title("Steady-state attenuation (tail avg)")
    ax_atten_bar.set_xticks(x_pos)
    ax_atten_bar.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax_atten_bar.set_ylabel("Attenuation (log10 P_d/P_e)")

    ax_misadj_bar.bar(x_pos, [results[c]["steady_state_mse"] for c in configs], color=colors)
    ax_misadj_bar.set_title("Misadjustment (steady-state MSE)")
    ax_misadj_bar.set_xticks(x_pos)
    ax_misadj_bar.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax_misadj_bar.set_ylabel("MSE")
    ax_misadj_bar.set_yscale("log")

    ax_conv_bar.bar(x_pos, [results[c]["conv_idx"] for c in configs], color=colors)
    ax_conv_bar.set_title("Convergence time")
    ax_conv_bar.set_xticks(x_pos)
    ax_conv_bar.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax_conv_bar.set_ylabel("Iterations")

    seen_taps = {}
    for taps, lr in configs:
        seen_taps.setdefault(taps, results[(taps, lr)]["eigvals"])

    for taps, eigvals in seen_taps.items():
        spread = eigvals.max() / eigvals.min()
        ax_eig.plot(np.sort(eigvals)[::-1], marker="o", label=f"taps={taps} (spread={spread:.1f})")

    ax_eig.set_title("Eigenvalue spread of R (input autocorrelation)")
    ax_eig.set_xlabel("Eigenvalue index (sorted desc.)")
    ax_eig.set_ylabel("Eigenvalue")
    ax_eig.set_yscale("log")
    ax_eig.legend(fontsize=7)

    fig.suptitle(f"ANC tuning sweep: taps x lr ({mode})", fontsize=14)
    plt.tight_layout()
    plt.show()
