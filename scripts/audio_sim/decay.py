from dataclasses import dataclass
import numpy as np


_EPS = 1e-300

_ARRIVAL_FRAC = 0.05


def first_arrival(ir: np.ndarray, frac: float = _ARRIVAL_FRAC) -> int:
    """
    First sample where |ir| crosses `frac` of its peak.

    Not argmax: the direct arrival is only the loudest arrival while the direct
    path is the shortest one. Once a reflection path gets close in length to the
    direct path the two can swap, and argmax silently starts reporting an echo.
    """
    peak = np.max(np.abs(ir))
    if peak <= 0.0:
        return 0
    above = np.flatnonzero(np.abs(ir) >= frac * peak)
    return int(above[0]) if above.size else 0


def _last_above(ir: np.ndarray, threshold_db: float):
    """
    Index of the last sample within `threshold_db` of the peak, or None.

    Sparse-train safe: it tracks the last *arrival* that loud, so it does not
    care whether the gaps between reflections happen to line up with any
    particular analysis window.
    """
    peak = np.max(np.abs(ir)) if ir.size else 0.0
    if peak <= 0.0:
        return None
    above = np.flatnonzero(np.abs(ir) >= peak * 10.0 ** (threshold_db / 20.0))
    return int(above[-1]) if above.size else None


def energy_decay_curve(ir: np.ndarray, start: int = 0) -> np.ndarray:
    """
    Schroeder backward energy integral, in dB, normalized to 0 dB at `start`.

    edc[n] = 10*log10( sum_{k>=n} ir[k]^2 / sum_{k>=start} ir[k]^2 )

    Monotonically decreasing by construction, which is the whole point. A duct
    response is a train of discrete reflections, so the raw envelope crosses any
    threshold over and over and "the" crossing time is ambiguous; the backward
    integral crosses exactly once.
    """
    e = np.asarray(ir[start:], dtype=float) ** 2
    remaining = np.cumsum(e[::-1])[::-1]
    total = remaining[0] if remaining.size else 0.0
    if total <= 0.0:
        return np.full(e.shape, -np.inf)
    return 10.0 * np.log10(np.maximum(remaining / total, _EPS))


def taps_from_tau(tau_s: float, fs: float) -> int:
    """
    Seconds -> taps, using budget_model.budget()'s exact rounding.

    Deliberately duplicated convention: if these two ever round differently the
    span this module reports and the array budget_model sizes disagree by a tap,
    which is the kind of thing that costs an afternoon.
    """
    return max(1, int(round(tau_s * fs)))


@dataclass
class Decay:
    """What a decay threshold says about one impulse response."""

    threshold_db: float
    fs: float
    arrival: int  # taps of pure propagation delay before anything happens
    ring_taps: int  # arrival -> threshold, the enclosure's ring-down
    span_taps: int  # 0 -> threshold, what an FIR has to store
    crossed: bool  # False => the ir ran out before the threshold was met

    @property
    def ring_s(self) -> float:
        return self.ring_taps / self.fs

    @property
    def span_s(self) -> float:
        return self.span_taps / self.fs

    def __str__(self) -> str:
        warn = "" if self.crossed else "  !! ir too short, tau is a lower bound"
        return (
            f"{self.threshold_db:+.0f} dB: arrival {self.arrival:4d} taps, "
            f"ring {self.ring_taps:5d} taps ({self.ring_s * 1e3:6.2f} ms), "
            f"span {self.span_taps:5d} taps ({self.span_s * 1e3:6.2f} ms){warn}"
        )


