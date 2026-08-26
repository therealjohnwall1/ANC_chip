module sample_in_tb;

  import globals::*;
  import dv_globals::*;

  localparam int CYC_TB = 4;

  localparam time CLK_PERIOD = 20ns;

  logic clk;
  logic rst_n;
  logic data_rdy;
  logic data_valid;
  logic [INPUT_WIDTH-1:0] data_in;
  sample_t x_n;
  logic x_n_new;
  logic [$clog2(globals::TAP_LEN)-1:0] head_idx;
  logic [$clog2(globals::TAP_LEN)-1:0] exp_head = '0;

  sample_in dut (
    .clk       (clk),
    .rst_n     (rst_n),
    .data_rdy  (data_rdy),
    .data_valid(data_valid),
    .data_in   (data_in),
    .x_n       (x_n),
    .x_n_new   (x_n_new),
    .head_idx  (head_idx)
  );

  initial begin
    clk = 1'b0;
    forever #(CLK_PERIOD/2) clk = ~clk;
  end

  initial begin
    $dumpfile({DV_OUT_DIR, "sample_in_tb.vcd"});
    $dumpvars(0, sample_in_tb);
  end

  task automatic drive_sample(input logic [INPUT_WIDTH-1:0] d,
                              input logic rdy,
                              input logic val);
    data_in   <= d;
    data_rdy  <= rdy;
    data_valid<= val;
    @(posedge clk);
    @(negedge clk);
    data_rdy   <= 1'b0;
    data_valid <= 1'b0;
  endtask

  function automatic sample_t expected(input logic [INPUT_WIDTH-1:0] d);
    return sample_t'({~d[INPUT_WIDTH-1], d[INPUT_WIDTH-2:0], {(WORD_LEN-INPUT_WIDTH){1'b0}}});
  endfunction

  initial begin
    rst_n <= 1'b0;
    data_rdy <= 1'b0;
    data_valid <= 1'b0;
    data_in <= '0;
    repeat (4) @(posedge clk);
    rst_n <= 1'b1;

    repeat (CYC_TB) @(posedge clk);

    $display("T=%0t: reset released", $time);
    if (head_idx !== '0) $fatal(1, "reset did not clear head_idx");

    @(negedge clk);
    data_in <= 12'h123;
    data_rdy <= 1'b0;
    data_valid <= 1'b1;
    @(posedge clk); @(negedge clk);
    data_rdy <= 1'b0;
    data_valid <= 1'b0;
    assert (x_n_new === 1'b0) else
      $fatal(1, "x_n_new fired with data_rdy low");
    if (x_n !== '0) $display("note: x_n changed on non-valid handshake");
    if (head_idx !== '0) $fatal(1, "head_idx advanced on non-valid handshake");

    repeat (CYC_TB) @(posedge clk);

    drive_sample(12'h7FF, 1'b1, 1'b1);
    if (x_n !== expected(12'h7FF)) $fatal(1, "x_n wrong for 0x7FF");
    if (x_n_new !== 1'b1) $fatal(1, "x_n_new not strobed");
    exp_head++;
    if (head_idx !== exp_head) $fatal(1, "head_idx != %0d", exp_head);
    $display("T=%0t: 0x7FF -> x_n=%h x_n_new=%0d", $time, x_n, x_n_new);

    repeat (CYC_TB) @(posedge clk);
    if (x_n_new !== 1'b0) $fatal(1, "x_n_new not one-cycle");

    drive_sample(12'h800, 1'b1, 1'b1);
    if (x_n !== expected(12'h800)) $fatal(1, "x_n wrong for 0x800");
    exp_head++;
    if (head_idx !== exp_head) $fatal(1, "head_idx != %0d", exp_head);
    $display("T=%0t: 0x800 -> x_n=%h", $time, x_n);

    repeat (CYC_TB) @(posedge clk);

    drive_sample(12'h000, 1'b1, 1'b1);
    if (x_n !== expected(12'h000)) $fatal(1, "x_n wrong for 0x000");
    exp_head++;
    if (head_idx !== exp_head) $fatal(1, "head_idx != %0d", exp_head);
    $display("T=%0t: 0x000 -> x_n=%h", $time, x_n);

    repeat (CYC_TB) @(posedge clk);

    drive_sample(12'hFFF, 1'b1, 1'b1);
    if (x_n !== expected(12'hFFF)) $fatal(1, "x_n wrong for 0xFFF");
    exp_head++;
    if (head_idx !== exp_head) $fatal(1, "head_idx != %0d", exp_head);
    $display("T=%0t: 0xFFF -> x_n=%h", $time, x_n);

    repeat (CYC_TB) @(posedge clk);

    rst_n <= 1'b0;
    repeat (2) @(posedge clk);
    if (x_n !== '0) $fatal(1, "reset did not clear x_n");
    if (x_n_new !== 1'b0) $fatal(1, "reset did not clear x_n_new");
    if (head_idx !== '0) $fatal(1, "reset did not clear head_idx");
    $display("T=%0t: reset cleared x_n/x_n_new", $time);
    rst_n <= 1'b1;

    $display("T=%0t: TB done.", $time);
    $finish;
  end

  initial begin
    #10ms $fatal(1, "timeout");
  end

endmodule
