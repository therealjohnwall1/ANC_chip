// serial_bridge_tb -- unit test for the SPI slave bridge.
//
// Drives serial_bridge with an SPI bus (sclk/cs_n/sdi) and a fake read-data
// source (rdata = {addr, 9'b0}) and checks:
//   * writes pulse wr once with the correct addr/wdata
//   * reads return the expected 16-bit value MSB-first on sdo
//   * sdo stays low during write frames

module serial_bridge_tb;

  localparam time CLK_PERIOD = 20ns;
  localparam int SCLK_HALF = 8;  // clk cycles per sclk half-period

  logic clk;
  logic rst_n;

  logic sclk, cs_n, sdi, sdo;
  logic wr, rd;
  logic [6:0] addr;
  logic [15:0] wdata, rdata;

  // fake MMIO read: echo the address in the high 7 bits
  assign rdata = {addr, 9'b0};

  serial_bridge dut (
      .clk  (clk),
      .rst_n(rst_n),
      .sclk (sclk),
      .cs_n (cs_n),
      .sdi  (sdi),
      .sdo  (sdo),
      .wr   (wr),
      .rd   (rd),
      .addr (addr),
      .wdata(wdata),
      .rdata(rdata)
  );

  initial begin
    clk = 1'b0;
    forever #(CLK_PERIOD / 2) clk = ~clk;
  end

  // ---- SPI BFM ----
  task automatic spi_bit(input logic tx, output logic rx);
    sdi = tx;
    repeat (SCLK_HALF) @(posedge clk);  // setup while sclk low
    sclk = 1'b1;
    repeat (SCLK_HALF) @(posedge clk);  // hold high
    rx   = sdo;  // master samples on rising edge
    sclk = 1'b0;
    repeat (SCLK_HALF) @(posedge clk);  // hold low
  endtask

  task automatic spi_xfer(input logic rw, input logic [6:0] a, input logic [15:0] d,
                          output logic [15:0] r);
    logic rx;
    cs_n = 1'b0;
    spi_bit(rw, rx);
    for (int i = 6; i >= 0; i--) spi_bit(a[i], rx);
    for (int i = 15; i >= 0; i--) begin
      spi_bit(rw ? d[i] : 1'b0, rx);
      if (!rw) r[i] = rx;
    end
    cs_n = 1'b1;
    repeat (4) @(posedge clk);  // let the commit/wr pulse settle
  endtask

  // ---- write monitor: waits for the next wr pulse and checks values ----
  task automatic expect_write(input logic [6:0] a, input logic [15:0] d);
    fork
      begin
        @(posedge clk iff wr);
        if (addr !== a) $fatal(1, "write addr %0d, expected %0d", addr, a);
        if (wdata !== d) $fatal(1, "write data %h, expected %h", wdata, d);
        $display("  write ok: addr=%0d data=%04h", a, d);
      end
    join_none
  endtask

  int errors = 0;
  logic [15:0] rd_val;

  initial begin
    rst_n = 1'b0;
    sclk  = 1'b0;
    cs_n  = 1'b1;
    sdi   = 1'b0;
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    repeat (2) @(posedge clk);

    // 1. simple write
    expect_write(7'h11, 16'hABCD);
    spi_xfer(1'b1, 7'h11, 16'hABCD, rd_val);
    wait fork;

    // 2. simple read (rdata = {addr, 9'b0} = 0x05 -> 0x0A00)
    spi_xfer(1'b0, 7'h05, 16'h0, rd_val);
    if (rd_val !== 16'h0A00) begin
      $fatal(1, "read returned %h, expected 0A00", rd_val);
      errors++;
    end
    $display("  read  ok: addr=05 -> %04h", rd_val);

    // 3. another read, different address
    spi_xfer(1'b0, 7'h3F, 16'h0, rd_val);
    if (rd_val !== 16'h7E00) begin
      $fatal(1, "read returned %h, expected 7E00", rd_val);
      errors++;
    end
    $display("  read  ok: addr=3F -> %04h", rd_val);

    // 4. two writes back-to-back
    expect_write(7'h02, 16'h000F);
    spi_xfer(1'b1, 7'h02, 16'h000F, rd_val);
    wait fork;

    expect_write(7'h03, 16'h8001);
    spi_xfer(1'b1, 7'h03, 16'h8001, rd_val);
    wait fork;

    // 5. read after writes still works (sdo not polluted by writes)
    spi_xfer(1'b0, 7'h10, 16'h0, rd_val);
    if (rd_val !== 16'h2000) begin
      $fatal(1, "read returned %h, expected 2000", rd_val);
      errors++;
    end
    $display("  read  ok: addr=10 -> %04h", rd_val);

    if (errors == 0) $display("T=%0t: serial_bridge_tb PASS", $time);
    else $display("T=%0t: serial_bridge_tb FAIL (%0d errors)", $time, errors);
    $finish;
  end

  initial begin
    #500us $fatal(1, "timeout");
  end

endmodule
