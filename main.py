import matplotlib.pyplot as plt
from scripts.gen_noise import generate_noise



if __name__ == "__main__":
    #fs = 44100
    fs = 1000000
    A_tone = 1.0
    sig_noise = 0.8
    f0 = 440
    n = 1000

    x = generate_noise(fs, A_tone, sig_noise, f0, n)

    plt.plot(x)
    plt.title("Generated Noise Validation")
    plt.xlabel("Sample")
    plt.ylabel("Amplitude")
    plt.show()
