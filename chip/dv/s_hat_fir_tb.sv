// s_hat_fir testbench -- secondary-path FIR and x_f window.
//
// Loads s_hat[] through the write port, verifies readback, then feeds a known
// x[n] sequence, checking x_f_ready timing and reading the window back through
// x_fn_tap to confirm x_f[n] = sum s_hat[m]*x[n-m] lands in xf_line[k+1].
// Expected x_f values come from chip/dv/gen_vectors.py (golden model).

module s_hat_fir_tb;

  import globals::*;
  import dv_globals::*;

  `include "s_hat_fir_vectors.svh"

  localparam time CLK_PERIOD = 20ns;
  localparam int TAP_CNT_W = $clog2(TAP_LEN);

  logic clk;
  logic rst_n;

  logic x_n_new;
  sample_t x_n;
  logic [TAP_CNT_W-1:0] tap_idx;
  xf_t x_fn_tap;
  logic x_f_ready;
  logic x_f_valid;

  logic [TAP_CNT_W-1:0] sh_wr_sel;
  sample_t sh_wr_data;
  logic sh_wr_en;
  logic [TAP_CNT_W-1:0] sh_rd_sel;
  sample_t sh_rd_data;

  s_hat_fir dut (
    .clk        (clk),
    .rst_n      (rst_n),
    .x_n_new    (x_n_new),
    .x_n        (x_n),
    .tap_idx    (tap_idx),
    .x_fn_tap   (x_fn_tap),
    .x_f_ready  (x_f_ready),
    .x_f_valid  (x_f_valid),
    .sh_wr_sel  (sh_wr_sel),
    .sh_wr_data (sh_wr_data),
    .sh_wr_en   (sh_wr_en),
    .sh_rd_sel  (sh_rd_sel),
    .sh_rd_data (sh_rd_data)
  );

  initial begin
    clk = 1'b0;
    forever #(CLK_PERIOD/2) clk = ~clk;
  end

  initial begin
    $dumpfile({DV_OUT_DIR, "s_hat_fir_tb.vcd"});
    $dumpvars(0, s_hat_fir_tb);
  end

  task automatic load_shat();
    for (int k = 0; k < TAP_LEN; k++) begin
      @(negedge clk);
      sh_wr_sel  = TAP_CNT_W'(k);
      sh_wr_data = sample_t'(SH_S[k]);
      sh_wr_en   = 1'b1;
      @(posedge clk);
      @(negedge clk);
      sh_wr_en = 1'b0;
      // readback, one address behind the write for pacing
      sh_rd_sel = TAP_CNT_W'(k);
      if (sh_rd_data !== sample_t'(SH_S[k]))
        $fatal(1, "s_hat[%0d] readback = %h, expected %h", k, sh_rd_data, SH_S[k]);
    end
  endtask

  // feed one sample; when x_f_ready fires, verify window[0] == x_f[j-1]
  task automatic feed_sample(input int j);
    int c;
    @(negedge clk);
    x_n     = sample_t'(SH_X[j]);
    x_n_new = 1'b1;
    @(posedge clk);
    @(negedge clk);
    x_n_new = 1'b0;

    for (c = 0; c < 100; c++) begin
      @(posedge clk);
      if (x_f_ready) begin
        if (j >= 1) begin
          tap_idx = '0;
          #1;
          if (x_fn_tap !== xf_t'(SH_XF[j-1]))
            $fatal(1, "x_f[%0d] = %h, expected %h (window[0])",
                   j-1, x_fn_tap, SH_XF[j-1]);
        end
        // x_f_ready must be a one-cycle pulse
        @(posedge clk);
        if (x_f_ready !== 1'b0) $fatal(1, "x_f_ready not one-cycle (j=%0d)", j);
        return;
      end
    end
    $fatal(1, "x_f_ready never asserted (j=%0d)", j);
  endtask

  initial begin
    rst_n = 1'b0;
    x_n_new = 1'b0; x_n = '0;
    tap_idx = '0;
    sh_wr_sel = '0; sh_wr_data = '0; sh_wr_en = 1'b0; sh_rd_sel = '0;
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    if (x_f_valid !== 1'b0) $fatal(1, "x_f_valid high after reset");
    if (x_f_ready !== 1'b0) $fatal(1, "x_f_ready high after reset");

    load_shat();
    $display("load_shat: 32 coefficients written and read back");

    // feed all samples; incremental check verifies each x_f[k-1] as it lands
    for (int j = 0; j < SH_M; j++)
      feed_sample(j);

    if (x_f_valid !== 1'b1) $fatal(1, "x_f_valid never went high");

    // full sweep of the window: after the last sample, x_fn_tap[t] = x_f[M-2-t]
    for (int t = 0; t < TAP_LEN; t++) begin
      tap_idx = TAP_CNT_W'(t);
      @(negedge clk);
      if (x_fn_tap !== xf_t'(SH_XF[SH_M - 2 - t]))
        $fatal(1, "window[%0d] = %h, expected x_f[%0d] = %h",
               t, x_fn_tap, SH_M - 2 - t, SH_XF[SH_M - 2 - t]);
    end

    $display("T=%0t: s_hat_fir_tb done.", $time);
    $finish;
  end

  initial begin
    #100ms $fatal(1, "timeout");
  end

endmodule