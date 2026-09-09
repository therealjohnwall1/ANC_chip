// S_hat FIR + x_f line -- secondary-path estimate filter
//
// Computes x_f[n] = sum_{m=0}^{N_S-1} s_hat[m] * x[n-m]  (INCLUDES x[n], per
// golden/README.md section 3) as one N_S-cycle MAC pass per sample, pushes the
// result into an x_f history line, and presents the W FIR update window to
// error_block:
//
//     x_fn_tap = x_f[n-1-tap_idx]     (tap_idx k -> x_f[n-1-k])
//
// s_hat[m] lives in an FF regfile (same structure as the weights) behind a raw
// write port and a readback port -- the memory-level MCU interface. A full
// MMIO/command wrapper is a later feature; this exposes the memory itself.
//
// Sequencing:
//   x_n is only the *new* reference value one edge after sample_in asserts
//   x_n_new, so the tap-line capture is staged one cycle (S_CAP). x_f[n] is
//   ready ~N_S+3 cycles after x_n_new (x_f_ready pulse). error_block must run
//   its update pass for sample n AFTER x_f_ready, so its window is x_f[n-1-k]
//   and not one sample stale. x_f_valid is a level arm for that gate.
//
// N_S == N_W == TAP_LEN today (see globals). The golden model supports N_S !=
// N_W; split these when that config lands.

module s_hat_fir
  import globals::*;
(
    input logic clk,
    input logic rst_n,

    // reference sample from sample_in
    input logic x_n_new,
    input sample_t x_n,

    // x_f window scan for error_block: k -> x_f[n-1-k]
    input logic [$clog2(TAP_LEN)-1:0] tap_idx,
    output xf_t x_fn_tap,

    output logic x_f_ready,  // one-cycle pulse: x_f[n] computed and pushed
    output logic x_f_valid,  // level: at least one x_f in the line

    // s_hat[] load / readback
    input logic [$clog2(TAP_LEN)-1:0] sh_wr_sel,
    input sample_t sh_wr_data,
    input logic sh_wr_en,
    input logic [$clog2(TAP_LEN)-1:0] sh_rd_sel,
    output sample_t sh_rd_data
);

  localparam int     S_TAP_LEN     = TAP_LEN;                    // N_S for now
  localparam int     S_TAP_CNT_W   = $clog2(S_TAP_LEN);
  localparam int     XF_LINE_DEPTH = TAP_LEN + 1;                // golden: xf_line depth = N_W + 1

  // x_f conversion: products are Q1.15 * Q1.15 = Q2.30; x_f is Q2.14, XF_FRAC
  // frac bits. Drop 30-14 = 16 bits with round-half-away-from-zero, then
  // saturate to the 16-bit signed x_f range.
  localparam int     SAMPLE_FRAC   = WORD_LEN - 1;
  localparam int     XF_SHIFT      = 2 * SAMPLE_FRAC - XF_FRAC;  // 16
  localparam accum_t XF_MAX        = accum_t'(32767);
  localparam accum_t XF_MIN        = -accum_t'(32768);

  // tap line: [0] = x[n], shift register
  sample_t x_line[S_TAP_LEN];
  // filtered line: [0] = x_f[n], depth N_W+1 so tap_idx+1 stays in range
  xf_t xf_line[XF_LINE_DEPTH];

  logic [S_TAP_CNT_W-1:0] fir_cnt;
  accum_t acc;

  // s_hat memory + its A-port scan during the MAC pass
  logic [S_TAP_CNT_W-1:0] sh_scan;
  sample_t sh_scan_out;

  regfile #(
      .DEPTH(S_TAP_LEN),
      .WIDTH(WORD_LEN)
  ) u_shat_mem (
      .clk      (clk),
      .rst_n    (rst_n),
      .rd_sel_a (sh_scan),
      .rd_data_a(sh_scan_out),
      .rd_sel_b (sh_rd_sel),
      .rd_data_b(sh_rd_data),
      .rd_sel_c ('0),
      .rd_data_c(),
      .wr_sel   (sh_wr_sel),
      .wr_data  (sh_wr_data),
      .wr_en    (sh_wr_en)
  );

  sample_t x_cur;
  assign x_cur   = x_line[fir_cnt];
  assign sh_scan = fir_cnt;

  // x_f window presented to error_block
  logic [$clog2(XF_LINE_DEPTH)-1:0] win_idx;
  assign win_idx  = $unsigned(tap_idx) + 1'b1;
  assign x_fn_tap = xf_line[win_idx];

  // quantize acc -> xf_t, round-half-to-even (golden Fxp rounding="around")
  accum_t xf_floor, xf_rounded;
  logic xf_half, xf_frac_nonzero, xf_roundup;
  assign xf_floor        = acc >>> XF_SHIFT;
  assign xf_half         = acc[XF_SHIFT-1];
  assign xf_frac_nonzero = |acc[XF_SHIFT-2:0];
  assign xf_roundup      = xf_half & (xf_frac_nonzero | xf_floor[0]);
  assign xf_rounded      = xf_floor + accum_t'(xf_roundup);

  xf_t xf_out;
  always_comb begin
    if (xf_rounded > XF_MAX) xf_out = xf_t'(XF_MAX[15:0]);
    else if (xf_rounded < XF_MIN) xf_out = xf_t'(XF_MIN[15:0]);
    else xf_out = xf_t'(xf_rounded[15:0]);
  end

  typedef enum logic [1:0] {
    S_IDLE,
    S_CAP,
    S_FIR,
    S_ROUND
  } state_t;
  state_t state;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      state     <= S_IDLE;
      fir_cnt   <= '0;
      acc       <= '0;
      x_f_ready <= 1'b0;
      x_f_valid <= 1'b0;
    end else begin
      x_f_ready <= 1'b0;

      case (state)
        S_IDLE: begin
          // x_n_new while busy is dropped; sample deadline/overrun tracking is
          // a later bring-up FSM feature.
          if (x_n_new) state <= S_CAP;
        end

        // capture x[n] into the tap line one edge after x_n_new, when the
        // sample_in x_n output is guaranteed to hold the new sample
        S_CAP: begin
          x_line[0] <= x_n;
          for (int m = S_TAP_LEN - 1; m >= 1; m--) x_line[m] <= x_line[m-1];
          acc     <= '0;
          fir_cnt <= '0;
          state   <= S_FIR;
        end

        S_FIR: begin
          acc <= acc + accum_t'(sh_scan_out) * accum_t'(x_cur);
          if (fir_cnt == S_TAP_CNT_W'(S_TAP_LEN - 1)) state <= S_ROUND;
          else fir_cnt <= fir_cnt + 1'b1;
        end

        S_ROUND: begin
          for (int k = XF_LINE_DEPTH - 1; k >= 1; k--) xf_line[k] <= xf_line[k-1];
          xf_line[0] <= xf_out;
          x_f_ready  <= 1'b1;
          x_f_valid  <= 1'b1;
          state      <= S_IDLE;
        end

        default: state <= S_IDLE;
      endcase
    end
  end

endmodule
