// top.sv -- Real Digital Blackboard (Zynq XC7Z007S-1) wrapper for the anchor ANC chip.
//
// This is the FPGA prototype shell around tt_um_anchor (SPI slave -> mmio_regs
// -> anchor_top). The host-facing SPI/MMIO interface is brought out on PmodA so
// a host (Raspberry Pi, MCU, FT2232 MPSSE, ...) can drive the register map
// exactly as the ASIC would be driven.
//
// Clocking: the 100 MHz board clock (H16) drives the whole design directly; the
// datapath is handshake-paced, so no PLL is needed. serial_bridge requires
// sclk <= clk/4, i.e. keep the host SPI below 25 MHz.
//
// PmodA pinout (see constr/top.xdc):
//   JA1_P  F16  pmod_sclk   (host -> FPGA)
//   JA1_N  F17  pmod_cs_n   (host -> FPGA, active low)
//   JA2_P  G19  pmod_sdi    (host -> FPGA, MOSI)
//   JA2_N  G20  pmod_sdo    (FPGA -> host, MISO)
//   JA3_P  E18  pmod_irq    (FPGA -> host, level, sticky done/fault)
//
// Board I/O is used purely for observability:
//   led[0]   heartbeat
//   led[1]   irq
//   led[2]   x_f_valid
//   led[3]   y_n_ready
//   led[4]   w_n_updated
//   led[5]   mu_saturated
//   rgb_a    {y_n_ready, x_f_valid, w_n_updated}

module top (
    input  logic       clk,
    input  logic [3:0] btn,
    input  logic [11:0] sw,
    output logic [9:0] led,
    output logic [2:0] rgb_a,
    output logic [2:0] rgb_b,

    // PmodA -- SPI/MMIO host interface
    input  logic pmod_sclk,
    input  logic pmod_cs_n,
    input  logic pmod_sdi,
    output logic pmod_sdo,
    output logic pmod_irq
);

  // Power-on reset: hold the design in reset for 256 clocks after configuration.
  logic [7:0] por = '0;
  always_ff @(posedge clk)
    if (por != 8'hFF) por <= por + 1'b1;
  logic rst_n;
  assign rst_n = (por == 8'hFF);

  // ---- tt_um_anchor: ui_in = {5'b0, sdi, cs_n, sclk} ----
  logic [7:0] ui_in, uo_out, uio_out, uio_oe;
  assign ui_in = {5'b0, pmod_sdi, pmod_cs_n, pmod_sclk};

  tt_um_anchor u_anchor (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (8'b0),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (1'b1),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  // uo_out = {2'b0, mu_saturated, w_n_updated, y_n_ready, x_f_valid, irq, sdo}
  assign pmod_sdo = uo_out[0];
  assign pmod_irq = uo_out[1];

  // ---- observability ----
  logic [24:0] hb = '0;
  always_ff @(posedge clk) hb <= hb + 1'b1;

  assign led[0]   = hb[24];
  assign led[1]   = uo_out[1];  // irq
  assign led[2]   = uo_out[2];  // x_f_valid
  assign led[3]   = uo_out[3];  // y_n_ready
  assign led[4]   = uo_out[4];  // w_n_updated
  assign led[5]   = uo_out[5];  // mu_saturated
  assign led[9:6] = 4'b0;

  assign rgb_a = {uo_out[3], uo_out[2], uo_out[4]};
  assign rgb_b = 3'b0;

  // btn/sw reserved for the bring-up FSM (self-test trigger / mode select).
  logic _unused;
  assign _unused = ^{btn, sw};

endmodule
