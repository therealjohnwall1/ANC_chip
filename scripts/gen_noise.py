import numpy as np

"""
Full generated data path

                      +-------------------+
                      |   Primary Path    |  ---> Modifies delay & EQ
 Raw Noise x(n) ----> | (Short FIR Filter)| ---------------------------> Disturbance d(n)
                      +-------------------+                                  |
                                                                             v
                                                                   [ Noise at Ear ]
                                                                 (d(n) + Anti-Noise)
"""

# TODO: implement/add support for time enveleopes
# TODO: pink noise swapout/compare for white noise

def generate_noise(fs, A_tone, sig_noise, f0, n)->np.ndarray:
    """
    Generate tonal component for accurate noise profiles from external sources
    Mimics hissing + fundamental frequency
    x[n] = A_tone * sin(2π f0 n / fs) + σ_noise * N(0, 1)

    Args:
        fs: Sampling frequency, in Hz.
        A_tone: Amplitude of the tonal component.
        sig_noise: Standard deviation of the Gaussian noise component.
        f0: Fundamental tone frequency, in Hz.
        n: Number of samples to generate.
    """

    samples = np.arange(n)
    x = A_tone * np.sin(2 * np.pi * f0 * samples / fs) + sig_noise * np.random.normal(0, 1, size=n)

    return x

def FIR_filter(x:np.ndarray, taps:np.ndarray)->np.ndarray:
    """
    np.convolve(x,taps, full) same thing
    Apply short FIR filter to X

    Args:
        x: input(1d)
        taps: filter coefficents
    """

    N = len(taps)
    y = np.zeros(len(x))

    for n in range(len(x)):
        acc = 0
        for k in range(N):
            if n-k >= 0:
                acc += taps[k] * x[n-k]
        y[n] = acc
    return y







