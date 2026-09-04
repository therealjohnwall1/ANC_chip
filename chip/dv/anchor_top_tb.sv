// anchor_top integration testbench -- full FxLMS datapath, scripted playback.
//
// Drives anchor_top with a precomputed (x, e) stream and checks, after every
// sample, that y[n] matches golden/core.py's accumulator bit-for-bit and that
// the final weights match. There is no top-level FSM yet, so this TB encodes
// golden's stage order (core.sample_step): push x -> W FIR (old weights) ->
// S_hat FIR -> then feed e -> weight update.
//
// Stimulus + reference come from chip/dv/gen_anc_vectors.py (committed).

module anchor_top_tb;

  import globals::*;
  import dv_globals::*;

  `include "anc_params.svh"

  localparam time CLK_PERIOD = 20ns;
  localparam int HW = $clog2(HIST_DEPTH);

  logic clk;
  logic rst_n;

  // x (reference) adc
  logic adc_valid;
  logic [INPUT_WIDTH-1:0] adc_data;
  logic adc_rdy;
  // e (error) adc
  logic err_valid;
  logic [INPUT_WIDTH-1:0] err_data;
  logic err_rdy;

  // taps / observability
  sample_t x_n;
  logic x_n_new;
  logic [HW-1:0] head_idx;
  sample_t x_tap_out;
  accum_t y_n;
  logic y_n_ready;
  logic w_n_updated;
  logic mu_saturated;
  logic x_f_ready;
  logic x_f_valid;

  // w[] readback
  logic [$clog2(TAP_LEN)-1:0] w_rd_sel;
  sample_t w_rd_data;

  // s_hat[] load
  logic [$clog2(TAP_LEN)-1:0] sh_wr_sel;
  sample_t sh_wr_data;
  logic sh_wr_en;
  logic [$clog2(TAP_LEN)-1:0] sh_rd_sel;
  sample_t sh_rd_data;

  anchor_top dut (
    .clk          (clk),
    .rst_n        (rst_n),
    .adc_valid    (adc_valid),
    .adc_data     (adc_data),
    .adc_rdy      (adc_rdy),
    .err_valid    (err_valid),
    .err_data     (err_data),
    .err_rdy      (err_rdy),
    .x_n          (x_n),
    .x_n_new      (x_n_new),
    .head_idx     (head_idx),
    .x_tap_out    (x_tap_out),
    .y_n          (y_n),
    .y_n_ready    (y_n_ready),
    .w_n_updated  (w_n_updated),
    .mu_saturated (mu_saturated),
    .x_f_ready    (x_f_ready),
    .x_f_valid    (x_f_valid),
    .w_rd_sel     (w_rd_sel),
    .w_rd_data    (w_rd_data),
    .sh_wr_sel    (sh_wr_sel),
    .sh_wr_data   (sh_wr_data),
    .sh_wr_en     (sh_wr_en),
    .sh_rd_sel    (sh_rd_sel),
    .sh_rd_data   (sh_rd_data)
  );

  // ---- vectors ----
  logic [INPUT_WIDTH-1:0] x_code[ANC_PRIME + ANC_N];
  logic [INPUT_WIDTH-1:0] e_code[ANC_N];
  accum_t y_exp[ANC_N];
  sample_t w_exp[TAP_LEN];
  sample_t s_hat[TAP_LEN];

  initial begin
    clk = 1'b0;
    forever #(CLK_PERIOD/2) clk = ~clk;
  end

  initial begin
    $dumpfile({DV_OUT_DIR, "anchor_top_tb.vcd"});
    // $dumpvars takes whole module scopes, so dump only the small leaf modules
    // of interest and skip error_block (divider toggles every cycle = bloat).
    $dumpvars(0, dut.u_weights);     // adaptive weights w[]
    $dumpvars(0, dut.u_anti_noise);  // anti-noise output y_n (cancelling signal)
    $dumpvars(0, dut.u_s_hat_fir);   // filtered reference x_f
  end

  // ---- driver tasks ----

  task automatic load_shat();
    for (int k = 0; k < TAP_LEN; k++) begin
      @(negedge clk);
      sh_wr_sel  = k[$clog2(TAP_LEN)-1:0];
      sh_wr_data = s_hat[k];
      sh_wr_en   = 1'b1;
      @(posedge clk);
      @(negedge clk);
      sh_wr_en = 1'b0;
    end
  endtask

  // prime: push one x with no e, then wait for the S_hat FIR to finish so the
  // filtered-reference line is filled before adaptation starts
  task automatic prime_sample(input int ci);
    @(negedge clk);
    adc_data  = x_code[ci];
    adc_valid = 1'b1;
    @(posedge clk);
    @(negedge clk);
    adc_valid = 1'b0;
    for (int c = 0; c < 80; c++) begin
      @(posedge clk);
      if (x_f_ready) return;
    end
    $fatal(1, "prime: x_f_ready never asserted (ci=%0d)", ci);
  endtask

  // one full sample: x in -> capture y -> x_f ready -> e in -> weights updated
  task automatic run_sample(input int xi, input int ei, input int idx);
    int c;
    // x
    @(negedge clk);
    adc_data  = x_code[xi];
    adc_valid = 1'b1;
    @(posedge clk);
    @(negedge clk);
    adc_valid = 1'b0;

    // anti-noise FIR -> y[n] (old weights), checked against golden
    for (c = 0; c < 100; c++) begin
      @(posedge clk);
      if (y_n_ready) break;
    end
    if (c == 100) $fatal(1, "y_n_ready never asserted (idx=%0d)", idx);
    if (y_n !== y_exp[idx])
      $fatal(1, "y[%0d] = %h, expected %h", idx, y_n, y_exp[idx]);

    // wait S_hat FIR so error_block's x_f window is the current sample's
    for (c = 0; c < 100; c++) begin
      @(posedge clk);
      if (x_f_ready) break;
    end
    if (c == 100) $fatal(1, "x_f_ready never asserted (idx=%0d)", idx);

    // e -> NLMS update
    @(negedge clk);
    err_data  = e_code[ei];
    err_valid = 1'b1;
    @(posedge clk);
    @(negedge clk);
    err_valid = 1'b0;

    for (c = 0; c < 400; c++) begin
      @(posedge clk);
      if (w_n_updated) break;
    end
    if (c == 400) $fatal(1, "w_n_updated never asserted (idx=%0d)", idx);
  endtask

  initial begin
    $readmemh("anc_x.mem",    x_code);
    $readmemh("anc_e.mem",    e_code);
    $readmemh("anc_y.mem",    y_exp);
    $readmemh("anc_w.mem",    w_exp);
    $readmemh("anc_shat.mem", s_hat);
  end

  initial begin
    rst_n = 1'b0;
    adc_valid = 1'b0; adc_data = '0;
    err_valid = 1'b0; err_data = '0;
    sh_wr_sel = '0; sh_wr_data = '0; sh_wr_en = 1'b0; sh_rd_sel = '0;
    w_rd_sel = '0;
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    load_shat();
    $display("loaded s_hat[0..%0d] via sh_wr", TAP_LEN - 1);

    // prime ANC_PRIME samples into the tap lines
    for (int j = 0; j < ANC_PRIME; j++)
      prime_sample(j);
    if (x_f_valid !== 1'b1) $fatal(1, "x_f_valid never went high after priming");
    $display("primed %0d samples", ANC_PRIME);

    // run the adaptation, checking y[n] each sample
    for (int j = 0; j < ANC_N; j++) begin
      run_sample(ANC_PRIME + j, j, j);
      if ((j & 511) == 511)
        $display("  sample %0d/%0d  y_n=%0d  e_q11=%0d", j + 1, ANC_N, y_n,
                 $signed({~err_data[11], err_data[10:0]}));
    end

    // final weights
    for (int k = 0; k < TAP_LEN; k++) begin
      w_rd_sel = k[$clog2(TAP_LEN)-1:0];
      @(negedge clk);
      if (w_rd_data !== w_exp[k])
        $fatal(1, "w[%0d] = %h, expected %h", k, w_rd_data, w_exp[k]);
    end

    $display("T=%0t: anchor_top_tb done -- %0d samples, y bit-exact vs golden, final w[] match.", $time, ANC_N);
    $finish;
  end

  initial begin
    #(ANC_N * 400 * 20) $fatal(1, "timeout"); // generous upper bound
  end

endmodule