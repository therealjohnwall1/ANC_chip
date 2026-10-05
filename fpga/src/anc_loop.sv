// anc_loop.sv -- closed-loop FxLMS controller around anchor_top.
//
// Reference x comes from the on-board PDM mic (Q1.15). The error mic is
// *simulated*: the primary path P(z) and secondary path S(z) are FIR
// difference equations (matmuls over the golden plant coefficients). So:
//
//   d[n]   = P(z) x[n]          (noise at the, simulated, error mic)
//   y[n]   = anchor_top W(z) x  (anti-noise, chip output)
//   y_s[n] = S(z) y[n]
//   e[n]   = d[n] - y_s[n]      (residual, fed back to the chip)
//
// s_hat[] is preloaded with S_TAPS (a perfect secondary-path estimate) so the
// chip's filtered-x update points the right way. The first ANC_PRIME samples
// only prime the tap lines (no e), mirroring the self-test.
//
// Drives the same parallel sample ports as self_test so top.sv can mux it in.

module anc_loop
  import globals::*;
(
    input logic clk,
    input logic rst_n,

    // reference sample (from the mic)
    input  logic signed [15:0] x_in,
    input  logic x_valid,
    input  logic [31:0] sample_idx_in,
    output logic ready,

    // to anchor_top (shared sample/coefficient ports)
    output logic adc_valid,
    output logic [INPUT_WIDTH-1:0] adc_data,
    output logic err_valid,
    output logic [INPUT_WIDTH-1:0] err_data,
    output logic [$clog2(TAP_LEN)-1:0] sh_wr_sel,
    output sample_t sh_wr_data,
    output logic sh_wr_en,
    output logic [$clog2(TAP_LEN)-1:0] w_rd_sel,

    // from anchor_top
    input accum_t y_n,
    input logic y_n_ready,
    input logic x_f_ready,
    input logic w_n_updated,

    // result of one processed sample (for the live stream)
    output logic signed [15:0] x_out,
    output logic signed [15:0] d_out,
    output logic signed [15:0] y_out,
    output logic signed [15:0] e_out,
    output logic out_valid,
    output logic [31:0] sample_idx_out
);

  localparam int PRIME = 32;
  localparam int TAP_W = $clog2(TAP_LEN);

  // golden plant coefficients (Q1.15): P from gen_anc_vectors p_taps,
  // S from s_taps (0.4*0.75^k).
  localparam logic signed [15:0] P_TAPS[0:5] =
      '{16'sd19661, 16'sd13763, 16'sd9634, 16'sd6744, 16'sd4721, 16'sd3304};
  localparam logic signed [15:0] S_TAPS[0:31] = '{
      16'sd13107, 16'sd9830, 16'sd7373, 16'sd5530,
      16'sd4147,  16'sd3110, 16'sd2333, 16'sd1750,
      16'sd1312,  16'sd984,  16'sd738,  16'sd554,
      16'sd415,   16'sd311,  16'sd234,  16'sd175,
      16'sd131,   16'sd99,   16'sd74,   16'sd55,
      16'sd42,    16'sd31,   16'sd23,   16'sd18,
      16'sd13,    16'sd10,   16'sd7,    16'sd6,
      16'sd4,     16'sd3,    16'sd2,    16'sd2
  };

  // ---- helpers ----
  function automatic logic signed [15:0] sat16(input logic signed [63:0] v);
    if (v > 64'sd32767) sat16 = 16'sd32767;
    else if (v < -64'sd32768) sat16 = -16'sd32768;
    else sat16 = v[15:0];
  endfunction

  // Q1.15 -> 12-bit offset binary (invert MSB, keep bits [14:4])
  function automatic logic [INPUT_WIDTH-1:0] q15_to_code(input logic signed [15:0] p);
    q15_to_code = {~p[15], p[14:4]};
  endfunction

  // ---- plant FIRs ----
  logic d_start, s_start;
  logic signed [15:0] d_val, ysec_val;
  logic d_done, s_done;

  plant_fir #(
      .NTAP(6),
      .TAPS(P_TAPS)
  ) u_P (
      .clk(clk), .rst_n(rst_n), .start(d_start), .x_in(x_in),
      .y_out(d_val), .done(d_done)
  );

  logic signed [15:0] y_reg;
  plant_fir #(
      .NTAP(32),
      .TAPS(S_TAPS)
  ) u_S (
      .clk(clk), .rst_n(rst_n), .start(s_start), .x_in(y_reg),
      .y_out(ysec_val), .done(s_done)
  );

  // ---- FSM ----
  typedef enum logic [3:0] {
    ST_LD_SEL, ST_LD_WR,
    ST_IDLE, ST_X, ST_WY, ST_SSTART, ST_WS, ST_WXF, ST_WE, ST_WW, ST_EMIT
  } st_e;

  st_e state;
  logic [TAP_W-1:0] ld_idx;
  logic [5:0] prime_cnt;
  logic prime;
  logic signed [15:0] x_reg, e_reg;
  logic [31:0] idx_reg;
  logic signed [15:0] e_next;
  logic d_seen, s_seen, xf_seen;  // latch pulses that may arrive before we wait

  assign ready = (state == ST_IDLE);
  assign e_next = sat16(d_val - ysec_val);

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      state <= ST_LD_SEL;
      ld_idx <= '0;
      prime <= 1'b1;
      prime_cnt <= '0;
      adc_valid <= 1'b0;
      adc_data <= '0;
      err_valid <= 1'b0;
      err_data <= '0;
      sh_wr_sel <= '0;
      sh_wr_data <= '0;
      sh_wr_en <= 1'b0;
      w_rd_sel <= '0;
      d_start <= 1'b0;
      s_start <= 1'b0;
      x_reg <= '0;
      y_reg <= '0;
      e_reg <= '0;
      idx_reg <= '0;
      x_out <= '0;
      d_out <= '0;
      y_out <= '0;
      e_out <= '0;
      out_valid <= 1'b0;
      sample_idx_out <= '0;
      d_seen <= 1'b0;
      s_seen <= 1'b0;
      xf_seen <= 1'b0;
    end else begin
      adc_valid <= 1'b0;
      err_valid <= 1'b0;
      sh_wr_en <= 1'b0;
      d_start <= 1'b0;
      s_start <= 1'b0;
      out_valid <= 1'b0;

      // latch handshake pulses (they may fire before the FSM reaches its wait)
      if (d_done) d_seen <= 1'b1;
      if (s_done) s_seen <= 1'b1;
      if (x_f_ready) xf_seen <= 1'b1;

      case (state)
        // ---- load s_hat = S_TAPS ----
        ST_LD_SEL: begin
          sh_wr_sel  <= ld_idx;
          sh_wr_data <= S_TAPS[ld_idx];
          sh_wr_en   <= 1'b1;
          state      <= ST_LD_WR;
        end
        ST_LD_WR: begin
          if (ld_idx == TAP_LEN - 1) begin
            prime <= 1'b1;
            prime_cnt <= '0;
            state <= ST_IDLE;
          end else begin
            ld_idx <= ld_idx + 1'b1;
            state <= ST_LD_SEL;
          end
        end

        // ---- one sample ----
        ST_IDLE: begin
          if (x_valid) begin
            x_reg     <= x_in;
            idx_reg   <= sample_idx_in;
            adc_data  <= q15_to_code(x_in);
            adc_valid <= 1'b1;
            d_start   <= 1'b1;
            d_seen    <= 1'b0;
            s_seen    <= 1'b0;
            xf_seen   <= 1'b0;
            state     <= ST_X;
          end
        end
        ST_X: begin
          if (y_n_ready) begin
            y_reg <= sat16(y_n >>> 15);
            state <= ST_SSTART;
          end
        end
        ST_SSTART: begin
          s_start <= 1'b1;
          state   <= ST_WS;
        end
        ST_WS: begin
          if ((s_done || s_seen) && (d_done || d_seen)) state <= ST_WXF;
        end
        ST_WXF: begin
          if (x_f_ready || xf_seen) begin
            e_reg <= e_next;
            if (prime) begin
              state <= ST_EMIT;
            end else begin
              err_data  <= q15_to_code(e_next);
              err_valid <= 1'b1;
              state     <= ST_WE;
            end
          end
        end
        ST_WE: begin
          if (w_n_updated) state <= ST_EMIT;
        end
        ST_EMIT: begin
          x_out          <= x_reg;
          d_out          <= d_val;
          y_out          <= y_reg;
          e_out          <= e_reg;
          sample_idx_out <= idx_reg;
          out_valid      <= 1'b1;  // pulse every sample (prime included)
          if (prime) begin
            if (prime_cnt == PRIME - 1) begin
              prime <= 1'b0;
            end else begin
              prime_cnt <= prime_cnt + 1'b1;
            end
          end
          state <= ST_IDLE;
        end
        default: state <= ST_LD_SEL;
      endcase
    end
  end

endmodule
