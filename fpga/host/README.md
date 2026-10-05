# host — PmodA SPI/MMIO driver

`anc_spi.py` drives the anchor FPGA's host-facing SPI/MMIO interface (the same
register map as the ASIC) from a Linux host. Set **SW0 = 1** on the board to
route the datapath to the PmodA SPI path (SW0 = 0 is the on-board self-test).

## Wiring

Route the Blackboard's **PmodA** to a 3.3 V SPI master (Raspberry Pi, an
FT232H/FT2232H adapter, or any MCU):

| PmodA pin | Signal     | Maps to host |
|-----------|------------|--------------|
| JA1_P     | `pmod_sclk`| SCLK         |
| JA1_N     | `pmod_cs_n`| CS (active low) |
| JA2_P     | `pmod_sdi` | MOSI         |
| JA2_N     | `pmod_sdo` | MISO         |
| JA3_P     | `pmod_irq` | IRQ (optional) |
| 5 / 11    | GND        | GND          |
| 6 / 12    | 3V3        | 3V3          |

Keep **SCLK ≤ 6.25 MHz** — the datapath runs at 25 MHz and `serial_bridge`
requires `sclk ≤ clk/4`.

## Raspberry Pi (`spidev`)

```sh
# raspi-config -> Interface Options -> SPI -> enable, then:
sudo apt install python3-spidev        # or: pip install spidev
./anc_spi.py --transport spidev -d /dev/spidev0.0 -s 1000000 status
./anc_spi.py --transport spidev -d /dev/spidev0.0 -s 1000000 selftest
```

`selftest` loads `s_hat[]`, primes, streams the reduced golden x/e vectors from
`../src/mem`, reads Y back (folded into the same checksum as the on-chip
self-test), reads `w[]`, and compares both to golden.

## USB-SPI adapter (`pyftdi`)

```sh
pip install pyftdi
./anc_spi.py --transport ftdi -u ftdi://ftdi:232h/1 -s 1000000 selftest
```

## Commands

```
status                     read STATUS with decoded bits
selftest [--no-check]      run the golden vector stream (default checks)
reg-read  <addr> [--count N]
reg-write <addr> <value>
w-dump                     read back the 32 adaptive weights
```

`--transport mock` needs no hardware: STATUS reads as all-ones and data as 0,
so the full sequence can be exercised as a smoke test
(`anc_spi.py --transport mock selftest --no-check`).

## Protocol

24-bit frames, SPI mode 0, MSB first:

```
bit[23]    rw     (1=write, 0=read)
bit[22:16] addr[6:0]
bit[15:0]  data
```

Register map and STATUS bit definitions are at the top of `../src/mmio_regs.sv`.
