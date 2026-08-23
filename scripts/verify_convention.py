"""
Does the streaming form of FxLMS actually equal the batch form in ans.py?

`lms_anc` precomputes the filtered reference over the whole signal before its
loop starts:

    x_filt = np.convolve(x, s_hat_taps)[:len(x)]

Hardware cannot. It has to build x_f[n] out of a tap line, one sample at a
time, which means someone has to restate the algorithm with explicit indices --
and the indices are not symmetric:

    x_f[n] = sum(k=0..N_S-1) s_hat[k] * x[n-k]      <-- INCLUDES x[n]
    y[n]   = sum(k=0..N_W-1) w[k]     * x[n-1-k]    <-- EXCLUDES x[n]
    w[k]  += mu[n] * e[n] * x_f[n-1-k]              <-- W offset, x_f signal

y[n] drops the current sample because `lms_anc` slices `x[i-taps:i]`, which
stops one short of i. x_f[n] keeps it because np.convolve at index n runs
k=0..N_S-1 over x[n-k]. Three windows, two different offsets, and none of it
is visible in ans.py because the convolve is precomputed.

An off-by-one there is indistinguishable from a quantization effect once the
fixed-point model lands -- both show up as "golden disagrees with ans.py by a
little". So it gets pinned down here, at float64, where the answer is either
exact to machine epsilon or wrong.

This is the executable form of section 3 of golden/README.md. It models no
fixed point at all; it exists to prove the index convention, nothing else.

Run from the repo root:  python -m scripts.verify_convention
"""

import numpy as np

from scripts.ans import lms_anc

# float64 dot products reassociate between numpy and a Python loop, so exact
# equality is the wrong bar. Anything above a few ULP is a real disagreement.
TOL = 1e-12


def streaming_fxlms(x, d, n_w, lr, s_taps, s_hat, normalize=True, eps=0.00001):
    """
    FxLMS the way the chip runs it: tap lines, one sample at a time.

    Everything touching s_taps is plant, not chip -- the real secondary path
    only exists here because something has to stand in for the acoustics. On
    silicon e[n] arrives from the error-mic ADC with S(z) already applied.

    Returns (y, e, w_history), aligned the same way lms_anc aligns them: the
    first n_w samples are warm-up and produce no output.
    """
    n_s = len(s_hat)

    x_line = np.zeros(max(n_w + 1, n_s))   # x_line[k]  = x[n-k]
    xf_line = np.zeros(n_w + 1)            # xf_line[k] = x_f[n-k]
    y_line = np.zeros(len(s_taps))         # plant: past y, for the real S(z)

    w = np.zeros(n_w)
    y_out, e_out, w_hist = [], [], [w.copy()]

    for i in range(len(x)):
        x_line = np.roll(x_line, 1)
        x_line[0] = x[i]

        # S_hat FIR -- reads index 0, i.e. the sample that just arrived
        x_f = sum(s_hat[k] * x_line[k] for k in range(n_s))
        xf_line = np.roll(xf_line, 1)
        xf_line[0] = x_f

        # Tap line is not full yet, nothing valid to emit. Same samples
        # lms_anc skips by starting its loop at i = taps.
        if i < n_w:
            continue

        # W FIR -- reads indices 1..n_w, one sample behind the S_hat window.
        # Uses the weights held on entry; the update below is what makes this
        # a read-before-write hazard for any overlapped MAC schedule.
        y = sum(w[k] * x_line[k + 1] for k in range(n_w))

        # ---- plant, not chip -------------------------------------------
        y_line = np.roll(y_line, 1)
        y_line[0] = y
        e = d[i] - s_taps @ y_line
        # ----------------------------------------------------------------

        xf_win = np.array([xf_line[k + 1] for k in range(n_w)])

        step = lr
        if normalize:
            step = lr / (xf_win @ xf_win + eps)

        w = w + step * e * xf_win

        y_out.append(y)
        e_out.append(e)
        w_hist.append(w.copy())

    return np.array(y_out), np.array(e_out), np.array(w_hist)


def compare(label, n_w, n_s, normalize, n=200, seed=7):
    """Run both forms on the same inputs and report the worst disagreement."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    d = rng.standard_normal(n)

    # Real S(z) longer than the estimate, so the truncation that sweep.py
    # prices as M is exercised rather than assumed away.
    s_taps = rng.standard_normal(n_s + 2) * 0.3
    s_hat = s_taps[:n_s]

    lr = 0.1 if normalize else 0.01

    _, _, y_ref, e_ref, w_ref = lms_anc(x, d, n_w, lr, s_taps=s_taps,
                                        s_hat_taps=s_hat, normalize=normalize,
                                        keep_history=True)
    y_s, e_s, w_s = streaming_fxlms(x, d, n_w, lr, s_taps, s_hat,
                                    normalize=normalize)

    assert len(y_s) == len(y_ref), f"{label}: {len(y_s)} steps vs {len(y_ref)}"

    d_y = float(np.max(np.abs(y_s - y_ref)))
    d_e = float(np.max(np.abs(e_s - e_ref)))
    d_w = float(np.max(np.abs(w_s - np.asarray(w_ref))))
    worst = max(d_y, d_e, d_w)

    ok = "ok" if worst < TOL else "MISMATCH"
    print(f"  {label:<28} {d_y:10.2e} {d_e:10.2e} {d_w:10.2e}   {ok}")
    return worst


CASES = [
    #  label                       N_W  N_S  normalize
    ("NLMS  N_W=4   N_S=3",          4,   3,  True),
    ("NLMS  N_W=32  N_S=32",        32,  32,  True),   # config I
    ("NLMS  N_W=64  N_S=64",        64,  64,  True),   # config G
    ("NLMS  N_W=32  N_S=8",         32,   8,  True),   # S_hat shorter than W
    ("NLMS  N_W=8   N_S=32",         8,  32,  True),   # S_hat longer than W
    ("LMS   N_W=32  N_S=32",        32,  32,  False),
]


if __name__ == "__main__":
    print("=" * 74)
    print("Streaming (tap-line) FxLMS vs batch lms_anc -- float64, index check")
    print("=" * 74)
    print(f"  {'case':<28} {'max|dy|':>10} {'max|de|':>10} {'max|dw|':>10}")

    worst = max(compare(*c) for c in CASES)

    print()
    print(f"  worst disagreement across cases: {worst:.2e}   (tol {TOL:.0e})")
    if worst < TOL:
        print("  index convention in golden/README.md section 3 is confirmed")
    else:
        raise SystemExit("streaming form does NOT match ans.py -- fix before "
                         "building on this convention")
