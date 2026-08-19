import math

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from scripts.ans import lms_anc
from scripts.gen_noise import generate_noise
from scripts.audio_sim import duct as dt
from scripts.audio_sim import spans as sp
from scripts.audio_sim.sweep import Case, LR_LADDER, ADAPT_S, NOISE_FLOOR_DB, _paths


ROWS = [
    ("I", Case("L=0.3 m, g=0.1  (short+damped)", 0.30, 0.10), 32, 32),
    ("G", Case("L=0.3 m, g=0.5  (short)",        0.30, 0.50), 64, 64),
]


def int_bits(peak: float) -> int:
    """Integer bits (excluding sign) needed to hold `peak` without saturating."""
    if peak <= 0.0 or not np.isfinite(peak):
        return 0
    return max(0, math.ceil(math.log2(peak)))


def q_format(peak: float, total_bits: int) -> str:
    i = int_bits(peak)
    return f"Q{i + 1}.{total_bits - 1 - i}"


def accumulator_peak(taps: np.ndarray, signal: np.ndarray) -> float:
    """
    Largest partial sum seen while MACing `taps` across every window of `signal`.

    Mirrors the hardware: products are accumulated one tap at a time, so the
    quantity that must not overflow is max|cumsum|, not max|dot|.
    """
    n = len(taps)
    if len(signal) < n:
        return 0.0
    windows = sliding_window_view(signal, n)[:, ::-1]
    partials = np.cumsum(windows * taps, axis=1)
    return float(np.max(np.abs(partials)))


def measure(case: Case, n_w: int, m_shat: int, base: dt.Duct,
            seed: int = 0, f0: float = 300.0, a_tone: float = 1.0,
            sig: float = 0.7, tail_frac: float = 0.34) -> dict:
    """One converged FxLMS run, reported as ranges rather than attenuation."""
    config = case.duct(base)
    s_taps, p_taps = _paths(config, case.g)
    s_hat = s_taps[:max(1, m_shat)]
    n = int(ADAPT_S * config.fs)

    best = None
    for lr in LR_LADDER:
        np.random.seed(seed)
        x = generate_noise(config.fs, a_tone, sig, f0, n)
        d = np.convolve(x, p_taps)[:n]
        rms = float(np.sqrt(np.mean(d ** 2)))
        d = d + np.random.normal(0.0, rms * 10.0 ** (NOISE_FLOOR_DB / 20.0), n)

        with np.errstate(over="ignore", invalid="ignore"):
            _, d_out, y, e, w_hist = lms_anc(x, d, n_w, lr, s_taps=s_taps,
                                             s_hat_taps=s_hat, normalize=True,
                                             keep_history=False)

        tail = max(1, int(len(e) * tail_frac))
        d_t, e_t = d_out[-tail:], e[-tail:]
        with np.errstate(over="ignore", invalid="ignore"):
            p_b, p_a = float(np.sum(d_t ** 2)), float(np.sum(e_t ** 2))
            atten = (10.0 * math.log10(p_b / p_a)
                     if p_a > 0 and p_b > 0 and np.isfinite(p_a) else float("nan"))
        if not (np.all(np.isfinite(e_t)) and np.isfinite(atten) and atten > -3.0):
            continue
        if best is None or atten > best["atten"]:
            best = {"atten": atten, "lr": lr, "w": w_hist[-1], "x": x, "y": y}

    if best is None:
        raise RuntimeError(f"no stable run for {case.label} at N_W={n_w}, M={m_shat}")

    w = best["w"]
    x_filt = np.convolve(best["x"], s_hat)[:len(best["x"])]

    return {
        "case": case, "n_w": n_w, "m": m_shat,
        "atten": best["atten"], "lr": best["lr"],
        "max_w": float(np.max(np.abs(w))),
        "max_shat": float(np.max(np.abs(s_hat))),
        "max_x": float(np.max(np.abs(best["x"]))),
        "max_y": float(np.max(np.abs(best["y"]))),
        "acc_w": accumulator_peak(w, best["x"]),
        "acc_s": accumulator_peak(s_hat, best["x"]),
        "sum_abs_w": float(np.sum(np.abs(w))),
    }


def report(r: dict) -> None:
    print(f"  N_W={r['n_w']}  M={r['m']}  lr={r['lr']:.2f}  "
          f"broadband={r['atten']:.2f} dB")
    print(f"    max|x[n]|        {r['max_x']:10.5f}   (ADC input, expected < 1)")
    print(f"    max|y[n]|        {r['max_y']:10.5f}   (DAC output)")
    print(f"    max|w[k]|        {r['max_w']:10.5f}   -> needs {int_bits(r['max_w'])} int bits, "
          f"{q_format(r['max_w'], 24)} at 24-bit")
    print(f"    max|s_hat[k]|    {r['max_shat']:10.5f}   -> needs {int_bits(r['max_shat'])} int bits, "
          f"{q_format(r['max_shat'], 24)} at 24-bit")
    print(f"    sum|w[k]|        {r['sum_abs_w']:10.5f}   (worst-case filter gain bound)")
    print(f"    max partial W.x  {r['acc_w']:10.5f}   -> needs {int_bits(r['acc_w'])} int bits")
    print(f"    max partial S.x  {r['acc_s']:10.5f}   -> needs {int_bits(r['acc_s'])} int bits")

    over = [n for n, v in (("w", r["max_w"]), ("s_hat", r["max_shat"])) if v >= 1.0]
    if over:
        print(f"    !! {', '.join(over)} exceeds +/-1 -- Q1.x would saturate, need an integer bit")
    else:
        print(f"    OK  both coefficient arrays fit in Q1.x")


