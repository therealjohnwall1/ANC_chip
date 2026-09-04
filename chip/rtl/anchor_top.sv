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
  output logic [$clog2(TAP_LEN)-1:0] head_idx,
  output sample_t x_tap_out,

  // anti-noise output
  output accum_t y_n,
  output logic y_n_ready,

  // error_block status
  output logic w_n_updated,
  output logic mu_saturated
);

  // ---- wires between blocks ----
  // sample_in -> anti_noise
  logic [$clog2(TAP_LEN)-1:0] x_tap_sel;

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

  // error input, Q1.11 -> Q1.15 (mirrors sample_in's normalization)
  sample_t e_n;
  assign e_n = sample_t'({~err_data[INPUT_WIDTH-1],
                          err_data[INPUT_WIDTH-2:0],
                          {(WORD_LEN-INPUT_WIDTH){1'b0}}});

  assign adc_rdy = 1'b1;
  assign err_rdy = 1'b1;

  sample_in u_sample_in (
    .clk        (clk),
    .rst_n      (rst_n),
    .data_rdy   (adc_rdy),
    .data_valid (adc_valid),
    .data_in    (adc_data),
    .x_n        (x_n),
    .x_n_new    (x_n_new),
    .head_idx   (head_idx),
    .x_tap_sel  (x_tap_sel),
    .x_tap_out  (x_tap_out)
  );

  anti_noise u_anti_noise (
    .clk        (clk),
    .rst_n      (rst_n),
    .x_n_new    (x_n_new),
    .head_idx   (head_idx),
    .x_tap_sel  (x_tap_sel),
    .w_tap_sel  (w_tap_sel),
    .x_tap_out  (x_tap_out),
    .w_tap_out  (w_tap_out),
    .y_n        (y_n),
    .y_n_ready  (y_n_ready)
  );

  error_block u_error_block (
    .clk           (clk),
    .rst_n         (rst_n),
    .e_n_valid     (err_valid),
    .e_n           (e_n),
    .tap_idx       (upd_tap_idx),
    .w_n_tap       (w_n_tap),
    .x_fn_tap      (x_fn_tap),
    .w_new_tap     (w_new_tap),
    .w_wr_en       (w_wr_en),
    .w_n_updated   (w_n_updated),
    .mu_saturated  (mu_saturated)
  );

  /*
  * missing: weights ram(sram)
  * s_hat, x_f filter, 
  * s_hat coefficent memory and mcu load/readback
  */
endmodule
