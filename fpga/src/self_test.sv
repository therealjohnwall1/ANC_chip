// self_test.sv -- on-chip golden-vector self-test for anchor_top.
//
// Replays a reduced (ANC_N sample) x/e stimulus through the anchor_top
// datapath, following the same stage order as chip/dv/anchor_top_tb.sv:
//
//   load s_hat[] -> prime (ANC_PRIME x, wait x_f_ready)
//   for n in 0..ANC_N-1:
//       x -> wait y_n_ready (fold y_n into a 64-bit checksum)
//         -> wait x_f_ready -> e -> wait w_n_updated
//   then read back w[] and compare against the golden final weights.
//
// pass = (all w[] match) && (y checksum == Y_CHK). The vectors and expected
// values come from fpga/scripts/gen_selftest_vectors.py (see src/mem and
// src/pkg/selftest_params.sv). y[n] is not stored: it is folded into the
// rotate-xor checksum, which the generator reproduces exactly.

module self_test
  import globals::*;
  import selftest_params::*;
(
    input logic clk,
    input logic rst_n,
    input logic run,  // level; launches the test once after reset

    // sample injection -> anchor_top (shared with the SPI/MMIO path)
    output logic adc_valid,
    output logic [INPUT_WIDTH-1:0] adc_data,
    output logic err_valid,
    output logic [INPUT_WIDTH-1:0] err_data,

    // s_hat[] load
    output logic [$clog2(TAP_LEN)-1:0] sh_wr_sel,
    output sample_t sh_wr_data,
    output logic sh_wr_en,

    // w[] readback
    output logic [$clog2(TAP_LEN)-1:0] w_rd_sel,
    input sample_t w_rd_data,

    // anchor_top handshakes
    input accum_t y_n,
    input logic y_n_ready,
    input logic x_f_ready,
    input logic w_n_updated,

    output logic busy,
    output logic done,
    output logic pass
);

  localparam int CNT_W = $clog2(X_LEN);
  localparam int TAP_W = $clog2(TAP_LEN);

  // ---- vector ROMs (loaded from src/mem/*.mem at elaboration) ----
  logic [INPUT_WIDTH-1:0] x_rom[0:X_LEN-1];
  logic [INPUT_WIDTH-1:0] e_rom[0:ANC_N-1];
  logic [WORD_LEN-1:0] w_exp[0:TAP_LEN-1];
  logic [WORD_LEN-1:0] shat_rom[0:TAP_LEN-1];

  initial begin
    $readmemh("x.mem", x_rom);
    $readmemh("e.mem", e_rom);
    $readmemh("w.mem", w_exp);
    $readmemh("shat.mem", shat_rom);
  end

  typedef enum logic [3:0] {
    ST_IDLE,
    ST_LD_SEL,
    ST_LD_WR,
    ST_PR_X,
    ST_PR_WAIT,
    ST_X,
    ST_Y,
    ST_XF,
    ST_E,
    ST_W,
    ST_CK_SEL,
    ST_CK_CMP,
    ST_DONE
  } state_e;

  state_e state;
  logic [CNT_W-1:0] idx;
  logic [63:0] chk;
  logic w_ok;
  logic launched;
  logic xf_pending;

  assign busy = (state != ST_IDLE) && (state != ST_DONE);

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      state      <= ST_IDLE;
      idx        <= '0;
      chk        <= '0;
      w_ok       <= 1'b1;
      pass       <= 1'b0;
      done       <= 1'b0;
      launched   <= 1'b0;
      xf_pending <= 1'b0;
      adc_valid  <= 1'b0;
      adc_data   <= '0;
      err_valid  <= 1'b0;
      err_data   <= '0;
      sh_wr_sel  <= '0;
      sh_wr_data <= '0;
      sh_wr_en   <= 1'b0;
      w_rd_sel   <= '0;
    end else begin
      // one-cycle strobes by default
      adc_valid <= 1'b0;
      err_valid <= 1'b0;
      sh_wr_en  <= 1'b0;

      case (state)
        ST_IDLE: begin
          if (run && !launched) begin
            launched <= 1'b1;
            idx      <= '0;
            chk      <= '0;
            w_ok     <= 1'b1;
            state    <= ST_LD_SEL;
          end
        end

        // one write cycle per tap: present, then advance
        ST_LD_SEL: begin
          sh_wr_sel  <= idx[TAP_W-1:0];
          sh_wr_data <= shat_rom[idx[TAP_W-1:0]];
          sh_wr_en   <= 1'b1;
          state      <= ST_LD_WR;
        end
        ST_LD_WR: begin
          if (idx == TAP_LEN - 1) begin
            idx   <= '0;
            state <= ST_PR_X;
          end else begin
            idx   <= idx + 1'b1;
            state <= ST_LD_SEL;
          end
        end

        // prime: push x with no e, wait for the x_f line to fill
        ST_PR_X: begin
          adc_data  <= x_rom[idx];
          adc_valid <= 1'b1;
          state     <= ST_PR_WAIT;
        end
        ST_PR_WAIT: begin
          if (x_f_ready) begin
            if (idx == ANC_PRIME - 1) begin
              idx   <= '0;
              state <= ST_X;
            end else begin
              idx   <= idx + 1'b1;
              state <= ST_PR_X;
            end
          end
        end

        // main loop: x -> y -> x_f -> e -> w
        ST_X: begin
          xf_pending <= 1'b0;
          adc_data   <= x_rom[ANC_PRIME + idx];
          adc_valid  <= 1'b1;
          state      <= ST_Y;
        end
        ST_Y: begin
          if (x_f_ready) xf_pending <= 1'b1;
          if (y_n_ready) begin
            chk   <= {chk[62:0], chk[63]} ^ y_n;
            state <= ST_XF;
          end
        end
        ST_XF: begin
          if (xf_pending || x_f_ready) begin
            xf_pending <= 1'b0;
            state      <= ST_E;
          end
        end
        ST_E: begin
          err_data  <= e_rom[idx];
          err_valid <= 1'b1;
          state     <= ST_W;
        end
        ST_W: begin
          if (w_n_updated) begin
            if (idx == ANC_N - 1) begin
              idx   <= '0;
              state <= ST_CK_SEL;
            end else begin
              idx   <= idx + 1'b1;
              state <= ST_X;
            end
          end
        end

        // verify final w[] and the accumulated y checksum
        ST_CK_SEL: begin
          w_rd_sel <= idx[TAP_W-1:0];
          state    <= ST_CK_CMP;
        end
        ST_CK_CMP: begin
          if (w_rd_data !== w_exp[idx[TAP_W-1:0]]) w_ok <= 1'b0;
          if (idx == TAP_LEN - 1) begin
            state <= ST_DONE;
          end else begin
            idx   <= idx + 1'b1;
            state <= ST_CK_SEL;
          end
        end

        ST_DONE: begin
          done <= 1'b1;
          pass <= w_ok && (chk == Y_CHK);
        end

        default: state <= ST_IDLE;
      endcase
    end
  end

endmodule
