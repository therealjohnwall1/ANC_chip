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
  output logic w_wr_en,     // high the cycle w_new_tap/tap_idx are stable;
                            // the weights memory samples them on the edge
                            // that ends the cycle
  output logic w_n_updated, // one-cycle pulse, all TAP_LEN taps written
  output logic mu_saturated // sticky per update: mu exceeded step_t
);

  // the divider toggles every cycle for ~64 cycles a sample; don't trace it
  /* verilator tracing_off */

  localparam int TAP_CNT_W    = $clog2(TAP_LEN);
  localparam int SAMPLE_FRAC  = WORD_LEN - 1;                     // 15, Q1.15
  localparam int ACC_FRAC     = 2 * SAMPLE_FRAC;                  // 30
  localparam int ACC_SHIFT    = ACC_FRAC - 2 * XF_FRAC;           // 2
  localparam int NUM_SHIFT    = ACC_FRAC + STEP_FRAC - LR_FRAC;   // 32, update.py:117
  localparam int DELTA_FRAC   = STEP_FRAC + SAMPLE_FRAC + XF_FRAC; // 46, mu*e*x_f
  localparam int W_SHIFT      = DELTA_FRAC - SAMPLE_FRAC;         // 31, align w to delta

  localparam accum_t EPS_ACC = accum_t'(EPS_RAW) <<< (ACC_FRAC - EPS_FRAC);

  accum_t div_num;
  assign div_num = accum_t'(LR_RAW) <<< NUM_SHIFT;

  typedef enum logic [2:0] {
    S_IDLE, S_ENERGY, S_DIV_SETUP, S_DIV, S_DIV_ROUND, S_UPD_READ, S_UPD_WRITE
  } state_t;

  state_t state;

  sample_t e_n_q;
  logic [TAP_CNT_W-1:0] tap_cnt;
  assign tap_idx = tap_cnt;

  accum_t e_acc;

  accum_t div_den;
  logic [64:0] div_rem;
  logic [63:0] div_quot;
  logic [5:0]  div_cnt;

  logic [64:0] rem_shift, rem_sub;
  logic        div_ge;

  assign rem_shift = {div_rem[63:0], div_num[div_cnt]};
  assign rem_sub   = rem_shift - {1'b0, div_den};
  assign div_ge    = rem_shift >= {1'b0, div_den};

  logic [64:0] quot_rounded;
  logic        quot_inc;
  localparam logic [64:0] STEP_RAW_MAX = 65'd2147483647;  // step_t positive max
  assign quot_inc     = {div_rem, 1'b0} >= {2'b00, div_den};
  assign quot_rounded = {1'b0, div_quot} + quot_inc;

  step_t mu_q;

  accum_t delta_raw, sum_raw, sum_floor, rounded;
  logic   half_bit, frac_nonzero, round_up;
  sample_t w_rounded;

  localparam accum_t SAMPLE_MAX = accum_t'(32767);
  localparam accum_t SAMPLE_MIN = -accum_t'(32768);

  assign delta_raw = accum_t'(mu_q) * accum_t'(e_n_q) * accum_t'(x_fn_tap);
  assign sum_raw   = (accum_t'(w_n_tap) <<< W_SHIFT) + delta_raw;

  // round-half-to-even (bankers), matching golden Fxp rounding="around":
  // q = floor(sum / 2^W_SHIFT); round up when > half, or == half and q odd.
  assign sum_floor    = sum_raw >>> W_SHIFT;
  assign half_bit     = sum_raw[W_SHIFT-1];     // top fractional bit (>= half)
  assign frac_nonzero = |sum_raw[W_SHIFT-2:0];  // any lower fractional bit
  assign round_up     = half_bit & (frac_nonzero | sum_floor[0]);
  assign rounded      = sum_floor + accum_t'(round_up);

  always_comb begin
    if (rounded > SAMPLE_MAX)       w_rounded = 16'sd32767;
    else if (rounded < SAMPLE_MIN)  w_rounded = 16'sh8000;
    else                            w_rounded = sample_t'(rounded[15:0]);
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      state        <= S_IDLE;
      tap_cnt      <= '0;
      e_n_q        <= '0;
      e_acc        <= '0;
      div_den      <= '0;
      div_rem      <= '0;
      div_quot     <= '0;
      div_cnt      <= '0;
      mu_q         <= '0;
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

        S_DIV_SETUP: begin
          div_den  <= e_acc + EPS_ACC;
          div_rem  <= '0;
          div_quot <= '0;
          div_cnt  <= '1;
          state    <= S_DIV;
        end

        S_DIV: begin
          div_rem  <= div_ge ? rem_sub : rem_shift;
          div_quot <= {div_quot[62:0], div_ge};
          if (div_cnt == '0) begin
            state <= S_DIV_ROUND;
          end else begin
            div_cnt <= div_cnt - 1'b1;
          end
        end

        S_DIV_ROUND: begin
          if (quot_rounded > STEP_RAW_MAX) begin
            mu_q         <= step_t'(STEP_RAW_MAX[31:0]);
            mu_saturated <= 1'b1;
          end else begin
            mu_q <= step_t'(quot_rounded[31:0]);
          end
          tap_cnt <= '0;
          state   <= S_UPD_READ;
        end

        S_UPD_READ: begin
          w_new_tap <= w_rounded;
          w_wr_en   <= 1'b1;  // high through the write cycle
          state     <= S_UPD_WRITE;
        end

        S_UPD_WRITE: begin
          if (tap_cnt == TAP_CNT_W'(TAP_LEN - 1)) begin
            state       <= S_IDLE;
            w_n_updated <= 1'b1;
          end else begin
            tap_cnt <= tap_cnt + 1'b1;
            state   <= S_UPD_READ;
          end
        end

        default: state <= S_IDLE;
      endcase
    end
  end

  /* verilator tracing_on */
endmodule