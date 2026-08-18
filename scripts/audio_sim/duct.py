from dataclasses import dataclass
import numpy as np

@dataclass
class Duct:
    """
    Represents speaker/duct/error microphone reference for noise cancelling setup.
       0 m                          0.8 m                 1.0 m         L (duct end)
    │                             │                      │              │
    ●─────────────────────────────●──────────────────────●──────────────┤
  Reference                    Speaker                 Error          Wall/
    Mic                      (anti-noise)                Mic         opening
    │                             │                      │              │
    │◄────── Primary path P(z) ──────────────────────────►              │
    │        (noise source → error mic)                                 │
    │                             │                                     │
    │                             │◄── Secondary path S(z) ──►│         │
    │                             │   (speaker → error mic)   │         │
    │                             │       direct: 0.58 ms     │         │
    │                             │                            ╲        │
    │                             │                             ╲───────┤ reflect (×g)
    │                             │                            ╱        │
    │                             │◄── echo #1 arrives ───────╱         │
    │                             │    delay = ((L-spk) + (L-err)) / c  │
    │                             │    amplitude ×g                     │
    │                             │                                     │
    │                             │        ... echoes ×g², ×g³, decaying...
    Values pulled from theory_of_op.md in docs
    """

    # meters
    ref_mic: float = 0
    speaker: float = 0.8
    error_mic: float = 1
    duct_len: float = 1.2  # L, position of the terminating wall/opening TODO: measure off real enclosure
    speed_sound: float = 343 # m/s
    fs: int = 16_000 # sample rate the ANC block runs at, see theory_of_op.md


def _images(src: float, duct_len: float, end_coeff: float, entrance_coeff: float, n_reflections: int):
    """
    1D image-source expansion of a duct of length L with a reflector at each end.

    Replacing solving for wave equations, just estimate it using 1d waves
    """
    yield src, 1.0

    for first_bounce_at_end in (True, False):
        pos, amp = src, 1.0
        at_end = first_bounce_at_end
        for _ in range(n_reflections):
            if at_end:
                pos = 2 * duct_len - pos
                amp *= end_coeff
            else:
                pos = -pos
                amp *= entrance_coeff
            at_end = not at_end
            yield pos, amp


def _add_delayed_impulse(ir: np.ndarray, delay: float, amp: float, half_width: int = 8):
    """
    Accumulate amp * delta[n - delay] into ir at a *fractional* sample delay.

    impulse is band-limited: a delta at a non-integer delay
    is a sinc centred on that delay, windowed down to 2*half_width taps
    so it's finite

    Args: ir: buffer to accumulate into, modified in place
        delay: delay in samples, may be fractional
        amp: signed amplitude (negative = polarity flip, e.g. open-end reflection)
        half_width: taps of sinc kept either side of the delay
    """
    if amp == 0.0:
        return

    center = int(np.floor(delay))
    idx = np.arange(center - half_width + 1, center + half_width + 1)
    t = delay - idx  

    window = 0.5 * (1.0 + np.cos(np.pi * t / half_width))
    kernel = amp * np.sinc(t) * window

    keep = (idx >= 0) & (idx < len(ir))  
    np.add.at(ir, idx[keep], kernel[keep])


def generate_ir(config: Duct, reflection_coeff, n_reflections, ir_len,
                src: float = None, dst: float = None, entrance_coeff: float = None) -> np.ndarray:
    """
    1D waveguide (image-source) impulse response between two points in the duct.

    This is the cheap stand-in for a real acoustic solve: below the duct's cross
    mode cutoff (f < c/2d for duct width d) sound in a tube is a plane wave, so
    the response of the medium is just "the same pulse arriving several times,
    later and quieter". No PDE, no mesh -- enumerate the reflection paths, drop
    a scaled impulse at each arrival time, done.

    Each image at position p contributes
        delay   = |p - dst| / c      (seconds)
        amp     = g_end^i * g_entrance^j   (i, j = bounces off each end)
    and the response is the sum of those impulses. Note there is no 1/r spread
    loss: a plane wave in a tube does not spread out, so the only decay is at
    the reflections.

    Args:
        config: duct geometry / sample rate
        reflection_coeff: g at the far end (x = duct_len). |g| < 1. Positive for
            a rigid wall, negative for an open end (pressure inverts on reflection).
        n_reflections: reflection orders to sum per image chain. Truncate once
            g^n is under the noise floor -- past that the echoes do nothing.
        ir_len: length of the returned response, in samples
        src: source position in meters, defaults to the speaker (S(z))
        dst: receiver position in meters, defaults to the error mic (S(z))
        entrance_coeff: g at x = 0, defaults to reflection_coeff

    Returns:
        impulse response, length ir_len, direct arrival normalized to 1.0
    """
    if src is None:
        src = config.speaker
    if dst is None:
        dst = config.error_mic
    if entrance_coeff is None:
        entrance_coeff = reflection_coeff

    assert abs(reflection_coeff) < 1 and abs(entrance_coeff) < 1, "reflections must lose energy or the duct rings forever"

    ir = np.zeros(ir_len)
    samples_per_meter = config.fs / config.speed_sound

    for pos, amp in _images(src, config.duct_len, reflection_coeff, entrance_coeff, n_reflections):
        delay = abs(pos - dst) * samples_per_meter
        if delay >= ir_len + 8:  # whole kernel lands past the end of the buffer
            continue
        _add_delayed_impulse(ir, delay, amp)

    return ir


def secondary_path(config: Duct, reflection_coeff, n_reflections, ir_len) -> np.ndarray:
    """S(z): speaker -> error mic, the path S_hat has to estimate."""
    return generate_ir(config, reflection_coeff, n_reflections, ir_len,
                       src=config.speaker, dst=config.error_mic)


def primary_path(config: Duct, reflection_coeff, n_reflections, ir_len) -> np.ndarray:
    """P(z): reference mic -> error mic, the path that turns x(n) into d(n)."""
    return generate_ir(config, reflection_coeff, n_reflections, ir_len,
                       src=config.ref_mic, dst=config.error_mic)


if __name__ == "__main__":
    duct = Duct()
    g = -0.7  

    n = 2048  

    for name, ir in (("S(z)", secondary_path(duct, g, 24, n)),
                     ("P(z)", primary_path(duct, g, 24, n))):
        peak = int(np.argmax(np.abs(ir)))
        energy = np.cumsum(ir ** 2)
        span = int(np.searchsorted(energy, 0.99 * energy[-1])) + 1
        print(f"{name}: first arrival tap {peak} ({peak / duct.fs * 1e3:.2f} ms), "
              f"99% energy span {span} taps ({span / duct.fs * 1e3:.2f} ms)")
