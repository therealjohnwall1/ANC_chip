//main

module anchor_top
  import globals::*;
(
  input wire clk,
  input wire rst_n,

  input logic adc_valid,
  input logic [INPUT_WIDTH-1:0] adc_data,
  output logic adc_rdy,

  output sample_t x_n,
  output logic x_n_new,
  output logic [$clog2(TAP_LEN)-1:0] head_idx,
  output sample_t tap_out
);

  logic [$clog2(TAP_LEN)-1:0] tap_sel;
  assign tap_sel = '0;

  assign adc_rdy = 1'b1;

  sample_in u_sample_in (
    .clk       (clk),
    .rst_n     (rst_n),
    .data_rdy  (adc_rdy),
    .data_valid(adc_valid),
    .data_in   (adc_data),
    .x_n       (x_n),
    .x_n_new   (x_n_new),
    .head_idx  (head_idx),
    .tap_sel   (tap_sel),
    .tap_out   (tap_out)
  );

endmodule
