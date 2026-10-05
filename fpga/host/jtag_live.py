#!/usr/bin/env python3
"""jtag_live.py -- read the FPGA's JTAG (BSCANE2 USER1) sample stream.

Option C live link: no extra hardware, no PS. The PL exposes a window of
samples plus a 32-bit sample index on JTAG USER1. Two stream formats:

  q4  (default)  NSAMP=8 quadruples {x,d,y,e} (16-bit each) -- ANC loop mode
  mic            NSAMP=16 raw mic PCM samples -- mic-only build

    ./livetest_c.sh raw --count 4          # print a few windows
    ./livetest_c.sh plot                   # live rolling plot (ANC: d vs e)
    ./livetest_c.sh --fmt mic plot         # mic-only build

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

FORMATS = {
    # name: (nsamp, channel names, bits per sample)
    "q4": (8, ("x", "d", "y", "e"), 64),
    "mic": (16, ("mic",), 16),
}


class JtagStream:
    def __init__(self, url, fmt="q4", freq=1_000_000):
        from pyftdi.jtag import JtagEngine
        from pyftdi.bits import BitSequence

        self.BitSequence = BitSequence
        self.nsamp, self.chans, self.cbits = FORMATS[fmt]
        self.nch = len(self.chans)
        self.width = self.nsamp * self.cbits + IDX_BITS
        self.jtag = JtagEngine(frequency=freq)
        self.jtag.configure(url)
        self.jtag.reset()
        self.jtag.go_idle()
        self.jtag.write_ir(BitSequence(IR_USER1))

    def _s16(self, dr, base):
        v = 0
        for b in range(16):
            if dr[base + b]:
                v |= 1 << b
        return v - (1 << 16) if v & 0x8000 else v

    def read_window(self):
        """Return (samples, index); samples is a list of per-channel tuples."""
        dr = self.jtag.read_dr(self.width + 1)  # +1 = ARM DAP BYPASS bit
        out = []
        for k in range(self.nsamp):
            base = k * self.cbits
            if self.nch == 1:
                out.append((self._s16(dr, base),))
            else:
                # q4 packs sample = {x,d,y,e} (e low). bits: e,y,d,x
                e = self._s16(dr, base + 0)
                y = self._s16(dr, base + 16)
                d = self._s16(dr, base + 32)
                x = self._s16(dr, base + 48)
                out.append((x, d, y, e))
        idx = 0
        ib = self.nsamp * self.cbits
        for b in range(IDX_BITS):
            if dr[ib + b]:
                idx |= 1 << b
        return out, idx

    def close(self):
        self.jtag.close()


def cmd_raw(js, count):
    for _ in range(count):
        w, idx = js.read_window()
        head = " ".join(f"{c}:" for c in js.chans)
        print(f"idx={idx:10d}")
        for s in w:
            print("   " + " ".join(f"{v:6d}" for v in s))


def _drain(js, rings, stop, lock):
    """Read windows, de-duplicate by index, append new samples to the rings."""
    last = None
    while not stop.is_set():
        samples, idx = js.read_window()
        ns = len(samples)
        if last is None:
            last = (idx - ns) & MASK32
        for k, s in enumerate(samples):
            si = (idx - (ns - 1) + k) & MASK32
            d = (si - last) & MASK32
            if 0 < d < 0x80000000:
                with lock:
                    for r, v in zip(rings, s):
                        r.append(v)
                last = si


def cmd_plot_mic(js, seconds, fs=16000, fps=30, nwin=2000, fft=1024):
    """Just the reference mic: rolling waveform + spectrum.

    Works for either format -- the mic is the first channel ('x' in q4, the
    only channel in 'mic')."""
    import numpy as np
    import matplotlib.pyplot as plt

    ch = "x" if "x" in js.chans else js.chans[0]
    ring = deque(maxlen=nwin)
    stop = threading.Event()
    lock = threading.Lock()
    th = threading.Thread(target=_drain, args=(js, [ring], stop, lock), daemon=True)
    th.start()

    plt.ion()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6))
    fig.suptitle(f"anchor FPGA on-board mic '{ch}' (REAL, reference) -- rolling window")
    (ln,) = ax1.plot([], [], lw=1, color="tab:blue")
    ax1.set_xlim(0, nwin)
    ax1.set_xlabel("samples (most recent at right)")
    ax1.set_ylabel("PCM")
    (fl,) = ax2.plot([], [], lw=1, color="tab:orange")
    ax2.set_xlim(0, fs / 2)
    ax2.set_xlabel("Hz")
    ax2.set_ylabel("|FFT|")
    fig.tight_layout()

    freqs = np.fft.rfftfreq(fft, d=1.0 / fs)
    t0 = time.time()

    def snap():
        with lock:
            return np.fromiter(ring, dtype=np.float64)

    try:
        while True:
            y = snap()
            if len(y) >= 32:
                if len(y) < nwin:
                    y = np.concatenate([np.zeros(nwin - len(y)), y])
                ln.set_data(np.arange(nwin), y[-nwin:])
                ax1.set_ylim(min(-100.0, float(y.min())), max(100.0, float(y.max())))
                seg = y[-fft:]
                if len(seg) < fft:
                    seg = np.concatenate([np.zeros(fft - len(seg)), seg])
                sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
                fl.set_data(freqs, sp)
                ax2.set_ylim(0, max(1.0, float(sp.max()) * 1.1))
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


def cmd_plot(js, seconds, fs=16000, fps=30, nwin=2000, fft=1024):
    import numpy as np
    import matplotlib.pyplot as plt

    rings = {c: deque(maxlen=nwin) for c in js.chans}
    stop = threading.Event()
    lock = threading.Lock()
    th = threading.Thread(target=_drain, args=(js, list(rings.values()), stop, lock),
                          daemon=True)
    th.start()

    plt.ion()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6))
    fig.suptitle(f"anchor ANC -- x real mic, d/e SIMULATED error path "
                 f"(no speaker/2nd mic)")
    lines = {}
    for c, col in zip(js.chans, ("0.7", "tab:blue", "tab:green", "tab:red")):
        (lines[c],) = ax1.plot([], [], lw=1, color=col, label=c)
    ax1.set_xlim(0, nwin)
    ax1.set_xlabel("samples (most recent at right)")
    ax1.set_ylabel("value")
    ax1.legend(loc="upper right", ncol=len(js.chans))
    (spec_d,) = ax2.plot([], [], lw=1, color="tab:blue", label="d")
    (spec_e,) = ax2.plot([], [], lw=1, color="tab:red", label="e")
    ax2.set_xlim(0, fs / 2)
    ax2.set_xlabel("Hz")
    ax2.set_ylabel("|FFT|")
    ax2.legend(loc="upper right")
    fig.tight_layout()

    freqs = np.fft.rfftfreq(fft, d=1.0 / fs)
    t0 = time.time()

    def snap(c):
        with lock:
            return np.fromiter(rings[c], dtype=np.float64)

    try:
        while True:
            n = len(rings[js.chans[0]])
            if n >= 32:
                for c in js.chans:
                    y = snap(c)
                    if len(y) < nwin:
                        y = np.concatenate([np.zeros(nwin - len(y)), y])
                    lines[c].set_data(np.arange(nwin), y[-nwin:])
                sig = max(100.0, float(max(abs(snap(c)).max() for c in js.chans)))
                ax1.set_ylim(-sig, sig)
                if "d" in rings and "e" in rings and len(snap("e")) >= fft:
                    for c, ln in (("d", spec_d), ("e", spec_e)):
                        seg = snap(c)[-fft:]
                        if len(seg) < fft:
                            seg = np.concatenate([np.zeros(fft - len(seg)), seg])
                        sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
                        ln.set_data(freqs, sp)
                    ax2.set_ylim(0, max(1.0, float(spec_d.get_ydata().max()) * 1.1))
                    pd = float(np.sum(snap("d") ** 2))
                    pe = float(np.sum(snap("e") ** 2))
                    if pe > 0 and pd > 0:
                        import math
                        fig.suptitle(f"anchor ANC (SIMULATED error) -- attenuation "
                                     f"{10 * math.log10(pd / pe):.1f} dB")
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
    p = argparse.ArgumentParser(description="FPGA JTAG USER1 stream reader")
    p.add_argument("--url", default="ftdi://ftdi:2232:8871422302DF/1")
    p.add_argument("--fmt", choices=sorted(FORMATS), default="q4")
    p.add_argument("--freq", type=float, default=1_000_000, help="JTAG clock Hz")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("raw")
    r.add_argument("--count", type=int, default=4)
    q = sub.add_parser("plot")
    q.add_argument("--seconds", type=float, default=0)
    q.add_argument("--fs", type=float, default=16000)
    q.add_argument("--view", choices=["anc", "mic"], default="anc",
                   help="anc: d vs e; mic: just the reference mic waveform+FFT")
    args = p.parse_args(argv)

    js = JtagStream(args.url, args.fmt, args.freq)
    try:
        if args.cmd == "raw":
            cmd_raw(js, args.count)
        elif args.cmd == "plot":
            if args.view == "mic":
                cmd_plot_mic(js, args.seconds, fs=args.fs)
            else:
                cmd_plot(js, args.seconds, fs=args.fs)
    finally:
        js.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
