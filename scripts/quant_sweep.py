"""
Step 2 of fixed-point sizing: what does the coefficient grid actually cost?

range_check.py bounded this analytically -- filtering noise wants ~9 fractional
bits, the adaptation stall wants ~12, so Q1.15 should be clear with margin.
This measures it instead of predicting it: run the same FxLMS configuration at
a range of coefficient widths and read the attenuation off the far end.

Only w[] is quantized. s_hat[] stays float64 on purpose -- truncating it is a
separate effect that sweep.py already prices (as M), and quantizing both at
once would leave the two indistinguishable in the result.

The signal is normalized to |x| <= 1 first. generate_noise emits a unit sine
plus sigma=0.7 gaussian, so it peaks near 3.5; feeding that to a Q1.x grid
would model an ADC clipping on every peak. Normalizing x and d by the same
factor leaves w[] untouched (it converges to a ratio between them), so this
rescales the experiment without moving the thing being measured.

Run from the repo root:  python -m scripts.quant_sweep
"""

import math

import numpy as np

from scripts.ans import lms_anc
from scripts.gen_noise import generate_noise
from scripts.tune import band_attenuation
from scripts.audio_sim import duct as dt
from scripts.audio_sim.sweep import (Case, ADAPT_S, NOISE_FLOOR_DB, BANDS,
                                     BAND_LABELS, _paths)

# Fixed rather than searched over LR_LADDER: the question is what the
# coefficient width costs, and letting lr move between points would fold
# step-size tuning into the answer.
LR = 0.10

FRAC_BITS = (8, 10, 11, 12, 13, 14, 15, 16, 18, 20, 24)

ROWS = [
    ("I", Case("L=0.3 m, g=0.1  (short+damped)", 0.30, 0.10), 32, 32),
    ("G", Case("L=0.3 m, g=0.5  (short)",        0.30, 0.50), 64, 64),
]


def run(case: Case, n_w: int, m_shat: int, base: dt.Duct, coef_quant,
        seed: int = 0, f0: float = 300.0, a_tone: float = 1.0,
        sig: float = 0.7, tail_frac: float = 0.34) -> dict:
    """One FxLMS run at one coefficient width. coef_quant=None -> float64."""
    config = case.duct(base)
    s_taps, p_taps = _paths(config, case.g)
    s_hat = s_taps[:max(1, m_shat)]
    n = int(ADAPT_S * config.fs)

    np.random.seed(seed)
    x = generate_noise(config.fs, a_tone, sig, f0, n)
    d = np.convolve(x, p_taps)[:n]
    rms = float(np.sqrt(np.mean(d ** 2)))
    d = d + np.random.normal(0.0, rms * 10.0 ** (NOISE_FLOOR_DB / 20.0), n)

    # Full-scale normalization -- see module docstring.
    scale = max(float(np.max(np.abs(x))), float(np.max(np.abs(d))))
    x, d = x / scale, d / scale

    with np.errstate(over="ignore", invalid="ignore"):
        _, d_out, _, e, w_hist = lms_anc(x, d, n_w, LR, s_taps=s_taps,
                                         s_hat_taps=s_hat, normalize=True,
                                         keep_history=False,
                                         coef_quant=coef_quant)

    tail = max(1, int(len(e) * tail_frac))
    d_t, e_t = d_out[-tail:], e[-tail:]
    with np.errstate(over="ignore", invalid="ignore"):
        p_b, p_a = float(np.sum(d_t ** 2)), float(np.sum(e_t ** 2))
        atten = (10.0 * math.log10(p_b / p_a)
                 if p_a > 0 and p_b > 0 and np.isfinite(p_a) else float("nan"))

    stable = bool(np.all(np.isfinite(e_t))) and np.isfinite(atten)
    bands = (band_attenuation(d_t, e_t, config.fs, BANDS) if stable
             else {k: float("nan") for k in BAND_LABELS})

    w = w_hist[-1]
    return {"atten": atten, "bands": bands,
            "n_nonzero": int(np.sum(w != 0.0)), "n_w": n_w}


if __name__ == "__main__":
    base = dt.Duct()
    print("=" * 78)
    print(f"Coefficient width sweep -- w[] quantized, s_hat float, "
          f"lr={LR}, {ADAPT_S:g}s adapt")
    print("=" * 78)

    for tag, case, n_w, m in ROWS:
        print(f"\nrow {tag}:  {case.label}   N_W={n_w}  M={m}")
        ref = run(case, n_w, m, base, None)
        print(f"  {'COEF_W':>7}  {'0-1kHz':>8}  {'1-2kHz':>8}  "
              f"{'broadband':>10}  {'vs float':>9}  {'live taps':>10}")
        print(f"  {'float64':>7}  {ref['bands'][BAND_LABELS[0]]:8.2f}  "
              f"{ref['bands'][BAND_LABELS[1]]:8.2f}  {ref['atten']:10.2f}  "
              f"{'--':>9}  {ref['n_nonzero']:>4}/{n_w:<5}")

        for f_bits in FRAC_BITS:
            r = run(case, n_w, m, base, f_bits)
            loss = r["atten"] - ref["atten"]
            flag = "" if loss > -0.5 else ("   <-- costs dB" if loss > -3.0
                                           else "   <-- broken")
            print(f"  {'Q1.'+str(f_bits):>7}  {r['bands'][BAND_LABELS[0]]:8.2f}  "
                  f"{r['bands'][BAND_LABELS[1]]:8.2f}  {r['atten']:10.2f}  "
                  f"{loss:+9.2f}  {r['n_nonzero']:>4}/{r['n_w']:<5}{flag}")
