// tt_um_anchor_tb -- end-to-end integration test over the SPI boundary.
//
// Drives tt_um_anchor through its SPI pins (the same path an MCU or the TT
// harness would use) with the precomputed (x, e) stream from gen_anc_vectors.py
// and checks, after every sample, that y[n] matches golden/core.py's
// accumulator bit-for-bit and that the final weights match.
//
// This is anchor_top_tb's stage order, now exercised through the MMIO register
// map: load s_hat -> prime -> per-sample write X / poll y_n_ready / read Y /
// poll x_f_ready / write E / poll w_n_updated.

module tt_um_anchor_tb;

  import globals::*;
  import dv_globals::*;

  `include "anc_params.svh"

  localparam time         CLK_PERIOD      = 20ns;
  localparam int          SCLK_HALF       = 8;       // clk cycles per sclk half-period

  // ---- register map ----
  localparam logic [ 6:0] REG_CMD         = 7'h00;
  localparam logic [ 6:0] REG_STATUS      = 7'h01;
  localparam logic [ 6:0] REG_X           = 7'h02;
  localparam logic [ 6:0] REG_E           = 7'h03;
  localparam logic [ 6:0] REG_Y_0         = 7'h04;
  localparam logic [ 6:0] REG_Y_1         = 7'h05;
  localparam logic [ 6:0] REG_Y_2         = 7'h06;
  localparam logic [ 6:0] REG_Y_3         = 7'h07;
  localparam logic [ 6:0] REG_SH_IDX      = 7'h10;
  localparam logic [ 6:0] REG_SH_DATA     = 7'h11;
  localparam logic [ 6:0] REG_W_IDX       = 7'h20;
  localparam logic [ 6:0] REG_W_DATA      = 7'h21;

  // ---- STATUS bits ----
  localparam int          SB_Y_READY      = 0;
  localparam int          SB_W_UPD        = 1;
  localparam int          SB_XF_VALID     = 2;
  localparam int          SB_XF_READY     = 3;
  localparam int          SB_MU_SAT       = 4;
  localparam int          SB_FAULT        = 5;

  localparam logic [15:0] CMD_CLEAR_FLAGS = 16'h10;  // CMD bit4

  logic clk;
  logic rst_n;

  logic sclk, cs_n, sdi;
  wire [7:0] ui_in = {5'b0, sdi, cs_n, sclk};
  wire [7:0] uo_out;
  logic [7:0] uio_in;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;
  logic ena;

  wire sdo = uo_out[0];

  tt_um_anchor dut (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  // ---- vectors ----
  logic [INPUT_WIDTH-1:0] x_code[ANC_PRIME + ANC_N];
  logic [INPUT_WIDTH-1:0] e_code[ANC_N];
  accum_t y_exp[ANC_N];
  sample_t w_exp[TAP_LEN];
  sample_t s_hat[TAP_LEN];

  initial begin
    clk = 1'b0;
    forever #(CLK_PERIOD / 2) clk = ~clk;
  end

  initial begin
    $dumpfile({DV_OUT_DIR, "tt_um_anchor_tb.vcd"});
    $dumpvars(0, dut.u_anchor.u_weights);
    $dumpvars(0, dut.u_anchor.u_s_hat_fir);
  end

  // ---- SPI BFM ----
  task automatic spi_bit(input logic tx, output logic rx);
    sdi = tx;
    repeat (SCLK_HALF) @(posedge clk);  // setup while sclk low
    sclk = 1'b1;
    repeat (SCLK_HALF) @(posedge clk);  // hold high
    rx   = sdo;  // master samples on rising edge
    sclk = 1'b0;
    repeat (SCLK_HALF) @(posedge clk);  // hold low
  endtask

  task automatic spi_xfer(input logic rw, input logic [6:0] a, input logic [15:0] d,
                          output logic [15:0] r);
    logic rx;
    cs_n = 1'b0;
    spi_bit(rw, rx);
    for (int i = 6; i >= 0; i--) spi_bit(a[i], rx);
    for (int i = 15; i >= 0; i--) begin
      spi_bit(rw ? d[i] : 1'b0, rx);
      if (!rw) r[i] = rx;
    end
    cs_n = 1'b1;
    repeat (4) @(posedge clk);  // let the commit/wr pulse settle
  endtask

  task automatic mmio_write(input logic [6:0] a, input logic [15:0] d);
    logic [15:0] r;
    spi_xfer(1'b1, a, d, r);
  endtask

  task automatic mmio_read(input logic [6:0] a, output logic [15:0] v);
    logic [15:0] r;
    spi_xfer(1'b0, a, 16'h0, r);
    v = r;
  endtask

  // poll STATUS until bit `bit_idx` is high
  task automatic wait_flag(input int bit_idx);
    logic [15:0] s;
    int guard = 0;
    forever begin
      mmio_read(REG_STATUS, s);
      if (s[bit_idx]) return;
      guard++;
      if (guard > 1000) $fatal(1, "wait_flag: timeout on STATUS bit %0d", bit_idx);
    end
  endtask

  task automatic load_shat();
    for (int k = 0; k < TAP_LEN; k++) begin
      mmio_write(REG_SH_IDX, {11'b0, k[$clog2(TAP_LEN)-1:0]});
      mmio_write(REG_SH_DATA, s_hat[k]);
    end
  endtask

  // prime: push one x with no e, wait for the S_hat FIR to finish
  task automatic prime_sample(input int ci);
    mmio_write(REG_X, {{(16 - INPUT_WIDTH) {1'b0}}, x_code[ci]});
    wait_flag(SB_XF_READY);
    mmio_write(REG_CMD, CMD_CLEAR_FLAGS);
  endtask

  // one full sample: x in -> read y -> x_f ready -> e in -> weights updated
  task automatic run_sample(input int xi, input int ei, input int idx);
    logic [15:0] y0, y1, y2, y3;
    accum_t y_rd;

    mmio_write(REG_CMD, CMD_CLEAR_FLAGS);

    // x
    mmio_write(REG_X, {{(16 - INPUT_WIDTH) {1'b0}}, x_code[xi]});

    // anti-noise FIR -> y[n] (old weights), checked against golden
    wait_flag(SB_Y_READY);
    mmio_read(REG_Y_0, y0);
    mmio_read(REG_Y_1, y1);
    mmio_read(REG_Y_2, y2);
    mmio_read(REG_Y_3, y3);
    y_rd = {y3, y2, y1, y0};
    if (y_rd !== y_exp[idx]) $fatal(1, "y[%0d] = %h, expected %h", idx, y_rd, y_exp[idx]);

    // wait S_hat FIR so error_block's x_f window is the current sample's
    wait_flag(SB_XF_READY);

    // e -> NLMS update
    mmio_write(REG_E, {{(16 - INPUT_WIDTH) {1'b0}}, e_code[ei]});
    wait_flag(SB_W_UPD);
  endtask

  initial begin
    $readmemh("anc_x.mem", x_code);
    $readmemh("anc_e.mem", e_code);
    $readmemh("anc_y.mem", y_exp);
    $readmemh("anc_w.mem", w_exp);
    $readmemh("anc_shat.mem", s_hat);
  end

  initial begin
    rst_n = 1'b0;
    ena = 1'b0;
    uio_in = 8'b0;
    sclk = 1'b0;
    cs_n = 1'b1;
    sdi = 1'b0;
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    ena   = 1'b1;
    repeat (2) @(posedge clk);

    load_shat();
    $display("loaded s_hat[0..%0d] via SPI", TAP_LEN - 1);

    // prime ANC_PRIME samples into the tap lines
    for (int j = 0; j < ANC_PRIME; j++) prime_sample(j);

    begin
      logic [15:0] s;
      mmio_read(REG_STATUS, s);
      if (s[SB_XF_VALID] !== 1'b1) $fatal(1, "x_f_valid never went high after priming");
    end
    $display("primed %0d samples", ANC_PRIME);

    // run the adaptation, checking y[n] each sample
    for (int j = 0; j < ANC_N; j++) begin
      run_sample(ANC_PRIME + j, j, j);
      if ((j & 511) == 511) $display("  sample %0d/%0d", j + 1, ANC_N);
    end

    // final weights
    for (int k = 0; k < TAP_LEN; k++) begin
      logic [15:0] w;
      mmio_write(REG_W_IDX, {11'b0, k[$clog2(TAP_LEN)-1:0]});
      mmio_read(REG_W_DATA, w);
      if (w !== w_exp[k]) $fatal(1, "w[%0d] = %h, expected %h", k, w, w_exp[k]);
    end

    $display("T=%0t: tt_um_anchor_tb done -- %0d samples, y bit-exact vs golden, final w[] match.",
             $time, ANC_N);
    $finish;
  end

  initial begin
    #(ANC_N * 40000 * 20) $fatal(1, "timeout");  // generous upper bound
  end

endmodule
