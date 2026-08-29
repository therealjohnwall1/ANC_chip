// error + NLMS weight-update stage -- chip truth: e(n) arrives directly
//
//   E[n]  = sum_k x_f[n-1-k]^2
//   mu[n] = lr / (E[n] + eps)
//
//
//   w[k] += round_sat(w[k] + mu*e*x_f)      Q1.15, rounded at the write
//
//
// for reference: k is indexing on the taps themselves while n samples time
// (multiple k samples in each n time)
module error_block
  import globals::*;
(
  input logic clk,
  input logic rst_n,

  // one-cycle pulse starts a full update pass; ignored while busy, so the
  // top level must wait for w_n_updated before feeding the next e(n)
  input logic e_n_valid,
  input sample_t e_n,

  // scan index for both the weights memory and the x_f line memory
  output logic [$clog2(TAP_LEN)-1:0] tap_idx,
  input sample_t w_n_tap,   // w[tap_idx], Q1.15
  input xf_t x_fn_tap,      // x_f window[tap_idx], Q2.14

  output sample_t w_new_tap,
  output logic w_wr_en,     
  output logic w_n_updated, 
  output logic mu_saturated
);

  localparam int TAP_CNT_W   = $clog2(TAP_LEN);
  localparam int ACC_FRAC    = 2 * SAMPLE_FRAC;            
  localparam int ACC_SHIFT   = ACC_FRAC - 2 * XF_FRAC;     

  typedef enum logic [2:0] {
    S_IDLE, S_ENERGY, S_DIV_SETUP, S_DIV, S_DIV_ROUND, S_UPD_READ, S_UPD_WRITE
  } state_t;

  state_t state;

  sample_t e_n_q;
  logic [TAP_CNT_W-1:0] tap_cnt;
  assign tap_idx = tap_cnt;

  accum_t e_acc;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      state        <= S_IDLE;
      tap_cnt      <= '0;
      e_n_q        <= '0;
      e_acc        <= '0;
      w_new_tap    <= '0;
      w_wr_en      <= 1'b0;
      w_n_updated  <= 1'b0;
      mu_saturated <= 1'b0;
    end else begin
      w_wr_en     <= 1'b0;
      w_n_updated <= 1'b0;

      case (state)
        S_IDLE: begin
          if (e_n_valid) begin
            e_n_q        <= e_n;
            e_acc        <= '0;
            tap_cnt      <= '0;
            mu_saturated <= 1'b0;
            state        <= S_ENERGY;
          end
        end

        S_ENERGY: begin
          e_acc <= e_acc + (accum_t'(x_fn_tap) * accum_t'(x_fn_tap) <<< ACC_SHIFT);
          if (tap_cnt == TAP_CNT_W'(TAP_LEN - 1)) begin
            state <= S_DIV_SETUP;
          end else begin
            tap_cnt <= tap_cnt + 1'b1;
          end
        end

        S_DIV_SETUP, S_DIV, S_DIV_ROUND: state <= S_DIV_SETUP;

        S_UPD_READ, S_UPD_WRITE: state <= S_UPD_READ;

        default: state <= S_IDLE;
      endcase
    end
  end
endmodule
