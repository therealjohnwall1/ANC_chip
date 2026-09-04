module anti_noise_tb;

  import globals::*;
  import dv_globals::*;

  localparam int N  = TAP_LEN;               // number of W taps
  localparam int HW = $clog2(HIST_DEPTH);    // history index width (6)
  localparam time CLK_PERIOD = 20ns;

  logic clk;
  logic rst_n;
  logic x_n_new;
  logic [HW-1:0] head_idx;

  logic [HW-1:0] x_tap_sel;
  logic [$clog2(N)-1:0] w_tap_sel;
  sample_t x_tap_out;
  sample_t w_tap_out;
  accum_t y_n;
  logic y_n_ready;

  // mimic the sample_in / weights blocks: memories indexed by the tap selects.
  // head_idx points just PAST the newest sample, so x_hist[head-1] = x[n].
  sample_t x_hist[HIST_DEPTH];
  sample_t w_taps[N];
  assign x_tap_out = x_hist[x_tap_sel];
  assign w_tap_out = w_taps[w_tap_sel];

  anti_noise dut (
    .clk       (clk),
    .rst_n     (rst_n),
    .x_n_new   (x_n_new),
    .head_idx  (head_idx),
    .x_tap_out (x_tap_out),
    .w_tap_out (w_tap_out),
    .x_tap_sel (x_tap_sel),
    .w_tap_sel (w_tap_sel),
    .y_n       (y_n),
    .y_n_ready (y_n_ready)
  );

  initial begin
    clk = 1'b0;
    forever #(CLK_PERIOD/2) clk = ~clk;
  end

  initial begin
    $dumpfile({DV_OUT_DIR, "anti_noise_tb.vcd"});
    $dumpvars(0, anti_noise_tb);
  end

  // y(n) = sum_k w_taps[k] * x[n-1-k]  (golden/fir.py w_window convention).
  // x[n] = x_hist[head-1], so x[n-1-k] = x_hist[head-2-k].
  function automatic accum_t expected_y(input int h_idx);
    accum_t acc;
    int idx;
    acc = '0;
    for (int k = 0; k < N; k++) begin
      idx = (h_idx - 2 - k) % HIST_DEPTH;
      if (idx < 0) idx += HIST_DEPTH;
      acc += 64'(w_taps[k]) * 64'(x_hist[idx]);
    end
    return acc;
  endfunction

  task automatic run_one(input int h_idx, input int exp_num);
    accum_t exp;
    exp = expected_y(h_idx);

    @(negedge clk);
    head_idx = h_idx[$clog2(HIST_DEPTH)-1:0];
    x_n_new  = 1'b1;
    @(negedge clk);
    x_n_new  = 1'b0;

    for (int c = 0; c < N + 2; c++) begin
      @(posedge clk);
      if (y_n_ready) begin
        $display("T=%0t  case %0d head=%0d  y_n=%0h  exp=%0h", $time, exp_num, h_idx, y_n, exp);
        if (y_n !== exp) $fatal(1, "case %0d: y_n mismatch", exp_num);
        @(posedge clk);
        if (y_n_ready !== 1'b0) $fatal(1, "case %0d: y_n_ready not one-cycle", exp_num);
        return;
      end
    end
    $fatal(1, "case %0d: y_n_ready never asserted (acc=%0d)", exp_num, dut.acc_num);
  endtask

  initial begin
    rst_n <= 1'b0;
    x_n_new <= 1'b0;
    head_idx <= '0;
    repeat (4) @(posedge clk);
    rst_n <= 1'b1;
    repeat (2) @(posedge clk);

    if (y_n_ready !== 1'b0) $fatal(1, "y_n_ready high while idle");

    // fill histories with deterministic signed values, including extremes
    for (int i = 0; i < HIST_DEPTH; i++)
      x_hist[i] = sample_t'($signed((i * 37) - 700));
    for (int i = 0; i < N; i++)
      w_taps[i] = sample_t'($signed((i * 53) + 11));
    x_hist[20] = 16'sh7FFF;
    x_hist[30] = 16'sh8000; // most negative Q1.15
    w_taps[7]  = 16'sh8000;

    run_one(0,  1);   // wrap-around: head-1 = index 63 = newest
    run_one(40, 2);   // no wrap; reaches x[38..7]
    run_one(31, 3);   // arbitrary head, exercises differencing + wrap

    // zero-weight case must give y(n)=0 regardless of x
    for (int i = 0; i < N; i++) w_taps[i] = '0;
    run_one(5, 4);

    // all-zero x case must give y(n)=0 regardless of w
    for (int i = 0; i < N; i++)
      w_taps[i] = sample_t'($signed((i * 11) + 3));
    for (int i = 0; i < HIST_DEPTH; i++) x_hist[i] = '0;
    run_one(9, 5);

    $display("T=%0t: TB done.", $time);
    $finish;
  end

  initial begin
    #20ms $fatal(1, "timeout");
  end

endmodule