def measure(ir: np.ndarray, fs: float, threshold_db: float = -40.0) -> Decay:
    """
    Decay of `ir` at one threshold, via the Schroeder energy decay curve.

    Args:
        ir: impulse response
        fs: sample rate, Hz
        threshold_db: how far the energy must fall before the tail is declared
            unmodeled. -40 dB is cheaper than -60 dB in taps, and the difference
            is exactly the truncation error that shows up as S_hat phase
            mismatch -- sweep it rather than guessing it.

    Returns:
        Decay. `crossed` is False when the response was truncated before the
        threshold was reached, in which case both durations are lower bounds.
    """
    arrival = first_arrival(ir)
    edc = energy_decay_curve(ir, start=arrival)

    # The EDC normalizes by the energy *present in the buffer*, so it always
    # nosedives to -inf on the last sample and therefore always crosses any
    # threshold -- even for a response chopped off mid-ring. The crossing only
    # means something once the response has actually decayed that far, which is
    # a question about the raw envelope at the end of the buffer, not the EDC.
    #
    # Ask it as an energy ratio between the last quarter of the buffer and the
    # first quarter after the arrival. Quarters, not a narrow tail window: gaps
    # between reflections run to tens of samples, so any window shorter than
    # the arrival spacing can land entirely inside a gap and report silence for
    # a response that is still ringing hard.
    win = max(1, len(ir) // 4)
    head_e = float(np.sum(np.asarray(ir[arrival : arrival + win], dtype=float) ** 2))
    tail_e = float(np.sum(np.asarray(ir[-win:], dtype=float) ** 2))
    decayed = head_e > 0.0 and tail_e <= head_e * 10.0 ** (threshold_db / 10.0)

    below = np.flatnonzero(edc <= threshold_db)
    crossed = bool(decayed and below.size)
    ring = int(below[0]) if crossed else len(edc)

    return Decay(
        threshold_db=threshold_db,
        fs=fs,
        arrival=arrival,
        ring_taps=ring,
        span_taps=arrival + ring,
        crossed=crossed,
    )


def envelope_tau(ir: np.ndarray, fs: float, threshold_db: float = -40.0) -> float:
    """
    The direct 20*log10(|h(t)|/|h(0)|) reading, in seconds, for cross-checking
    a hand calculation against `measure`.

    Reported as the LAST sample above the threshold, not the first crossing:
    on a reflection train the envelope dips below threshold between arrivals,
    so a first-crossing answer would just be the gap after the direct pulse.

    Expect this to come out shorter than the Schroeder ring at the same
    threshold -- it asks when the last *arrival* is that loud, while the
    integral asks when the remaining *energy* is that small. The gap between
    them is a measure of how sparse the response is.
    """
    last = _last_above(ir, threshold_db)
    return (last + 1) / fs if last is not None else 0.0


if __name__ == "__main__":
    import math
    from duct import Duct, secondary_path, primary_path

    d = Duct()
    g = 0.7
    n_refl = math.ceil(math.log(1e-3) / math.log(g))
    ir_len = 32768  # long enough that -60 dB is reached, not truncated
    round_trip = 2 * d.duct_len / d.speed_sound

    s_ir = secondary_path(d, g, n_refl, ir_len)
    p_ir = primary_path(d, g, n_refl, ir_len)

    print(
        f"duct L={d.duct_len} m, g={g}, fs={d.fs} Hz, "
        f"round trip 2L/c = {round_trip * 1e3:.2f} ms\n"
    )

    # One round trip is two bounces, so amplitude falls by g^2 -> 40*log10(g)
    # dB per round trip. Analytic ring time for comparison.
    print("S(z)                                                       analytic")
    for thr in (-20.0, -30.0, -40.0, -60.0):
        want = thr / (40.0 * math.log10(g)) * round_trip
        print(f"  {measure(s_ir, d.fs, thr)}   {want * 1e3:6.2f} ms")

    print("\nP(z)")
    for thr in (-20.0, -40.0):
        print(f"  {measure(p_ir, d.fs, thr)}")

    m_s = measure(s_ir, d.fs, -40.0)
    m_p = measure(p_ir, d.fs, -40.0)
    print(
        f"\ncausal margin: {m_p.arrival - m_s.arrival} taps "
        f"(P arrives {m_p.arrival}, S arrives {m_s.arrival}) -- "
        f"{'causal, FxLMS viable' if m_p.arrival >= m_s.arrival else 'ACAUSAL'}"
    )
    print(
        f"envelope cross-check at -40 dB: {envelope_tau(s_ir, d.fs, -40.0) * 1e3:.2f} ms "
        f"vs Schroeder span {m_s.span_s * 1e3:.2f} ms"
    )
