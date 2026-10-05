// spi_host_tb.sv -- Verilator testbench for the PmodA SPI/MMIO host path.
//
// Instantiates `top`, forces SW0=1 (SPI mode), and bit-bangs the 24-bit SPI
// protocol from serial_bridge to:
//   - load s_hat[] via SH_IDX/SH_DATA
//   - prime (ANC_PRIME x samples, no e)
//   - run ANC_N samples: write X, poll STATUS, read Y_0..3 (folding y into
//     the same rotate-xor checksum as self_test), poll x_f_ready, write E,
//     poll w_n_updated, clear flags
//   - read back W[] and compare to golden, and compare the y checksum to Y_CHK
//
// SPI mode 0, MSB-first. sclk runs at 1/(2*TSPI) = 2.5 MHz (<= clk/4 = 6.25 MHz).
//
// Run: fpga/tb/run.sh spi

module spi_host_tb;
  import globals::*;
  import selftest_params::*;

  localparam time CLK_PERIOD = 10ns;  // 100 MHz -> top divides to 25 MHz
  localparam time TSPI = 200ns;       // sclk half-period (2.5 MHz)

  logic clk = 1'b0;
  always #(CLK_PERIOD / 2) clk = ~clk;

  logic [3:0] btn = '0;
  logic [11:0] sw = 12'h001;  // SW0=1 -> SPI mode
  logic [9:0] led;
  logic [2:0] rgb_a, rgb_b;
  logic pmod_sclk = 1'b0, pmod_cs_n = 1'b1, pmod_sdi = 1'b0;
  logic pmod_sdo, pmod_irq;

  top dut (
      .clk       (clk),
      .btn       (btn),
      .sw        (sw),
      .led       (led),
      .rgb_a     (rgb_a),
      .rgb_b     (rgb_b),
      .pmod_sclk (pmod_sclk),
      .pmod_cs_n (pmod_cs_n),
      .pmod_sdi  (pmod_sdi),
      .pmod_sdo  (pmod_sdo),
      .pmod_irq  (pmod_irq)
  );

  // vectors
  logic [INPUT_WIDTH-1:0] xv[0:X_LEN-1];
  logic [INPUT_WIDTH-1:0] ev[0:ANC_N-1];
  logic [WORD_LEN-1:0] wv[0:TAP_LEN-1];
  logic [WORD_LEN-1:0] shv[0:TAP_LEN-1];

  // register addresses (see mmio_regs.sv)
  localparam logic [6:0] A_CMD = 7'h00, A_STATUS = 7'h01, A_X = 7'h02, A_E = 7'h03;
  localparam logic [6:0] A_Y0 = 7'h04, A_Y1 = 7'h05, A_Y2 = 7'h06, A_Y3 = 7'h07;
  localparam logic [6:0] A_SHIDX = 7'h10, A_SHDATA = 7'h11;
  localparam logic [6:0] A_WIDX = 7'h20, A_WDATA = 7'h21;

  // ---- SPI host primitives ----
  task automatic spi_frame(input logic rw, input logic [6:0] a, input logic [15:0] din,
                           output logic [15:0] dout);
    logic [23:0] fr;
    fr   = {rw, a, din};
    dout = '0;
    pmod_cs_n = 1'b0;
    pmod_sclk = 1'b0;
    #(TSPI);
    for (int i = 23; i >= 0; i--) begin
      pmod_sdi = fr[i];
      #(TSPI);
      pmod_sclk = 1'b1;  // rising edge: master samples MISO
      #(TSPI / 2);
      if (i <= 15) dout = {dout[14:0], pmod_sdo};
      #(TSPI / 2);
      pmod_sclk = 1'b0;  // falling edge: slave updates MISO
      #(TSPI);
    end
    pmod_cs_n = 1'b1;  // commit writes
    #(TSPI * 2);
  endtask

  task automatic wreg(input logic [6:0] a, input logic [15:0] d);
    logic [15:0] ignored;
    spi_frame(1'b1, a, d, ignored);
    #(200ns);
  endtask

  task automatic rreg(input logic [6:0] a, output logic [15:0] d);
    spi_frame(1'b0, a, 16'h0, d);
    #(200ns);
  endtask

  task automatic wait_status(input logic [15:0] mask);
    logic [15:0] s;
    for (int t = 0; t < 5000; t++) begin
      rreg(A_STATUS, s);
      if ((s & mask) == mask) return;
    end
    $fatal(1, "wait_status timeout: mask=%h", mask);
  endtask

  task automatic clear_flags();
    wreg(A_CMD, 16'h0010);  // CMD.CLEAR_FLAGS
  endtask

  logic [63:0] chk;
  logic [15:0] y0, y1, y2, y3, wd, s;

  initial begin
    $readmemh("x.mem", xv);
    $readmemh("e.mem", ev);
    $readmemh("w.mem", wv);
    $readmemh("shat.mem", shv);

    // let the power-on reset finish (256 clk_anc ~= 10.24 us)
    #(20us);

    // ---- load s_hat[] ----
    for (int k = 0; k < TAP_LEN; k++) begin
      wreg(A_SHIDX, k[15:0]);
      wreg(A_SHDATA, shv[k]);
    end
    $display("s_hat loaded via SPI");

    // ---- prime ----
    for (int j = 0; j < ANC_PRIME; j++) begin
      wreg(A_X, xv[j]);
      wait_status(16'h0001);  // y_n_ready
      wait_status(16'h0008);  // x_f_ready
      clear_flags();
    end
    $display("primed %0d samples via SPI", ANC_PRIME);

    // ---- run ----
    chk = '0;
    for (int j = 0; j < ANC_N; j++) begin
      wreg(A_X, xv[ANC_PRIME + j]);
      wait_status(16'h0001);  // y_n_ready
      rreg(A_Y0, y0);
      rreg(A_Y1, y1);
      rreg(A_Y2, y2);
      rreg(A_Y3, y3);
      chk = {chk[62:0], chk[63]} ^ {y3, y2, y1, y0};
      wait_status(16'h0008);  // x_f_ready
      wreg(A_E, ev[j]);
      wait_status(16'h0002);  // w_n_updated
      clear_flags();
      if ((j & 255) == 255) $display("  sample %0d/%0d", j + 1, ANC_N);
    end

    // ---- verify final w[] ----
    for (int k = 0; k < TAP_LEN; k++) begin
      wreg(A_WIDX, k[15:0]);
      rreg(A_WDATA, wd);
      if (wd !== wv[k]) $fatal(1, "w[%0d] = %h, expected %h", k, wd, wv[k]);
    end

    if (chk !== Y_CHK) $fatal(1, "y checksum = %h, expected %h", chk, Y_CHK);

    $display("PASS: SPI/MMIO host path bit-exact vs golden (w[] + y checksum)");
    $finish;
  end

  initial begin
    #(500ms);
    $display("TIMEOUT");
    $fatal(1);
  end

endmodule
