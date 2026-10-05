// block responsible for sampling in @16khz and saving them into a memory
// buffer window(circular register impl) 32x16 bit register(512) to store
// previous samples -> this gives a lookback time of
//
// Circular buffer is implemented since we do sequential add and mults,
// meaning we only need to keep track of the offsets and then just scan
// through
//
// one writer(peripheral data_in,rdy, and valid)
// multiple readers(read through tap_sel, tap_out)
module sample_in
  import globals::*;
(
    input logic clk,
    input logic rst_n,

    input logic data_rdy,
    input logic data_valid,
    input logic [INPUT_WIDTH-1:0] data_in,

    output sample_t x_n,
    output logic x_n_new,
    output logic [$clog2(HIST_DEPTH)-1:0] head_idx,

    input logic [$clog2(HIST_DEPTH)-1:0] x_tap_sel,
    output sample_t x_tap_out
);

  // Q1.11 -> Q1.15
  localparam int NORM_SHIFT = WORD_LEN - INPUT_WIDTH;

  sample_t hist [HIST_DEPTH];

  sample_t conv;
  assign conv = sample_t'({~data_in[INPUT_WIDTH-1], data_in[INPUT_WIDTH-2:0], {NORM_SHIFT{1'b0}}});

  assign x_tap_out = hist[x_tap_sel];

  // synchronous reset
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      x_n      <= '0;
      x_n_new  <= 1'b0;
      head_idx <= '0;
    end else if (data_rdy && data_valid) begin
      x_n            <= conv;
      x_n_new        <= 1'b1;
      hist[head_idx] <= conv;
      head_idx       <= head_idx + 1'b1;

    end else begin
      x_n_new <= 1'b0;
    end
  end
endmodule
