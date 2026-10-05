// top.sv -- Real Digital Blackboard (Zynq XC7Z007S-1) wrapper for the anchor ANC chip.
//
// One copy of anchor_top is shared between two drivers, selected by SW0:
//
//   SW0 = 0 (default)  on-chip self-test: self_test replays reduced golden
//                      vectors through the datapath and reports pass/fail on
//                      the LEDs / RGB LED.
//   SW0 = 1            PmodA SPI/MMIO host interface: serial_bridge + mmio_regs
//                      expose the register map (same map as the ASIC) on PmodA.
//
// The SPI/MMIO wiring matches chip/rtl/tt_um_anchor.sv; that wrapper is left
// untouched for the TinyTapeout flow.
//
// Clocking: error_block's NLMS update has a ~21 ns critical path, so the
// 100 MHz board clock is divided by 4 to 25 MHz (40 ns) for the whole design.
// serial_bridge requires sclk <= clk/4, i.e. keep host SPI below 6.25 MHz.
//
// PmodA pinout (constr/top.xdc):
//   JA1_P F16 pmod_sclk, JA1_N F17 pmod_cs_n, JA2_P G19 pmod_sdi,
//   JA2_N G20 pmod_sdo,  JA3_P E18 pmod_irq
//
// LEDs:
//   led[0]=pass led[1]=done led[2]=busy led[3]=fail led[4]=test_mode
//   led[5]=heartbeat
//   rgb_a: green=pass, red=fail, blue=running

module top
  import globals::*;
(
    input  logic        clk,
    input  logic [3:0]  btn,
    input  logic [11:0] sw,
    output logic [9:0]  led,
    output logic [2:0]  rgb_a,
    output logic [2:0]  rgb_b,

    // PmodA -- SPI/MMIO host interface
    input  logic pmod_sclk,
    input  logic pmod_cs_n,
    input  logic pmod_sdi,
    output logic pmod_sdo,
    output logic pmod_irq
);

  localparam int HW = $clog2(HIST_DEPTH);

  // ---- clock: 100 MHz / 4 = 25 MHz for the ANC datapath ----
  logic [1:0] clk_div = 2'b0;
  always_ff @(posedge clk) clk_div <= clk_div + 1'b1;
  logic clk_anc;
`ifdef VERILATOR
  assign clk_anc = clk_div[1];
`else
  BUFG u_bufg_anc (
      .I(clk_div[1]),
      .O(clk_anc)
  );
