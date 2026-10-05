#!/usr/bin/env python3
"""jtag_live.py -- read the FPGA's JTAG (BSCANE2 USER1) sample stream.

Option C live link: no extra hardware and no PS. The PL exposes a NSAMP-wide
window of SW-bit samples on JTAG USER1 (see fpga/src/jtag_stream.sv). The host
walks the TAP (the Zynq chain has two TAPs: ARM DAP ir=4, PL ir=6), sets
PL=USER1 / ARM=BYPASS, then repeatedly scans the DR to pull windows.

This build streams a ramp so the transport can be validated:
    anc_jtag_live.py --url ftdi://ftdi:2232:8871422302DF/1 ramp --count 8
    anc_jtag_live.py ... plot          # live rolling plot

Run inside fpga/host/.venv (has pyftdi + system numpy/matplotlib):
    fpga/host/.venv/bin/python fpga/host/jtag_live.py ...
"""

import argparse
import sys
import threading
import time
from collections import deque

# JTAG chain: TDI -> ARM DAP (ir=4) -> PL (ir=6) -> TDO.
# IR scan order (first shifted -> last): PL_IR(6) || ARM_BYPASS(4), LSB-first.
PL_USER1 = "010000"  # USER1 opcode 0x02, 6 bits, LSB-first
ARM_BYPASS = "1111"
IR_USER1 = PL_USER1 + ARM_BYPASS


class JtagStream:
    def __init__(self, url, nsamp=16, sw=16, freq=1_000_000):
        from pyftdi.jtag import JtagEngine
        from pyftdi.bits import BitSequence

        self.BitSequence = BitSequence
        self.nsamp = nsamp
        self.sw = sw
        self.width = nsamp * sw
        self.jtag = JtagEngine(frequency=freq)
        self.jtag.configure(url)
        self.jtag.reset()
        self.jtag.go_idle()
        self.jtag.write_ir(BitSequence(IR_USER1))

    def read_window(self):
        """Return NSAMP SW-bit samples (LSB-first per sample)."""
        dr = self.jtag.read_dr(self.width + 1)  # +1 = ARM DAP BYPASS bit
        out = []
        for k in range(self.nsamp):
            v = 0
            for b in range(self.sw):
                if dr[k * self.sw + b]:
                    v |= 1 << b
            out.append(v)
        return out

    def close(self):
        self.jtag.close()


def cmd_ramp(js, count):
    import numpy as np

    for _ in range(count):
        w = js.read_window()
        a = np.array([x - (1 << 16) if x >= (1 << 15) else x for x in w], dtype=np.int64)
        print(" ".join(f"{x:6d}" for x in a))
    # ramp sanity: consecutive diffs should be 1 within a window, +1 across
    print("hint: expect 0 1 2 3 ... 15, then 16 17 ... 31, ...")


def cmd_plot(js, seconds, fps=30):
    import numpy as np
    import matplotlib.pyplot as plt
    import matplotlib.animation as anim

    ring = deque(maxlen=4096)
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            ring.extend(js.read_window())

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6))
    fig.suptitle("anchor FPGA JTAG (USER1) stream")
    (ln,) = ax1.plot([], [], lw=1)
    ax1.set_ylim(-1, 64)
    ax1.set_ylabel("sample")
    (fl,) = ax2.plot([], [], lw=1)
    ax2.set_xlim(0, 8000)
    ax2.set_ylabel("|FFT|")

    t0 = time.time()

    def upd(_):
        if len(ring) < 64:
            return ln, fl
        y = np.fromiter(ring, dtype=np.float64)
        ax1.set_xlim(0, len(y))
        ln.set_data(np.arange(len(y)), y)
        seg = y[-2048:]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        fl.set_data(np.arange(len(spec)) * (len(spec) / max(1, len(spec))), spec)
        ax2.set_xlim(0, len(spec))
        ax2.set_ylim(0, max(1.0, spec.max() * 1.1) if len(spec) else 1)
        if seconds and time.time() - t0 > seconds:
            stop.set()
            plt.close(fig)
        return ln, fl

    _ = anim.FuncAnimation(fig, upd, interval=1000 // fps, blit=False, cache_frame_data=False)
    plt.tight_layout()
    plt.show()
    stop.set()
    th.join(timeout=1)


def main(argv=None):
    p = argparse.ArgumentParser(description="FPGA JTAG USER1 stream reader")
    p.add_argument("--url", default="ftdi://ftdi:2232:8871422302DF/1")
    p.add_argument("--nsamp", type=int, default=16)
    p.add_argument("--sw", type=int, default=16)
    p.add_argument("--freq", type=float, default=1_000_000)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("ramp")
    r.add_argument("--count", type=int, default=8)
    q = sub.add_parser("plot")
    q.add_argument("--seconds", type=float, default=0)
    args = p.parse_args(argv)

    js = JtagStream(args.url, args.nsamp, args.sw, args.freq)
    try:
        if args.cmd == "ramp":
            cmd_ramp(js, args.count)
        elif args.cmd == "plot":
            cmd_plot(js, args.seconds)
    finally:
        js.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
