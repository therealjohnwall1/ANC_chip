// top level

module anchor_top
  import globals::*;
(
    input wire clk,
    input wire rst_n,

    // reference mic (x[n]) adc
    input logic adc_valid,
    input logic [INPUT_WIDTH-1:0] adc_data,
    output logic adc_rdy,

    // error mic (e[n]) adc
    input logic err_valid,
    input logic [INPUT_WIDTH-1:0] err_data,
    output logic err_rdy,

    // sample_in staging / observability
    output sample_t x_n,
    output logic x_n_new,
    output logic [$clog2(HIST_DEPTH)-1:0] head_idx,
    output sample_t x_tap_out,

    // anti-noise output
    output accum_t y_n,
    output logic   y_n_ready,

    // error_block status
    output logic w_n_updated,
    output logic mu_saturated,

    // w[] observability (debug/test readback of the weights register file)
    input logic [$clog2(TAP_LEN)-1:0] w_rd_sel,
    output sample_t w_rd_data,

    // s_hat_fir status / sequencing
    output logic x_f_ready,  // pulse: x_f[n] computed -- assert e after this
    output logic x_f_valid,  // level: x_f line primed, gates the update pass

    // s_hat[] load/readback (memory-level MCU interface; MMIO wrapper is later work)
    input logic [$clog2(TAP_LEN)-1:0] sh_wr_sel,
    input sample_t sh_wr_data,
    input logic sh_wr_en,
    input logic [$clog2(TAP_LEN)-1:0] sh_rd_sel,
    output sample_t sh_rd_data
);

  // ---- wires between blocks ----
  // sample_in -> anti_noise
  logic [$clog2(HIST_DEPTH)-1:0] x_tap_sel;

  // anti_noise <-> weights RAM
  logic [$clog2(TAP_LEN)-1:0] w_tap_sel;
  sample_t w_tap_out;

  // error_block <-> weights RAM
  logic [$clog2(TAP_LEN)-1:0] upd_tap_idx;
  sample_t w_n_tap;
  sample_t w_new_tap;
  logic w_wr_en;

  // error_block <- x_f RAM (filtered reference)
  xf_t x_fn_tap;

  // error_block trigger: don't run the update pass until the x_f line is
  // primed. In a sample, e[n] should be asserted AFTER x_f_ready so the update
  // window is x_f[n-1-k], not one sample stale.
  logic e_n_valid_g;
  assign e_n_valid_g = err_valid && x_f_valid;

  // error input, Q1.11 -> Q1.15 (mirrors sample_in's normalization)
  sample_t e_n;
  assign e_n = sample_t'({
    ~err_data[INPUT_WIDTH-1], err_data[INPUT_WIDTH-2:0], {(WORD_LEN - INPUT_WIDTH) {1'b0}}
  });

  assign adc_rdy = 1'b1;
  assign err_rdy = 1'b1;

  sample_in u_sample_in (
      .clk       (clk),
      .rst_n     (rst_n),
      .data_rdy  (adc_rdy),
      .data_valid(adc_valid),
      .data_in   (adc_data),
      .x_n       (x_n),
      .x_n_new   (x_n_new),
      .head_idx  (head_idx),
      .x_tap_sel (x_tap_sel),
      .x_tap_out (x_tap_out)
  );

  anti_noise u_anti_noise (
      .clk      (clk),
      .rst_n    (rst_n),
      .x_n_new  (x_n_new),
      .head_idx (head_idx),
      .x_tap_sel(x_tap_sel),
      .w_tap_sel(w_tap_sel),
      .x_tap_out(x_tap_out),
      .w_tap_out(w_tap_out),
      .y_n      (y_n),
      .y_n_ready(y_n_ready)
  );

  error_block u_error_block (
      .clk         (clk),
      .rst_n       (rst_n),
      .e_n_valid   (e_n_valid_g),
      .e_n         (e_n),
      .tap_idx     (upd_tap_idx),
      .w_n_tap     (w_n_tap),
      .x_fn_tap    (x_fn_tap),
      .w_new_tap   (w_new_tap),
      .w_wr_en     (w_wr_en),
      .w_n_updated (w_n_updated),
      .mu_saturated(mu_saturated)
  );

  // weights memory, w[] -- FF register file
  //   read A: anti_noise     (wr .w_tap_sel -> .w_tap_out, THIS sample's FIR)
  //   read B: error_block    (.addr -> .w_n_tap, the update stage)
  //   write : error_block    (.addr, .w_new_tap, .w_wr_en)
  // read B and write share addr (upd_tap_idx): RMW at one index, one cycle apart
  regfile #(
      .DEPTH(TAP_LEN),
      .WIDTH(WORD_LEN)
  ) u_weights (
      .clk      (clk),
      .rst_n    (rst_n),
      .rd_sel_a (w_tap_sel),
      .rd_data_a(w_tap_out),
      .rd_sel_b (upd_tap_idx),
      .rd_data_b(w_n_tap),
      .rd_sel_c (w_rd_sel),
      .rd_data_c(w_rd_data),
      .wr_sel   (upd_tap_idx),
      .wr_data  (w_new_tap),
      .wr_en    (w_wr_en)
  );

  // S_hat FIR -- computes x_f[n] = sum s_hat[m]*x[n-m], holds s_hat[] in its
  // own regfile, and drives error_block's x_f window scan (tap_idx).
  // x_n here is sample_in's x_n output (the just-sampled reference).
  s_hat_fir u_s_hat_fir (
      .clk       (clk),
      .rst_n     (rst_n),
      .x_n_new   (x_n_new),
      .x_n       (x_n),
      .tap_idx   (upd_tap_idx),
      .x_fn_tap  (x_fn_tap),
      .x_f_ready (x_f_ready),
      .x_f_valid (x_f_valid),
      .sh_wr_sel (sh_wr_sel),
      .sh_wr_data(sh_wr_data),
      .sh_wr_en  (sh_wr_en),
      .sh_rd_sel (sh_rd_sel),
      .sh_rd_data(sh_rd_data)
  );

endmodule
