#!/usr/bin/env python3
"""anc_spi.py -- host driver for the anchor FPGA PmodA SPI/MMIO interface.

Talks the 24-bit SPI protocol implemented by src/serial_bridge.sv to the
register map in src/mmio_regs.sv:

    frame = [ rw | addr[6:0] ] [ data[15:0] ]   (mode 0, MSB first)
    rw=1 write, rw=0 read

It can load s_hat[], stream a golden x/e stimulus, read Y / W back, and
verify against the reduced golden vectors in fpga/src/mem.

Transports (--transport):
  spidev   Linux spidev (Raspberry Pi / SoC SPI). Default /dev/spidev0.0
  ftdi     pyftdi (FT232H / FT2232H MPSSE USB-SPI adapter)
  mock     no hardware; STATUS reads as all-ones, data as 0 (sequence smoke test)

Examples:
  anc_spi.py --transport spidev -d /dev/spidev0.0 -s 1000000 status
  anc_spi.py --transport ftdi -u ftdi://ftdi:232h/1 selftest
  anc_spi.py --transport mock selftest --no-check

Wiring (Blackboard PmodA, see constr/top.xdc):
  JA1_P F16 pmod_sclk  <- SCLK
  JA1_N F17 pmod_cs_n  <- CS   (active low)
  JA2_P G19 pmod_sdi   <- MOSI
  JA2_N G20 pmod_sdo   -> MISO
  JA3_P E18 pmod_irq   -> IRQ  (optional)
  GND/GND, 3V3/3V3
Keep SCLK <= 6.25 MHz (the datapath clock is 25 MHz; serial_bridge needs
sclk <= clk/4).
"""

import argparse
import os
import re
import sys

# ---- register map (mmio_regs.sv) ----
CMD, STATUS, X, E = 0x00, 0x01, 0x02, 0x03
Y0, Y1, Y2, Y3 = 0x04, 0x05, 0x06, 0x07
SH_IDX, SH_DATA = 0x10, 0x11
W_IDX, W_DATA = 0x20, 0x21

CMD_RUN, CMD_PRIME, CMD_CLEAR_COEFFS, CMD_MUTE, CMD_CLEAR_FLAGS = (1 << 0, 1 << 1,
                                                                   1 << 2, 1 << 3,
                                                                   1 << 4)
ST_Y_READY, ST_W_UPDATED, ST_XF_VALID, ST_XF_READY, ST_MU_SAT, ST_FAULT = (
    1 << 0, 1 << 1, 1 << 2, 1 << 3, 1 << 4, 1 << 5)

MASK64 = (1 << 64) - 1


# ========================================================================
# transports
# ========================================================================
class SpidevTransport:
    def __init__(self, device, speed):
        import spidev  # noqa: PLC0415

        bus, dev = _parse_spidev(device)
        self.spi = spidev.SpiDev()
        self.spi.open(bus, dev)
        self.spi.mode = 0
        self.spi.max_speed_hz = speed
        self.spi.bits_per_word = 8

    def xfer(self, out):
        return self.spi.xfer2(out)


class FtdiTransport:
    def __init__(self, url, speed):
        from pyftdi.spi import SpiController  # noqa: PLC0415

        self.ctrl = SpiController()
        self.ctrl.configure(url)
        self.port = self.ctrl.get_port(cs=0, freq=speed, mode=0)

    def xfer(self, out):
        return list(self.port.exchange(out, duplex=True))


class MockTransport:
    """No hardware: STATUS reads as all-ones so the sequence runs to the end."""

    def __init__(self, *_a, **_k):
        pass

    def xfer(self, out):
        addr = out[0] & 0x7F
        if (out[0] & 0x80) == 0 and addr == STATUS:
            return [0, 0xFF, 0xFF]
        return [0, 0, 0]


def _parse_spidev(device):
    m = re.search(r"spidev(\d+)\.(\d+)", device)
    if not m:
        raise SystemExit(f"bad spidev device: {device} (want /dev/spidevB.C)")
    return int(m.group(1)), int(m.group(2))


# ========================================================================
# driver
# ========================================================================
class Anchor:
    def __init__(self, transport):
        self.t = transport

    def raw(self, rw, addr, data):
        out = [((rw & 1) << 7) | (addr & 0x7F), (data >> 8) & 0xFF, data & 0xFF]
        resp = self.t.xfer(out)
        return ((resp[1] & 0xFF) << 8) | (resp[2] & 0xFF)

    def wreg(self, addr, data):
        self.raw(1, addr, data & 0xFFFF)

    def rreg(self, addr):
        return self.raw(0, addr, 0)

    def status(self):
        return self.rreg(STATUS)

    def clear_flags(self):
        self.wreg(CMD, CMD_CLEAR_FLAGS)

    def wait_status(self, mask, tries=10000):
        for _ in range(tries):
            if (self.status() & mask) == mask:
                return True
        raise TimeoutError(f"STATUS mask 0x{mask:04x} never set")

    def load_shat(self, shat):
        for k, v in enumerate(shat):
            self.wreg(SH_IDX, k)
            self.wreg(SH_DATA, v & 0xFFFF)

    def read_w(self):
        out = []
        for k in range(32):
            self.wreg(W_IDX, k)
            out.append(self.rreg(W_DATA))
        return out


