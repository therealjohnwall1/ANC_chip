import numpy as np
import matplotlib.pyplot as plt
from scripts.gen_noise import generate_noise, FIR_filter
from scripts.wiener import lms_walk
from scripts.ans import lms_anc, calc_attenuation
from scripts.tune import tune_anc


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


def test_lms_anc():
    fs = 44100
    A_tone = 1.0
    sig_noise = 0.1
    f0 = 440
    n = 2000
    taps = 8
    lr = 0.01
    window = 50  # samples per rolling attenuation estimate

    true_path = np.array([0.8, 0.4, -0.2, 0.1, 0.0, -0.05, 0.02, 0.0])

    x_ref = generate_noise(fs, A_tone, sig_noise, f0, n)
    d = FIR_filter(x_ref, true_path)

    x_out, d_out, y, e, w_history = lms_anc(x_ref, d, taps, lr)
    w_history = np.array(w_history)

    # rolling attenuation: how much quieter e is vs d, in non-overlapping chunks over time
    n_chunks = len(e) // window
    attenuation = [
        calc_attenuation(d_out[i*window:(i+1)*window], e[i*window:(i+1)*window])
        for i in range(n_chunks)
    ]

    w_error = [np.linalg.norm(w - true_path) for w in w_history]

    fig, axes = plt.subplots(3, 1, figsize=(8, 9))

    axes[0].plot(attenuation)
    axes[0].set_title("Attenuation over time (rolling, log10 P_d/P_e)")
    axes[0].set_xlabel(f"Chunk (window={window} samples)")
    axes[0].set_ylabel("Attenuation")

    axes[1].plot(e)
    axes[1].set_title("Residual error e[n]")
    axes[1].set_xlabel("Sample")
    axes[1].set_ylabel("Amplitude")

    axes[2].plot(w_error)
    axes[2].set_title("||w_n - w_true|| convergence")
    axes[2].set_xlabel("Step")
    axes[2].set_ylabel("Weight error norm")

    plt.tight_layout()
    plt.show()


def test_tune_anc():
    fs = 44100
    A_tone = 1.0
    sig_noise = 0.1
    f0 = 440
    n = 2000

    true_path = np.array([0.8, 0.4, -0.2, 0.1, 0.0, -0.05, 0.02, 0.0])

    x_ref = generate_noise(fs, A_tone, sig_noise, f0, n)
    d = FIR_filter(x_ref, true_path)

    taps_list = [8, 16, 32]
    lr_list = [0.005, 0.01, 0.05, 0.2]  # 0.2 is intentionally past the stability bound

    tune_anc(x_ref, d, taps_list, lr_list, true_path=true_path)


if __name__ == "__main__":
    # test_generate_noise()
    # test_lms_walk()
    #test_lms_anc()
    test_tune_anc()
