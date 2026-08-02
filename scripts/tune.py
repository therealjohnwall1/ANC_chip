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


def fft_spectrum(x: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """
    One-sided magnitude spectrum of x, in dB (20*log10|X(f)|, floor-clipped).
    """
    n = len(x)
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    mag_db = 20 * np.log10(np.clip(np.abs(spectrum) / n, 1e-12, None))
    return freqs, mag_db


def band_attenuation(
    d: np.ndarray, e: np.ndarray, fs: float, bands: list[tuple[float, float]]
) -> dict[str, float]:
    """
    Attenuation (same log10 power-ratio convention as calc_attenuation),
    computed separately per frequency band instead of over the whole signal
    -- overall attenuation can hide poor cancellation in a specific band
    (e.g. strong below 300 Hz, weak above 1 kHz).

    Args:
        d: noise before cancellation
        e: residual after cancellation
        fs: sampling rate, Hz
        bands: (low_hz, high_hz) edges, e.g. [(0, 500), (500, np.inf)]
    """
    n = min(len(d), len(e))
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    D = np.fft.rfft(d[:n])
    E = np.fft.rfft(e[:n])

    result = {}
    for low, high in bands:
        mask = (freqs >= low) & (freqs < high)
        label = f"{low:g}-{high:g}Hz" if np.isfinite(high) else f">{low:g}Hz"

        with np.errstate(over="ignore", invalid="ignore"):
            p_before = np.sum(np.abs(D[mask]) ** 2)
            p_after = np.sum(np.abs(E[mask]) ** 2)
            ratio = p_before / p_after

        result[label] = float(np.log10(ratio)) if np.isfinite(ratio) and ratio > 0 else np.nan

    return result


def plot_fft_comparison(ax, d: np.ndarray, e_by_label: dict[str, np.ndarray], fs: float, colors=None):
    """
    Overlay the reference noise's spectrum against each config's residual
    spectrum -- shows which frequencies actually got cancelled (real ANC
    doesn't cancel all frequencies equally), not just the aggregate dB number.
    """
    freqs_d, d_db = fft_spectrum(d, fs)
    ax.plot(freqs_d, d_db, color="black", linewidth=1.5, label="d (before)")

    colors = colors if colors is not None else [None] * len(e_by_label)
    for (label, e), color in zip(e_by_label.items(), colors):
        freqs_e, e_db = fft_spectrum(e, fs)
        ax.plot(freqs_e, e_db, color=color, alpha=0.8, linewidth=0.9, label=f"e ({label})")

    ax.set_title("Spectrum: before (d) vs after (e)")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Magnitude (dB)")
    ax.legend(fontsize=6)


def plot_spectrograms(d: np.ndarray, e: np.ndarray, fs: float, title: str = ""):
    """
    Time+frequency view of d vs e -- shows how cancellation evolves as the
    filter converges, not just the converged end state a single FFT gives.

    Kept out of the sweep table on purpose: a spectrogram is a 2D-per-run
    plot and can't be overlaid across configs the way the other panels are,
    so tune_anc calls this once per selected config as its own figure.
    """
    fig, (ax_d, ax_e) = plt.subplots(1, 2, figsize=(14, 5), sharex=True, sharey=True)

    ax_d.specgram(d, Fs=fs, cmap="magma")
    ax_d.set_title(f"d (before) {title}")
    ax_d.set_xlabel("Time (s)")
    ax_d.set_ylabel("Frequency (Hz)")

    _, _, _, im = ax_e.specgram(e, Fs=fs, cmap="magma")
    ax_e.set_title(f"e (after) {title}")
    ax_e.set_xlabel("Time (s)")

    fig.colorbar(im, ax=[ax_d, ax_e], label="Power (dB)")
    fig.suptitle(f"Spectrogram: before vs after cancellation {title}", fontsize=13)
    plt.show()


def tune_anc(
    x_ref: np.ndarray,
    d: np.ndarray,
    fs: float,
    taps_list: list[int],
    lr_list: list[float],
    true_path: np.ndarray | None = None,
    window: int = 50,
    tail_frac: float = 0.2,
    conv_tol: float = 1.5,
    normalize: bool = False,
    bands: list[tuple[float, float]] | None = None,
    spectrogram_configs: list[tuple[int, float]] | None = None,
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
    plus the input autocorrelation matrix's eigenvalue spread, before/after
    FFT spectra, and band-limited attenuation, on one figure so a tuning
    choice can be read off directly instead of from the formula alone.
    Spectrograms (time+frequency) are plotted separately per selected config,
    since a 2D-per-run plot can't overlay across configs like the rest can.

    Args:
        x_ref: reference mic signal
        d: noise as it arrives at the error mic (desired/target signal)
        fs: sampling rate, Hz -- needed to label the spectral plots in Hz
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
        bands: frequency bands for band_attenuation, defaults to
               [(0, 500), (500, fs/2)] (below/above 500 Hz)
        spectrogram_configs: which (taps, lr) configs to render spectrograms
                              for; defaults to just the best stable config

    Returns:
        dict keyed by (taps, lr) with the raw per-run metrics/curves
    """
    bands = bands if bands is not None else [(0, 500), (500, fs / 2)]
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
        band_atten = band_attenuation(d_out, e, fs, bands)

        results[(taps, lr)] = dict(
            e=e,
            d_out=d_out,
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
            band_atten=band_atten,
        )

    mode = "NLMS" if normalize else "LMS"
    _plot_tuning_table(results, d, fs, window=window, mode=mode)

    if spectrogram_configs is None:
        spectrogram_configs = [_best_stable_config(results)]

    for taps, lr in spectrogram_configs:
        r = results[(taps, lr)]
        plot_spectrograms(r["d_out"], r["e"], fs, title=f"[{mode}, {_label(taps, lr)}]")

    return results


def _best_stable_config(results: dict) -> tuple[int, float]:
    stable = {c: r for c, r in results.items() if r["stable"]}
    pool = stable or results

    def score(c):
        val = pool[c]["steady_state_attenuation"]
        return val if np.isfinite(val) else -np.inf

    return max(pool, key=score)


def _plot_tuning_table(results: dict, d: np.ndarray, fs: float, window: int, mode: str = "LMS"):
    fig, axes = plt.subplots(3, 4, figsize=(20, 13))
    ax_atten, ax_mse, ax_wnorm, ax_msd = axes[0]
    ax_atten_bar, ax_misadj_bar, ax_conv_bar, ax_eig = axes[1]
    ax_fft, ax_band, ax_spare1, ax_spare2 = axes[2]
    ax_spare1.axis("off")
    ax_spare2.axis("off")

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

    e_by_label = {_label(t, lr): results[(t, lr)]["e"] for t, lr in configs}
    plot_fft_comparison(ax_fft, d, e_by_label, fs, colors=colors)

    band_labels = list(next(iter(results.values()))["band_atten"].keys())
    n_bands = len(band_labels)
    width = 0.8 / n_bands
    for bi, band_label in enumerate(band_labels):
        values = [results[c]["band_atten"][band_label] for c in configs]
        offset = (bi - (n_bands - 1) / 2) * width
        ax_band.bar(x_pos + offset, values, width=width, label=band_label)

    ax_band.set_title("Band-limited attenuation")
    ax_band.set_xticks(x_pos)
    ax_band.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax_band.set_ylabel("Attenuation (log10 P_d/P_e)")
    ax_band.legend(fontsize=7)

    fig.suptitle(f"ANC tuning sweep: taps x lr ({mode})", fontsize=14)
    plt.tight_layout()
    plt.show()
