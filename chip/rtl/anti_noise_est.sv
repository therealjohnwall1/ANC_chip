// anti-noise generation stage
//
//
module anti_noise
  import globals::*;
(
  input logic clk,
  input logic rst_n,

  input logic x_n_new,

  // select x_tap_sel, pull from x_tap_out in sample_in block
  // select w_tap_sel, pull from w_tap_out in weights block

  input logic [$clog2(TAP_LEN)-1:0] head_idx,
  input sample_t x_tap_out,
  input sample_t w_tap_out,
  output logic [$clog2(TAP_LEN)-1:0] x_tap_sel,
  output logic [$clog2(TAP_LEN)-1:0] w_tap_sel,
  output accum_t y_n,
  output logic y_n_ready
);

  assign x_tap_sel = head_idx - acc_num[$clog2(TAP_LEN)-1:0] - 1'b1;
  assign w_tap_sel = acc_num[$clog2(TAP_LEN)-1:0];

  logic [$clog2(TAP_LEN):0] acc_num;

  logic active;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      active    <= 1'b0;
      acc_num   <= '0;
      y_n       <= '0;
      y_n_ready <= 1'b0;
    
    // start TAP_LEN cycle MAC
    end else if (x_n_new) begin
      active    <= 1'b1;
      acc_num   <= '0;
      y_n       <= '0;
      y_n_ready <= 1'b0;

    // cont MAC
    end else if (active) begin
      y_n <= y_n + accum_t'(w_tap_out) * accum_t'(x_tap_out);
      
      // MAC full, set y_n_ready flag high to pull data out and move onto next
      if (acc_num == ($clog2(TAP_LEN) + 1)'(TAP_LEN - 1)) begin
        active    <= 1'b0;
        y_n_ready <= 1'b1;


      // MAC not full,
      end else begin
        acc_num   <= acc_num + 1'b1;
        y_n_ready <= 1'b0;
      end
    
    end else begin
      y_n_ready <= 1'b0;
    end
  end
endmodule
