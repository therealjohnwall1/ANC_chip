module anti_noise_tb;

  import globals::*;
  import dv_globals::*;

  localparam int N = TAP_LEN;
  localparam time CLK_PERIOD = 20ns;

  logic clk;
  logic rst_n;
  logic x_n_new;
  logic [$clog2(N)-1:0] head_idx;

  logic [$clog2(N)-1:0] x_tap_sel;
  logic [$clog2(N)-1:0] w_tap_sel;
  sample_t x_tap_out;
  sample_t w_tap_out;
  accum_t y_n;
  logic y_n_ready;

  // mimic the sample_in / weights blocks: memories indexed by the tap selects
  sample_t x_hist[N];
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

  // expected y(n) = sum_k w_taps[k] * x_hist[(head_idx-1-k) mod N]
  function automatic accum_t expected_y(input int h_idx);
    accum_t acc;
    int idx;
    acc = '0;
    for (int k = 0; k < N; k++) begin
      idx = (h_idx - 1 - k) % N;
      if (idx < 0) idx += N;
      acc += $signed(64'(w_taps[k])) * $signed(64'(x_hist[idx]));
    end
    return acc;
  endfunction

  // pulse x_n_new for one cycle at head_idx=h_idx, then wait for y_n_ready
  task automatic run_one(input int h_idx, input int exp_num);
    accum_t exp;
    int seen;

    exp = expected_y(h_idx);

    @(negedge clk);
    head_idx = h_idx[$clog2(N)-1:0];
    x_n_new  = 1'b1;
    @(negedge clk);
    x_n_new  = 1'b0;

    // poll until ready (it should come within N+2 cycles)
    seen = 0;
    for (int c = 0; c < N + 2; c++) begin
      @(posedge clk);
      if (y_n_ready) begin
        seen++;
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

    // fill histories with deterministic signed values including extremes
    for (int i = 0; i < N; i++) begin
      x_hist[i] = sample_t'($signed((i * 37) - 700));
      w_taps[i] = sample_t'($signed((i * 53) + 11));
    end
    x_hist[3]  = 16'sh7FFF;
    x_hist[19] = 16'sh8000; // most negative Q1.15
    w_taps[7]  = 16'sh8000;

    run_one(0,  1);
    run_one(31, 2); // wrap-around index, newest sample = head-1 = 30
    run_one(17, 3); // arbitrary head, exercises differencing

    // zero-weight case must give y(n)=0 regardless of x
    for (int i = 0; i < N; i++) w_taps[i] = '0;
    run_one(5, 4);

    // all-zero x case must give y(n)=0 regardless of w
    for (int i = 0; i < N; i++) begin
      w_taps[i] = sample_t'($signed((i * 11) + 3));
      x_hist[i] = '0;
    end
    run_one(9, 5);

    $display("T=%0t: TB done.", $time);
    $finish;
  end

  initial begin
    #20ms $fatal(1, "timeout");
  end

endmodule