`endif

  // ---- reset (in the clk_anc domain) ----
  logic [7:0] por = '0;
  always_ff @(posedge clk_anc)
    if (por != 8'hFF) por <= por + 1'b1;
  logic rst_n;
  assign rst_n = (por == 8'hFF);

  // SW0 selects the datapath driver (default = on-chip self-test).
  logic test_mode;
  assign test_mode = ~sw[0];

  // ---- SPI slave <-> MMIO ----
  logic        wr, rd;
  logic [ 6:0] addr;
  logic [15:0] wdata, rdata;
  logic        sdo, irq;

  // ---- mmio_regs outputs (host sample/coefficient path) ----
  logic       mm_adc_valid, mm_err_valid;
  logic [INPUT_WIDTH-1:0] mm_adc_data, mm_err_data;
  logic [$clog2(TAP_LEN)-1:0] mm_sh_wr_sel, mm_sh_rd_sel, mm_w_rd_sel;
  sample_t    mm_sh_wr_data, mm_sh_rd_data;
  logic       mm_sh_wr_en;

  // ---- self_test outputs / status ----
  logic       st_adc_valid, st_err_valid;
  logic [INPUT_WIDTH-1:0] st_adc_data, st_err_data;
  logic [$clog2(TAP_LEN)-1:0] st_sh_wr_sel, st_w_rd_sel;
  sample_t    st_sh_wr_data;
  logic       st_sh_wr_en;
  logic       st_busy, st_done, st_pass;

  // ---- muxed datapath inputs ----
  logic       adc_valid, err_valid;
  logic [INPUT_WIDTH-1:0] adc_data, err_data;
  logic [$clog2(TAP_LEN)-1:0] sh_wr_sel, sh_rd_sel, w_rd_sel;
  sample_t    sh_wr_data;
  logic       sh_wr_en;

  // ---- anchor_top outputs ----
  logic          adc_rdy, err_rdy, x_n_new;
  sample_t       x_n, x_tap_out;
  logic [HW-1:0] head_idx;
  accum_t        y_n;
  logic          y_n_ready, w_n_updated, x_f_ready, x_f_valid, mu_saturated;
  sample_t       w_rd_data;

  // ========================================================================
  // SPI slave -> MMIO transaction interface
  // ========================================================================
  serial_bridge u_bridge (
      .clk  (clk_anc),
      .rst_n(rst_n),
      .sclk (pmod_sclk),
      .cs_n (pmod_cs_n),
      .sdi  (pmod_sdi),
      .sdo  (sdo),
      .wr   (wr),
      .rd   (rd),
      .addr (addr),
      .wdata(wdata),
      .rdata(rdata)
  );

  mmio_regs u_mmio (
      .clk         (clk_anc),
      .rst_n       (rst_n),
      .wr          (wr),
      .rd          (rd),
      .addr        (addr),
      .wdata       (wdata),
      .rdata       (rdata),
      .adc_valid   (mm_adc_valid),
      .adc_data    (mm_adc_data),
      .err_valid   (mm_err_valid),
      .err_data    (mm_err_data),
      .sh_wr_sel   (mm_sh_wr_sel),
      .sh_wr_data  (mm_sh_wr_data),
      .sh_wr_en    (mm_sh_wr_en),
      .sh_rd_sel   (mm_sh_rd_sel),
      .sh_rd_data  (mm_sh_rd_data),
      .w_rd_sel    (mm_w_rd_sel),
      .w_rd_data   (w_rd_data),
      .y_n         (y_n),
      .y_n_ready   (y_n_ready),
      .w_n_updated (w_n_updated),
      .x_f_ready   (x_f_ready),
      .x_f_valid   (x_f_valid),
      .mu_saturated(mu_saturated),
      .irq         (irq)
  );

  // ========================================================================
  // On-chip self-test (built-in vectors)
  // ========================================================================
  self_test u_self_test (
      .clk         (clk_anc),
      .rst_n       (rst_n),
      .run         (test_mode),
      .adc_valid   (st_adc_valid),
      .adc_data    (st_adc_data),
      .err_valid   (st_err_valid),
      .err_data    (st_err_data),
      .sh_wr_sel   (st_sh_wr_sel),
      .sh_wr_data  (st_sh_wr_data),
      .sh_wr_en    (st_sh_wr_en),
      .w_rd_sel    (st_w_rd_sel),
      .w_rd_data   (w_rd_data),
      .y_n         (y_n),
      .y_n_ready   (y_n_ready),
      .x_f_ready   (x_f_ready),
      .w_n_updated (w_n_updated),
      .busy        (st_busy),
      .done        (st_done),
      .pass        (st_pass)
  );

  // ========================================================================
  // Datapath (single instance, muxed inputs)
  // ========================================================================
  assign adc_valid  = test_mode ? st_adc_valid  : mm_adc_valid;
  assign adc_data   = test_mode ? st_adc_data   : mm_adc_data;
  assign err_valid  = test_mode ? st_err_valid  : mm_err_valid;
  assign err_data   = test_mode ? st_err_data   : mm_err_data;
  assign sh_wr_sel  = test_mode ? st_sh_wr_sel  : mm_sh_wr_sel;
  assign sh_wr_data = test_mode ? st_sh_wr_data : mm_sh_wr_data;
  assign sh_wr_en   = test_mode ? st_sh_wr_en   : mm_sh_wr_en;
  assign sh_rd_sel  = test_mode ? '0            : mm_sh_rd_sel;
  assign w_rd_sel   = test_mode ? st_w_rd_sel   : mm_w_rd_sel;

  anchor_top u_anchor (
      .clk         (clk_anc),
      .rst_n       (rst_n),
      .adc_valid   (adc_valid),
      .adc_data    (adc_data),
      .adc_rdy     (adc_rdy),
      .err_valid   (err_valid),
      .err_data    (err_data),
      .err_rdy     (err_rdy),
      .x_n         (x_n),
      .x_n_new     (x_n_new),
      .head_idx    (head_idx),
      .x_tap_out   (x_tap_out),
      .y_n         (y_n),
      .y_n_ready   (y_n_ready),
      .w_n_updated (w_n_updated),
      .mu_saturated(mu_saturated),
      .w_rd_sel    (w_rd_sel),
      .w_rd_data   (w_rd_data),
      .x_f_ready   (x_f_ready),
      .x_f_valid   (x_f_valid),
      .sh_wr_sel   (sh_wr_sel),
      .sh_wr_data  (sh_wr_data),
      .sh_wr_en    (sh_wr_en),
      .sh_rd_sel   (sh_rd_sel),
      .sh_rd_data  (mm_sh_rd_data)
  );

  // ========================================================================
  // Board I/O
  // ========================================================================
  assign pmod_sdo = sdo;
  assign pmod_irq = irq;

  logic [24:0] hb = '0;
  always_ff @(posedge clk_anc) hb <= hb + 1'b1;

  assign led[0]   = st_pass;
  assign led[1]   = st_done;
  assign led[2]   = st_busy;
  assign led[3]   = st_done & ~st_pass;  // fail
  assign led[4]   = test_mode;
  assign led[5]   = hb[24];
  assign led[9:6] = 4'b0;

  assign rgb_a = st_pass ? 3'b010 :  // green
                 st_done ? 3'b100 :  // red
                 st_busy ? 3'b001 :  // blue
                 3'b000;
  assign rgb_b = 3'b0;

  logic _unused;
  assign _unused = ^{btn, sw[11:1], adc_rdy, err_rdy, x_n_new, x_n, x_tap_out, head_idx};

endmodule
