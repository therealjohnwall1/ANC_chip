// tt_um_anchor.sv -- TinyTapeout top wrapper for anchor
//
// Exposes the ANC datapath (anchor_top) over an SPI slave on the TinyTapeout
// 8/8/8 GPIO. This module is the TT-compatible pin wrapper; the real work is
// in serial_bridge (SPI -> MMIO transactions) and mmio_regs (register bank
// -> anchor_top handshake).
//
// Pin map:
//   ui_in[0]  = sclk   (SPI clock)
//   ui_in[1]  = cs_n   (SPI chip select, active low)
//   ui_in[2]  = sdi    (SPI MOSI)
//   ui_in[7:3] = reserved
//   uo_out[0] = sdo    (SPI MISO)
//   uo_out[1] = irq    (level: sticky done/fault flag from mmio_regs)
//   uo_out[2] = x_f_valid  (debug)
//   uo_out[3] = y_n_ready  (debug)
//   uo_out[4] = w_n_updated(debug)
//   uo_out[5] = mu_saturated(debug)
//   uo_out[7:6] = tied low
//   uio       = unused (all inputs, uio_oe = 0)
//
// ena (TinyTapeout chip-select) holds the design in reset while low, so the
// datapath only runs when the harness selects this design.

module tt_um_anchor
  import globals::*;
(
  input  wire [7:0] ui_in,    // dedicated inputs
  output wire [7:0] uo_out,   // dedicated outputs
  input  wire [7:0] uio_in,   // IOs: input path
  output wire [7:0] uio_out,  // IOs: output path
  output wire [7:0] uio_oe,   // IOs: enable (1 = output)
  input  wire       ena,      // design selected
  input  wire       clk,
  input  wire       rst_n     // reset (active low)
);

  wire sclk = ui_in[0];
  wire cs_n = ui_in[1];
  wire sdi  = ui_in[2];

  // hold the whole design in reset while deselected (ena low)
  wire rst_n_i = rst_n & ena;

  // ---- serial <-> mmio <-> anchor_top wiring ----
  logic        wr, rd;
  logic [6:0]  addr;
  logic [15:0] wdata, rdata;
  logic        sdo, irq;

  logic adc_valid, err_valid;
  logic [INPUT_WIDTH-1:0] adc_data, err_data;

  logic [$clog2(TAP_LEN)-1:0] sh_wr_sel;
  sample_t sh_wr_data;
  logic sh_wr_en;
  logic [$clog2(TAP_LEN)-1:0] sh_rd_sel;
  sample_t sh_rd_data;

  logic [$clog2(TAP_LEN)-1:0] w_rd_sel;
  sample_t w_rd_data;

  accum_t y_n;
  logic y_n_ready, w_n_updated, x_f_ready, x_f_valid, mu_saturated;

  // anchor_top observability outputs we do not route off-chip
  logic adc_rdy, err_rdy;
  sample_t x_n, x_tap_out;
  logic x_n_new;
  logic [$clog2(HIST_DEPTH)-1:0] head_idx;

  serial_bridge u_bridge (
    .clk   (clk),
    .rst_n (rst_n_i),
    .sclk  (sclk),
    .cs_n  (cs_n),
    .sdi   (sdi),
    .sdo   (sdo),
    .wr    (wr),
    .rd    (rd),
    .addr  (addr),
    .wdata (wdata),
    .rdata (rdata)
  );

  mmio_regs u_mmio (
    .clk          (clk),
    .rst_n        (rst_n_i),
    .wr           (wr),
    .rd           (rd),
    .addr         (addr),
    .wdata        (wdata),
    .rdata        (rdata),
    .adc_valid    (adc_valid),
    .adc_data     (adc_data),
    .err_valid    (err_valid),
    .err_data     (err_data),
    .sh_wr_sel    (sh_wr_sel),
    .sh_wr_data   (sh_wr_data),
    .sh_wr_en     (sh_wr_en),
    .sh_rd_sel    (sh_rd_sel),
    .sh_rd_data   (sh_rd_data),
    .w_rd_sel     (w_rd_sel),
    .w_rd_data    (w_rd_data),
    .y_n          (y_n),
    .y_n_ready    (y_n_ready),
    .w_n_updated  (w_n_updated),
    .x_f_ready    (x_f_ready),
    .x_f_valid    (x_f_valid),
    .mu_saturated (mu_saturated),
    .irq          (irq)
  );

  anchor_top u_anchor (
    .clk          (clk),
    .rst_n        (rst_n_i),
    .adc_valid    (adc_valid),
    .adc_data     (adc_data),
    .adc_rdy      (adc_rdy),
    .err_valid    (err_valid),
    .err_data     (err_data),
    .err_rdy      (err_rdy),
    .x_n          (x_n),
    .x_n_new      (x_n_new),
    .head_idx     (head_idx),
    .x_tap_out    (x_tap_out),
    .y_n          (y_n),
    .y_n_ready    (y_n_ready),
    .w_n_updated  (w_n_updated),
    .mu_saturated (mu_saturated),
    .w_rd_sel     (w_rd_sel),
    .w_rd_data    (w_rd_data),
    .x_f_ready    (x_f_ready),
    .x_f_valid    (x_f_valid),
    .sh_wr_sel    (sh_wr_sel),
    .sh_wr_data   (sh_wr_data),
    .sh_wr_en     (sh_wr_en),
    .sh_rd_sel    (sh_rd_sel),
    .sh_rd_data   (sh_rd_data)
  );

  assign uo_out[0]   = sdo;
  assign uo_out[1]   = irq;
  assign uo_out[2]   = x_f_valid;
  assign uo_out[3]   = y_n_ready;
  assign uo_out[4]   = w_n_updated;
  assign uo_out[5]   = mu_saturated;
  assign uo_out[7:6] = 2'b0;

  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;

endmodule
