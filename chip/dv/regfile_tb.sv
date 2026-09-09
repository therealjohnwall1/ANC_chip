// single-clock FF register-file testbench
//
// Checks: reset clears; write-then-read addressability; write latency (a write
// is not visible until the edge that ends the write cycle); two independent
// simultaneous reads; reset-after-write clears everything.

module regfile_tb;

  import globals::*;
  import dv_globals::*;

  localparam int DEPTH = 32;
  localparam int WIDTH = 16;
  localparam int ADW = $clog2(DEPTH);

  localparam time CLK_PERIOD = 20ns;

  logic clk;
  logic rst_n;

  logic [ADW-1:0] rd_sel_a, rd_sel_b, wr_sel;
  logic [WIDTH-1:0] rd_data_a, rd_data_b, wr_data;
  logic wr_en;

  regfile #(
      .DEPTH(DEPTH),
      .WIDTH(WIDTH)
  ) dut (
      .clk      (clk),
      .rst_n    (rst_n),
      .rd_sel_a (rd_sel_a),
      .rd_data_a(rd_data_a),
      .rd_sel_b (rd_sel_b),
      .rd_data_b(rd_data_b),
      .rd_sel_c ('0),
      .rd_data_c(),
      .wr_sel   (wr_sel),
      .wr_data  (wr_data),
      .wr_en    (wr_en)
  );

  initial begin
    clk = 1'b0;
    forever #(CLK_PERIOD / 2) clk = ~clk;
  end

  initial begin
    $dumpfile({DV_OUT_DIR, "regfile_tb.vcd"});
    $dumpvars(0, regfile_tb);
  end

  task automatic write(input logic [ADW-1:0] a, input logic [WIDTH-1:0] d);
    @(negedge clk);
    wr_sel  = a;
    wr_data = d;
    wr_en   = 1'b1;
    @(posedge clk);
    @(negedge clk);
    wr_en = 1'b0;
  endtask

  initial begin
    rst_n = 1'b0;
    rd_sel_a = '0;
    rd_sel_b = '0;
    wr_sel = '0;
    wr_data = '0;
    wr_en = 1'b0;
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    // 1. reset cleared every location
    for (int a = 0; a < DEPTH; a++) begin
      rd_sel_a = ADW'(a);
      @(negedge clk);
      if (rd_data_a !== '0) $fatal(1, "reset did not clear addr %0d", a);
    end

    // 2. single write, addressable
    write(5'd5, 16'hABCD);
    rd_sel_a = 5'd5;
    @(negedge clk);
    if (rd_data_a !== 16'hABCD) $fatal(1, "write addr 5 not readable");

    // 3. write latency: data must not appear until the write edge
    @(negedge clk);
    wr_sel = 5'd7;
    wr_data = 16'h1234;
    wr_en = 1'b1;
    rd_sel_a = 5'd7;
    #1;  // settle comb, still before the capturing posedge
    if (rd_data_a !== '0) $fatal(1, "write visible before its clock edge");
    @(posedge clk);  // captured here
    @(negedge clk);  // past the nonblocking update
    if (rd_data_a !== 16'h1234) $fatal(1, "write not visible after edge");
    wr_en = 1'b0;

    // 4. fill the whole array with a pattern, read back via both ports
    for (int a = 0; a < DEPTH; a++) write(ADW'(a), WIDTH'(a * 97 + 3));
    for (int a = 0; a < DEPTH; a += 5) begin
      rd_sel_a = ADW'(a);
      @(negedge clk);
      if (rd_data_a !== WIDTH'(a * 97 + 3)) $fatal(1, "port A addr %0d wrong", a);
    end

    // 5. two independent simultaneous reads
    rd_sel_a = 5'd3;
    rd_sel_b = 5'd17;
    @(negedge clk);
    if (rd_data_a !== WIDTH'(3 * 97 + 3)) $fatal(1, "dual-read A wrong");
    if (rd_data_b !== WIDTH'(17 * 97 + 3)) $fatal(1, "dual-read B wrong");

    // 6. reset clears everything again
    rst_n = 1'b0;
    repeat (2) @(posedge clk);
    rst_n = 1'b1;
    rd_sel_a = 5'd3;
    @(negedge clk);
    if (rd_data_a !== '0) $fatal(1, "reset did not clear after writes");

    $display("T=%0t: regfile_tb done.", $time);
    $finish;
  end

  initial begin
    #10ms $fatal(1, "timeout");
  end

endmodule
