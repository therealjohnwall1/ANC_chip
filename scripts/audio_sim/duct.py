from dataclasses import dataclass
import math

import numpy as np

# Reflections below this are treated as gone; sets how deep the image sum
# has to run before generate_ir will trust the tail.
_TAIL_FLOOR = 1e-3  # -60 dB

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

    Replacing solving for wave equations, just estimate it using 1d waves.
    The analytic image set is {2kL +/- src}; the two chains below (first bounce
    off the far end, first bounce off the entrance) walk it outward in
    increasing reflection order, so the first time a position shows up it
    carries the fewest bounces and therefore the right amplitude.

    Coincident images are dropped. This matters when src sits *on* a reflector
    (ref_mic at x=0, the default): there -src == src, so both chains trace the
    same positions and every image would otherwise be emitted twice with two
    different reflection orders -- the direct arrival would come out as
    1 + g instead of 1.
    """
    seen = {round(src, 9)}
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

            key = round(pos, 9)
            if key in seen:
                continue  # keep walking the chain, its later images may still be new
            seen.add(key)
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
    kernel = np.sinc(t) * window
    # An unwindowed sinc sums to 1 over the integers, so it passes DC untouched.
    # Hann-windowing it costs up to ~4% of that at half-sample delays, which
    # would show up as a delay-dependent gain ripple on every arrival. Rescale
    # to put the DC gain back exactly on amp.
    kernel *= amp / kernel.sum()

    keep = (idx >= 0) & (idx < len(ir))  
    np.add.at(ir, idx[keep], kernel[keep])


def generate_ir(config: Duct, reflection_coeff: float, n_reflections: int, ir_len: int,
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
        impulse response, length ir_len. The direct arrival carries unit
        amplitude, but it is spread over a sinc kernel, so the peak *sample*
        sits below 1.0 whenever the arrival lands between samples.
    """
    if src is None:
        src = config.speaker
    if dst is None:
        dst = config.error_mic
    if entrance_coeff is None:
        entrance_coeff = reflection_coeff

    assert abs(reflection_coeff) < 1 and abs(entrance_coeff) < 1, "reflections must lose energy or the duct rings forever"

    # If g^n_reflections is still audible we have truncated the response by
    # reflection count instead of by physics, and every tau downstream is short.
    g_max = max(abs(reflection_coeff), abs(entrance_coeff))
    tail = g_max ** n_reflections
    if tail >= _TAIL_FLOOR:
        needed = math.ceil(math.log(_TAIL_FLOOR) / math.log(g_max))
        raise AssertionError(
            f"n_reflections={n_reflections} leaves the tail at {20 * math.log10(tail):.1f} dB "
            f"for g={g_max}; need n_reflections >= {needed} to get under "
            f"{20 * math.log10(_TAIL_FLOOR):.0f} dB"
        )

    ir = np.zeros(ir_len)
    samples_per_meter = config.fs / config.speed_sound

    for pos, amp in _images(src, config.duct_len, reflection_coeff, entrance_coeff, n_reflections):
        delay = abs(pos - dst) * samples_per_meter
        if delay >= ir_len + 8:  # whole kernel lands past the end of the buffer
            continue
        _add_delayed_impulse(ir, delay, amp)

    return ir


def secondary_path(config: Duct, reflection_coeff: float, n_reflections: int, ir_len: int) -> np.ndarray:
    """S(z): speaker -> error mic, the path S_hat has to estimate."""
    return generate_ir(config, reflection_coeff, n_reflections, ir_len,
                       src=config.speaker, dst=config.error_mic)


def primary_path(config: Duct, reflection_coeff: float, n_reflections: int, ir_len: int) -> np.ndarray:
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
