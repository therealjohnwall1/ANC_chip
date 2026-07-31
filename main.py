import numpy as np
import matplotlib.pyplot as plt
from scripts.gen_noise import generate_noise, FIR_filter
from scripts.wiener import lms_walk


def test_generate_noise():
    fs = 44100
    A_tone = 1.0
    sig_noise = 0
    f0 = 440
    n = 1000

    x = generate_noise(fs, A_tone, sig_noise, f0, n)

    plt.plot(x)
    plt.title("Generated Noise Validation")
    plt.xlabel("Sample")
    plt.ylabel("Amplitude")
    plt.show()


def test_lms_walk():
    fs = 44100
    A_tone = 1.0
    sig_noise = 0.1
    f0 = 440
    n = 2000
    taps = 8
    lr = 0.01

    true_path = np.array([0.8, 0.4, -0.2, 0.1, 0.0, -0.05, 0.02, 0.0])

    x_ref = generate_noise(fs, A_tone, sig_noise, f0, n)
    x_truth = FIR_filter(x_ref, true_path)

    history = lms_walk(x_ref, x_truth, taps, lr)
    w_final = history[-1]

    print("true path: ", true_path)
    print("lms w_n:   ", w_final)

    errors = [np.linalg.norm(w - true_path) for w in history]

    plt.plot(errors)
    plt.title("LMS Convergence: ||w_n - w_true||")
    plt.xlabel("Step")
    plt.ylabel("Weight error norm")
    plt.show()


if __name__ == "__main__":
    # test_generate_noise()
    test_lms_walk()
