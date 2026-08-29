// error + NLMS weight-update stage -- chip truth: e(n) arrives directly
//
//   E[n]  = sum_k x_f[n-1-k]^2
//   mu[n] = lr / (E[n] + eps)
//
//
//   w[k] += round_sat(w[k] + mu*e*x_f)      Q1.15, rounded at the write
//
//   States:
//   0. wait state, wait till e_n_valid
//   1.
module error_block
  import globals::*;
(
  input logic clk,
  input logic rst_n,

  input logic e_n_valid,
  input sample_t error_sample,

  input xf_t x_fn_tap,
  input sample_t w_n_tap,
  output sample_t w_new_tap,
  output logic w_wr_en
);
  typedef enum logic [2:0] {
    S_IDLE, S_ENERGY, S_DIV_SETUP, S_DIV, S_DIV_ROUND, S_UPD_READ, S_UPD_WRITE
  } state_t;

  logic[64:0] energy_n;
  logic[$clog2(TAP_LEN)-1:0] energy_ct;

always_ff @(posedge clk) begin
  if(!rst_n) begin
  end else begin
  
  case(state_t)
    S_IDLE:
      if (e_n_valid) begin
        state_t <= S_ENERGY;
        energy_n <= 64'b0;
        energy_ct <= $clog2(TAP_LEN)'b0;
      end 

    S_ENERGY:

      



  end



endmodule

