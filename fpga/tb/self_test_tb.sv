// self_test_tb.sv -- Verilator testbench for the on-chip self-test.
//
// Instantiates self_test + a single anchor_top (no SPI path) and runs until
// done, then checks the self-test's pass bit. This is a local pre-Vivado
// check that the FSM sequencing and golden checksum match.
//
// Run: fpga/tb/run.sh

module self_test_tb;
  import globals::*;
  import selftest_params::*;

  localparam time CLK_PERIOD = 10ns;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #(CLK_PERIOD / 2) clk = ~clk;

  // self_test
  logic adc_valid, err_valid;
  logic [INPUT_WIDTH-1:0] adc_data, err_data;
  logic [$clog2(TAP_LEN)-1:0] sh_wr_sel, w_rd_sel;
  sample_t sh_wr_data;
  logic sh_wr_en;
  logic busy, done, pass;

  // anchor_top
  logic [$clog2(TAP_LEN)-1:0] sh_rd_sel;
  sample_t sh_rd_data;
  logic adc_rdy, err_rdy, x_n_new;
  sample_t x_n, x_tap_out;
  logic [$clog2(HIST_DEPTH)-1:0] head_idx;
  accum_t y_n;
  logic y_n_ready, w_n_updated, x_f_ready, x_f_valid, mu_saturated;
  sample_t w_rd_data;

  self_test u_st (
      .clk        (clk),
      .rst_n      (rst_n),
      .run        (1'b1),
      .adc_valid  (adc_valid),
      .adc_data   (adc_data),
      .err_valid  (err_valid),
      .err_data   (err_data),
      .sh_wr_sel  (sh_wr_sel),
      .sh_wr_data (sh_wr_data),
      .sh_wr_en   (sh_wr_en),
      .w_rd_sel   (w_rd_sel),
      .w_rd_data  (w_rd_data),
      .y_n        (y_n),
      .y_n_ready  (y_n_ready),
      .x_f_ready  (x_f_ready),
      .w_n_updated(w_n_updated),
      .busy       (busy),
      .done       (done),
      .pass       (pass)
  );

  anchor_top u_anchor (
      .clk         (clk),
      .rst_n       (rst_n),
      .adc_valid   (adc_valid),
      .adc_data    (adc_data),
      .adc_rdy     (adc_rdy),
      .err_valid   (err_valid),
      .err_data    (err_data),
      .err_rdy     (err_rdy),
      .x_n         (x_n),
      .x_n_new     (x_n_new),
      .head_idx    (head_idx),
      .x_tap_out   (x_tap_out),
      .y_n         (y_n),
      .y_n_ready   (y_n_ready),
      .w_n_updated (w_n_updated),
      .mu_saturated(mu_saturated),
      .w_rd_sel    (w_rd_sel),
      .w_rd_data   (w_rd_data),
      .x_f_ready   (x_f_ready),
      .x_f_valid   (x_f_valid),
      .sh_wr_sel   (sh_wr_sel),
      .sh_wr_data  (sh_wr_data),
      .sh_wr_en    (sh_wr_en),
      .sh_rd_sel   (sh_rd_sel),
      .sh_rd_data  (sh_rd_data)
  );

  assign sh_rd_sel = '0;

  initial begin
    $dumpfile("out/self_test_tb.vcd");
    $dumpvars(0, self_test_tb);
  end

  initial begin
    rst_n = 1'b0;
    repeat (8) @(posedge clk);
    rst_n = 1'b1;
  end

  initial begin
    wait (done === 1'b1);
    $display("self_test done: pass=%b busy=%b (ANC_N=%0d, Y_CHK=%h)", pass, busy, ANC_N,
             Y_CHK);
    if (pass !== 1'b1) begin
      $display("FAIL: self-test mismatch");
      $fatal(1);
    end
    $display("PASS: self-test bit-exact vs golden");
    $finish;
  end

  initial begin
    #(20ms);
    $display("TIMEOUT: self-test never finished");
    $fatal(1);
  end

endmodule
