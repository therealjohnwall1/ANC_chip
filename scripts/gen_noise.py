import numpy as np


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









