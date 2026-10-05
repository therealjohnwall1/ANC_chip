// jtag_stream.sv -- JTAG (BSCANE2 USER1) sample streamer.
//
// Exposes a NSAMP-wide window of SW-bit samples through the FPGA's JTAG TAP
// using a USER1 data register. The host shifts the window out (LSB first) with
// repeated DR scans; each scan CAPTUREs a fresh window.
//
// This build streams a self-contained ramp (counter) so the transport can be
// validated end-to-end with no other data source. Replace the ramp source with
// the mic / ANC signals later (via a clock-domain FIFO into the DRCK domain).
//
// Chain: TDI -> ARM DAP (ir=4, kept in BYPASS) -> PL (ir=6, USER1) -> TDO.
// Host IR scan: PL_IR(6) || ARM_BYPASS(4), LSB-first = "010000" + "1111".
// DR scan length = NSAMP*SW + 1 (the +1 is the ARM DAP's 1-bit BYPASS).
//
// NOTE: the register/shift logic is clocked by DRCK, which (per UG953) toggles
// when SEL is asserted and CAPTURE or SHIFT is asserted.

module jtag_stream #(
    parameter int NSAMP = 16,
    parameter int SW = 16
) (
    input logic clk,
    input logic rst_n
);

  localparam int W = NSAMP * SW;

  logic capture, drck, bscan_reset, runtest, sel, shift, tck, tdi, tms, update, tdo;

  // BSCANE2 is a Xilinx primitive; bscan_sim_stub.sv provides a Verilator model.
  BSCANE2 #(
      .JTAG_CHAIN(1)  // USER1
  ) u_bscan (
      .CAPTURE(capture),
      .DRCK   (drck),
      .RESET  (bscan_reset),
      .RUNTEST(runtest),
      .SEL    (sel),
      .SHIFT  (shift),
      .TCK    (tck),
      .TDI    (tdi),
      .TMS    (tms),
      .UPDATE (update),
      .TDO    (tdo)
  );

  logic [W-1:0] sr;
  logic [31:0] cnt;  // ramp source (DRCK domain)

  always_ff @(posedge drck) begin
    if (bscan_reset) begin
      sr  <= '0;
      cnt <= '0;
    end else if (capture) begin
      // present NSAMP consecutive ramp values; advance for the next scan
      for (int k = 0; k < NSAMP; k++) sr[k*SW+:SW] <= cnt[SW-1:0] + k[SW-1:0];
      cnt <= cnt + NSAMP;
    end else if (shift) begin
      sr <= {tdi, sr[W-1:1]};
    end
  end

  assign tdo = sr[0];

  // keep clk/rst_n in the port list for the future CDC-FIFO source
  logic _unused;
  assign _unused = ^{clk, rst_n, sel, update, runtest, tck, tms};

endmodule