def stall_check(case: Case, n_w: int, m_shat: int, base: dt.Duct, lr: float,
                seed: int = 0, f0: float = 300.0, a_tone: float = 1.0,
                sig: float = 0.7, tail_frac: float = 0.34) -> dict:
    """
    How small does the LMS weight update get once converged?

    An update smaller than half an LSB of the coefficient register rounds to
    zero, the weight stops moving, and adaptation freezes short of convergence.
    So the smallest USEFUL update sets the fractional-bit floor for COEF_W --
    a separate and usually tighter requirement than representing w[k] itself.

    delta_w is recovered by diffing the weight history rather than recomputing
    lr*error*x_filt, so it is exactly what lms_anc did, with no second
    implementation to drift.

    NLMS makes this measurement scale-invariant: step_lr carries 1/||x_f||^2,
    error carries one factor of the signal scale and x_filt another, so the
    scale cancels. The un-normalized amplitude out of generate_noise therefore
    does not bias the answer.
    """
    config = case.duct(base)
    s_taps, p_taps = _paths(config, case.g)
    s_hat = s_taps[:max(1, m_shat)]
    n = int(ADAPT_S * config.fs)

    np.random.seed(seed)
    x = generate_noise(config.fs, a_tone, sig, f0, n)
    d = np.convolve(x, p_taps)[:n]
    rms = float(np.sqrt(np.mean(d ** 2)))
    d = d + np.random.normal(0.0, rms * 10.0 ** (NOISE_FLOOR_DB / 20.0), n)

    _, _, _, e, w_hist = lms_anc(x, d, n_w, lr, s_taps=s_taps, s_hat_taps=s_hat,
                                 normalize=True, keep_history=True)

    w_arr = np.asarray(w_hist)
    deltas = np.abs(np.diff(w_arr, axis=0))        # |delta_w| per sample, per tap
    tail = max(1, int(len(deltas) * tail_frac))
    conv = deltas[-tail:]                          # converged portion only

    flat = conv.ravel()
    flat = flat[flat > 0.0]
    per_tap_rms = np.sqrt(np.mean(conv ** 2, axis=0))

    return {
        "case": case, "n_w": n_w, "lr": lr,
        "pcts": {p: float(np.percentile(flat, p)) for p in (10, 50, 90)},
        "max": float(np.max(flat)),
        "per_tap_rms": per_tap_rms,
        "min_tap_rms": float(np.min(per_tap_rms)),
        "med_tap_rms": float(np.median(per_tap_rms)),
    }


def report_stall(r: dict, frac_bits=(11, 13, 15, 17, 19, 21, 23)) -> None:
    print(f"  N_W={r['n_w']}  lr={r['lr']:.2f}   |delta_w| once converged:")
    print(f"    p10 {r['pcts'][10]:.3e}   p50 {r['pcts'][50]:.3e}   "
          f"p90 {r['pcts'][90]:.3e}   max {r['max']:.3e}")
    print(f"    per-tap RMS: min {r['min_tap_rms']:.3e}  median {r['med_tap_rms']:.3e}")
    print()
    print(f"    {'F':>3}  {'Q1.F':>7}  {'half LSB':>10}  {'% updates':>10}  {'taps below':>11}")
    print(f"    {'':>3}  {'':>7}  {'':>10}  {'-> 0':>10}  {'half LSB':>11}")
    for f_bits in frac_bits:
        half_lsb = 2.0 ** -(f_bits + 1)
        n_below = int(np.sum(r["per_tap_rms"] < half_lsb))
        # fraction of individual updates that round away, from the percentiles
        upd = ("<10" if half_lsb < r["pcts"][10] else
               "10-50" if half_lsb < r["pcts"][50] else
               "50-90" if half_lsb < r["pcts"][90] else ">90")
        flag = "" if n_below == 0 else "   <-- taps freeze"
        print(f"    {f_bits:>3}  {'Q1.'+str(f_bits):>7}  {half_lsb:>10.3e}  "
              f"{upd:>10}  {n_below:>4}/{len(r['per_tap_rms']):<6}{flag}")


if __name__ == "__main__":
    base = dt.Duct()
    print("=" * 74)
    print(f"Range check -- float64, {ADAPT_S:g}s adaptation, "
          f"{NOISE_FLOOR_DB:g} dB error-mic floor")
    print("=" * 74)
    results = []
    for tag, case, n_w, m in ROWS:
        print(f"\nrow {tag}:  {case.label}")
        r = measure(case, n_w, m, base)
        report(r)
        results.append((tag, r))

    print("\n" + "=" * 74)
    print("Implied widths")
    print("=" * 74)
    peak_coef = max(max(r["max_w"], r["max_shat"]) for _, r in results)
    peak_acc = max(max(r["acc_w"], r["acc_s"]) for _, r in results)
    print(f"  worst coefficient across rows : {peak_coef:.5f}  -> {int_bits(peak_coef)} integer bits")
    print(f"  worst partial sum across rows : {peak_acc:.5f}  -> {int_bits(peak_acc)} integer bits")
    print(f"\n  COEF_W = Q{int_bits(peak_coef)+1}.x  (x = fractional bits, set by step 2)")
    print(f"  ACC_W  needs {int_bits(peak_acc)} integer bits above the "
          f"full-precision product's fraction")

    print("\n" + "=" * 74)
    print("Stall bound -- how many fractional bits before updates round to zero")
    print("=" * 74)
    for tag, r in results:
        print(f"\nrow {tag}:  {r['case'].label}")
        report_stall(stall_check(r["case"], r["n_w"], r["m"], base, r["lr"]))
