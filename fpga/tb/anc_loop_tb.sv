// anc_loop_tb.sv -- Verilator check for the closed-loop FxLMS controller.
//
// Feeds the golden x stimulus (src/mem/x.mem) as the reference and checks that
// the simulated-error residual e is attenuated vs the un-cancelled d.

module anc_loop_tb;
  import globals::*;
  import selftest_params::*;

  localparam time CLK_PERIOD = 40ns;  // 25 MHz
  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #(CLK_PERIOD / 2) clk = ~clk;

  // anc_loop <-> anchor_top
  logic signed [15:0] mic_x = '0;
  logic mic_valid = 1'b0;
  logic [31:0] mic_idx = '0;
  logic ready;
  logic adc_valid, err_valid;
  logic [INPUT_WIDTH-1:0] adc_data, err_data;
  logic [$clog2(TAP_LEN)-1:0] sh_wr_sel, w_rd_sel;
  sample_t sh_wr_data;
  logic sh_wr_en;
  logic [$clog2(TAP_LEN)-1:0] sh_rd_sel = '0;
  sample_t sh_rd_data;
  accum_t y_n;
  logic y_n_ready, x_f_ready, w_n_updated;
  logic signed [15:0] xo, do_, yo, eo;
  logic out_valid;
  logic [31:0] idxo;

  // anchor_top extras
  logic adc_rdy, err_rdy, x_n_new, x_f_valid, mu_saturated;
  sample_t x_n, x_tap_out, w_rd_data;
  logic [$clog2(HIST_DEPTH)-1:0] head_idx;

  anc_loop u_loop (
      .clk(clk), .rst_n(rst_n),
      .x_in(mic_x), .x_valid(mic_valid), .sample_idx_in(mic_idx), .ready(ready),
      .adc_valid(adc_valid), .adc_data(adc_data),
      .err_valid(err_valid), .err_data(err_data),
      .sh_wr_sel(sh_wr_sel), .sh_wr_data(sh_wr_data), .sh_wr_en(sh_wr_en),
      .w_rd_sel(w_rd_sel),
      .y_n(y_n), .y_n_ready(y_n_ready), .x_f_ready(x_f_ready), .w_n_updated(w_n_updated),
      .x_out(xo), .d_out(do_), .y_out(yo), .e_out(eo),
      .out_valid(out_valid), .sample_idx_out(idxo)
  );

  anchor_top u_anchor (
      .clk(clk), .rst_n(rst_n),
      .adc_valid(adc_valid), .adc_data(adc_data), .adc_rdy(adc_rdy),
      .err_valid(err_valid), .err_data(err_data), .err_rdy(err_rdy),
      .x_n(x_n), .x_n_new(x_n_new), .head_idx(head_idx), .x_tap_out(x_tap_out),
      .y_n(y_n), .y_n_ready(y_n_ready), .w_n_updated(w_n_updated),
      .mu_saturated(mu_saturated),
      .w_rd_sel(w_rd_sel), .w_rd_data(w_rd_data),
      .x_f_ready(x_f_ready), .x_f_valid(x_f_valid),
      .sh_wr_sel(sh_wr_sel), .sh_wr_data(sh_wr_data), .sh_wr_en(sh_wr_en),
      .sh_rd_sel(sh_rd_sel), .sh_rd_data(sh_rd_data)
  );

  logic [INPUT_WIDTH-1:0] xcode[0:X_LEN-1];

  function automatic logic signed [15:0] code_to_q15(input logic [INPUT_WIDTH-1:0] c);
    logic s;
    logic [15:0] raw;
    begin
      s   = ~c[INPUT_WIDTH-1];
      raw = {s, c[INPUT_WIDTH-2:0], {(WORD_LEN-INPUT_WIDTH) {1'b0}}};
      code_to_q15 = raw;
    end
  endfunction

  int n = 0;
  real sumd = 0.0, sume = 0.0;

  task automatic feed(input int i);
    while (!ready) @(posedge clk);
    @(negedge clk);
    mic_x     = code_to_q15(xcode[i]);
    mic_idx   = i;
    mic_valid = 1'b1;
    @(posedge clk);
    @(negedge clk);
    mic_valid = 1'b0;
  endtask

  initial begin
    $readmemh("x.mem", xcode);
  end

  initial begin
    rst_n = 1'b0;
    repeat (8) @(posedge clk);
    rst_n = 1'b1;

    for (int i = 0; i < X_LEN; i++) begin
      feed(i);
      while (!out_valid) @(posedge clk);
      if (i >= 64) begin  // skip priming + settle
        sumd += real'(do_) * real'(do_);
        sume += real'(eo) * real'(eo);
        n++;
      end
    end

    $display("anc_loop_tb: samples=%0d  E[d^2]=%.3e  E[e^2]=%.3e  att=%.2f dB",
             n, sumd, sume, 10.0 * $log10(sumd / sume));
    if (sume > sumd * 0.5) $fatal(1, "residual not attenuated (e ~= d)");
    $display("PASS: loop cancels the simulated error");
    $finish;
  end

  initial begin
    #(200ms);
    $fatal(1, "timeout");
  end

endmodule