# ========================================================================
# vector loading
# ========================================================================
def read_mem(path):
    with open(path) as f:
        return [int(line.strip(), 16) for line in f if line.strip() and not line.startswith("//")]


def read_params(path):
    txt = open(path).read()
    anc_n = int(re.search(r"ANC_N\s*=\s*(\d+)", txt).group(1))
    prime = int(re.search(r"ANC_PRIME\s*=\s*(\d+)", txt).group(1))
    chk = int(re.search(r"Y_CHK\s*=\s*64'h([0-9A-Fa-f]+)", txt).group(1), 16)
    return anc_n, prime, chk


def selftest(anc, mem_dir, check=True):
    xv = read_mem(os.path.join(mem_dir, "x.mem"))
    ev = read_mem(os.path.join(mem_dir, "e.mem"))
    wv = read_mem(os.path.join(mem_dir, "w.mem"))
    shv = read_mem(os.path.join(mem_dir, "shat.mem"))
    anc_n, prime, chk_exp = read_params(
        os.path.join(os.path.dirname(mem_dir), "pkg", "selftest_params.sv"))

    print(f"vectors: ANC_N={anc_n} PRIME={prime} Y_CHK=0x{chk_exp:016X}")
    anc.load_shat(shv)

    for j in range(prime):
        anc.wreg(X, xv[j])
        anc.wait_status(ST_Y_READY)
        anc.wait_status(ST_XF_READY)
        anc.clear_flags()
    print(f"primed {prime}")

    chk = 0
    for j in range(anc_n):
        anc.wreg(X, xv[prime + j])
        anc.wait_status(ST_Y_READY)
        y = anc.rreg(Y0) | (anc.rreg(Y1) << 16) | (anc.rreg(Y2) << 32) | (anc.rreg(Y3) << 48)
        chk = (((chk << 1) | (chk >> 63)) & MASK64) ^ (y & MASK64)
        anc.wait_status(ST_XF_READY)
        anc.wreg(E, ev[j])
        anc.wait_status(ST_W_UPDATED)
        anc.clear_flags()
        if (j & 255) == 255:
            print(f"  {j + 1}/{anc_n}")

    ok = True
    if check:
        wgot = anc.read_w()
        for k, (g, e) in enumerate(zip(wgot, wv)):
            if g != e:
                print(f"  w[{k}] = {g:04x}, expected {e:04x}")
                ok = False
        if chk != chk_exp:
            print(f"  y checksum = 0x{chk:016X}, expected 0x{chk_exp:016X}")
            ok = False
        print("PASS: SPI/MMIO bit-exact vs golden" if ok else "FAIL")
    else:
        print(f"sequence complete; y checksum = 0x{chk:016X} (not checked)")
    return 0 if ok else 1


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    default_mem = os.path.abspath(os.path.join(here, "..", "src", "mem"))

    p = argparse.ArgumentParser(description="anchor PmodA SPI/MMIO host driver")
    p.add_argument("--transport", choices=["spidev", "ftdi", "mock"], default="spidev")
    p.add_argument("-d", "--device", default="/dev/spidev0.0", help="spidev device")
    p.add_argument("-u", "--url", default="ftdi://ftdi:232h/1", help="pyftdi URL")
    p.add_argument("-s", "--speed", type=int, default=1_000_000, help="SCLK Hz (<=6.25e6)")
    p.add_argument("--mem-dir", default=default_mem)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("selftest").add_argument("--no-check", action="store_true")
    r = sub.add_parser("reg-read")
    r.add_argument("addr", type=lambda v: int(v, 0))
    r.add_argument("--count", type=int, default=1)
    w = sub.add_parser("reg-write")
    w.add_argument("addr", type=lambda v: int(v, 0))
    w.add_argument("value", type=lambda v: int(v, 0))
    sub.add_parser("w-dump")

    args = p.parse_args(argv)

    if args.transport == "spidev":
        t = SpidevTransport(args.device, args.speed)
    elif args.transport == "ftdi":
        t = FtdiTransport(args.url, args.speed)
    else:
        t = MockTransport()
    anc = Anchor(t)

    if args.cmd == "status":
        s = anc.status()
        names = ["y_ready", "w_updated", "x_f_valid", "x_f_ready", "mu_sat", "fault"]
        print(f"STATUS = 0x{s:04X}")
        for i, n in enumerate(names):
            print(f"  {n:10s} {(s >> i) & 1}")
    elif args.cmd == "selftest":
        return selftest(anc, args.mem_dir, check=not args.no_check)
    elif args.cmd == "reg-read":
        for i in range(args.count):
            print(f"0x{args.addr + i:02X} = 0x{anc.rreg(args.addr + i):04X}")
    elif args.cmd == "reg-write":
        anc.wreg(args.addr, args.value)
        print(f"wrote 0x{args.value:04X} -> 0x{args.addr:02X}")
    elif args.cmd == "w-dump":
        for k, v in enumerate(anc.read_w()):
            print(f"w[{k:2d}] = 0x{v:04X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
