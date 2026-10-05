// jtag_stream.sv -- JTAG (BSCANE2 USER1) sample streamer.
//
// Exposes a WIDTH-bit word through the FPGA's JTAG TAP using a USER1 data
// register. The host shifts the word out (LSB first) with repeated DR scans;
// each scan CAPTUREs a fresh value of data_in.
//
// For the live link, data_in carries {sample_idx[31:0], NSAMP x 16-bit samples}
// from top.sv; the index lets the host de-duplicate overlapping windows.
//
// Chain: TDI -> ARM DAP (ir=4, kept in BYPASS) -> PL (ir=6, USER1) -> TDO.
// Host IR scan: PL_IR(6) || ARM_BYPASS(4), LSB-first = "010000" + "1111".
// DR scan length = WIDTH + 1 (the +1 is the ARM DAP's 1-bit BYPASS).
//
// NOTE: the shift register is clocked by DRCK, which (per UG953) toggles when
// SEL is asserted and CAPTURE or SHIFT is asserted.

module jtag_stream #(
    parameter int WIDTH = 288
) (
    input logic [WIDTH-1:0] data_in
);

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

  logic [WIDTH-1:0] sr;

  always_ff @(posedge drck) begin
    if (bscan_reset) sr <= '0;
    else if (capture) sr <= data_in;
    else if (shift) sr <= {tdi, sr[WIDTH-1:1]};
  end

  assign tdo = sr[0];

  logic _unused;
  assign _unused = ^{sel, update, runtest, tck, tms};

endmodule
