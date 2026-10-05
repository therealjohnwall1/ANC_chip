#!/usr/bin/env python3
"""jtag_live.py -- read the FPGA's JTAG (BSCANE2 USER1) sample stream.

Option C live link: no extra hardware and no PS. The PL exposes a window of
NSAMP 16-bit samples plus a 32-bit sample index on JTAG USER1 (see
fpga/src/jtag_stream.sv). The host walks the Zynq 2-TAP chain (ARM DAP ir=4,
PL ir=6), sets PL=USER1 / ARM=BYPASS, then scans the DR. The index lets the
host drop overlapping samples across scans.

    ./livetest_c.sh raw --count 4        # print a few windows
    ./livetest_c.sh plot                 # live rolling waveform + spectrum

Run inside fpga/host/.venv (pyftdi + system numpy/matplotlib).
"""

import argparse
import sys
import threading
import time
from collections import deque

# JTAG chain: TDI -> ARM DAP (ir=4) -> PL (ir=6) -> TDO.
# IR scan order (first shifted -> last): PL_IR(6) || ARM_BYPASS(4), LSB-first.
PL_USER1 = "010000"   # USER1 opcode 0x02, 6 bits, LSB-first
ARM_BYPASS = "1111"
IR_USER1 = PL_USER1 + ARM_BYPASS

IDX_BITS = 32
MASK32 = (1 << 32) - 1


class JtagStream:
    def __init__(self, url, nsamp=16, sw=16, freq=1_000_000):
        from pyftdi.jtag import JtagEngine
        from pyftdi.bits import BitSequence

        self.BitSequence = BitSequence
        self.nsamp = nsamp
        self.sw = sw
        self.width = nsamp * sw + IDX_BITS
        self.jtag = JtagEngine(frequency=freq)
        self.jtag.configure(url)
        self.jtag.reset()
        self.jtag.go_idle()
        self.jtag.write_ir(BitSequence(IR_USER1))

    def read_window(self):
        """Return (samples[NSAMP], index); samples are signed SW-bit ints."""
        dr = self.jtag.read_dr(self.width + 1)  # +1 = ARM DAP BYPASS bit
        mask = (1 << self.sw) - 1
        sign = 1 << (self.sw - 1)
        samples = []
        for k in range(self.nsamp):
            v = 0
            for b in range(self.sw):
                if dr[k * self.sw + b]:
                    v |= 1 << b
            if v & sign:
                v -= (1 << self.sw)
            samples.append(v)
        idx = 0
        base = self.nsamp * self.sw
        for b in range(IDX_BITS):
            if dr[base + b]:
                idx |= 1 << b
        return samples, idx

    def close(self):
        self.jtag.close()


def cmd_raw(js, count):
    for _ in range(count):
        w, idx = js.read_window()
        print(f"idx={idx:10d}  " + " ".join(f"{x:6d}" for x in w))


def cmd_plot(js, seconds, fs=16000, fps=30, nwin=2000, fft=1024):
    """Scrolling (rolling-window) display of the de-duplicated sample stream."""
    import numpy as np
    import matplotlib.pyplot as plt

    ring = deque(maxlen=nwin)  # rolling window of the most recent samples
    stop = threading.Event()
    lock = threading.Lock()
    last = None

    def reader():
        nonlocal last
        while not stop.is_set():
            samples, idx = js.read_window()
            ns = len(samples)
            if last is None:
                last = (idx - ns) & MASK32
            new = []
            for k, s in enumerate(samples):
                si = (idx - (ns - 1) + k) & MASK32
                d = (si - last) & MASK32
                if 0 < d < 0x80000000:
                    new.append(s)
                    last = si
            if new:
                with lock:
                    ring.extend(new)

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    plt.ion()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6))
    fig.suptitle("anchor FPGA PDM mic -- JTAG (USER1) rolling window")
    (ln,) = ax1.plot([], [], lw=1)
    ax1.set_xlim(0, nwin)
    ax1.set_xlabel("samples (most recent at right)")
    ax1.set_ylabel("PCM")
    (fl,) = ax2.plot([], [], lw=1)
    ax2.set_xlabel("Hz")
    ax2.set_ylabel("|FFT|")
    fig.tight_layout()

    freqs = np.fft.rfftfreq(fft, d=1.0 / fs)
    t0 = time.time()

    def upd():
        with lock:
            y = np.fromiter(ring, dtype=np.float64)
        if len(y) < 32:
            return
        if len(y) < nwin:  # right-align until the rolling window fills
            y = np.concatenate([np.zeros(nwin - len(y)), y])
        ln.set_data(np.arange(nwin), y[-nwin:])
        ax1.set_ylim(min(-100.0, float(y.min())), max(100.0, float(y.max())))
        seg = y[-fft:]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        fl.set_data(freqs, spec)
        ax2.set_xlim(0, fs / 2)
        ax2.set_ylim(0, max(1.0, float(spec.max()) * 1.1))

    try:
        while True:
            upd()
            fig.canvas.draw_idle()
            fig.canvas.flush_events()
            plt.pause(1.0 / fps)
            if seconds and time.time() - t0 > seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        th.join(timeout=1)
        plt.close(fig)


def main(argv=None):
    p = argparse.ArgumentParser(description="FPGA JTAG USER1 mic stream reader")
    p.add_argument("--url", default="ftdi://ftdi:2232:8871422302DF/1")
    p.add_argument("--nsamp", type=int, default=16)
    p.add_argument("--sw", type=int, default=16)
    p.add_argument("--freq", type=float, default=1_000_000, help="JTAG clock Hz")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("raw")
    r.add_argument("--count", type=int, default=4)
    q = sub.add_parser("plot")
    q.add_argument("--seconds", type=float, default=0)
    q.add_argument("--fs", type=float, default=16000)
    args = p.parse_args(argv)

    js = JtagStream(args.url, args.nsamp, args.sw, args.freq)
    try:
        if args.cmd == "raw":
            cmd_raw(js, args.count)
        elif args.cmd == "plot":
            cmd_plot(js, args.seconds, fs=args.fs)
    finally:
        js.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
