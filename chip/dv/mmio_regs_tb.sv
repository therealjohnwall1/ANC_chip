// ============================================================================
// mmio_regs_tb.sv  --  unit test for mmio_regs   (HAND-WRITE THIS FILE)
// ============================================================================
//
// WHAT THIS FILE DOES
// -------------------
// This is the unit testbench for the mmio_regs block you are implementing in
// chip/rtl/mmio_regs.sv. It drives the transaction interface directly (wr/rd/
// addr/wdata, reads back rdata) and feeds a fake anchor_top so you can check
// the register bank in isolation, before it is wired to the SPI bridge.
//
// Write it the same way the other dv testbenches are written (see
// regfile_tb.sv for the closest example): instantiate mmio_regs, clock it,
// and poke the ports.
//
// Things you MUST verify:
//   1. writing X  pulses adc_valid for exactly one cycle and latches adc_data
//   2. writing E  pulses err_valid for exactly one cycle and latches err_data
//   3. writing SH_IDX then SH_DATA drives sh_wr_sel/sh_wr_data/sh_wr_en, and
//      SH_IDX auto-increments after the SH_DATA write
//   4. rdata is combinational: set w_rd_sel/sh_rd_sel/y_n, read the matching
//      registers (W_DATA, SH_DATA, Y_0..Y_3) and check the value
//   5. STATUS done-bits latch on y_n_ready/w_n_updated/x_f_ready pulses, stay
//      high after the pulse, and clear (with irq) on CMD.CLEAR_FLAGS
//   6. irq asserts when a done-bit latches and deasserts after CLEAR_FLAGS
//
// A good pattern for pulse width checks: use a counter in a fork that samples
// adc_valid over several cycles and asserts it is high exactly once.
// ============================================================================

module mmio_regs_tb;

  import globals::*;
  import dv_globals::*;

  localparam time CLK_PERIOD = 20ns;

  logic clk;
  logic rst_n;

  // transaction interface
  logic wr, rd;
  logic [6:0] addr;
  logic [15:0] wdata, rdata;

  // anchor_top ADC sample inputs
  logic adc_valid, err_valid;
  logic [INPUT_WIDTH-1:0] adc_data, err_data;

  // anchor_top s_hat
  logic [$clog2(TAP_LEN)-1:0] sh_wr_sel;
  sample_t sh_wr_data;
  logic sh_wr_en;
  logic [$clog2(TAP_LEN)-1:0] sh_rd_sel;
  sample_t sh_rd_data;

  // anchor_top weight readback
  logic [$clog2(TAP_LEN)-1:0] w_rd_sel;
  sample_t w_rd_data;

  // anchor_top status / outputs
  accum_t y_n;
  logic y_n_ready, w_n_updated, x_f_ready, x_f_valid, mu_saturated;

  logic irq;

  mmio_regs dut (
      .clk         (clk),
      .rst_n       (rst_n),
      .wr          (wr),
      .rd          (rd),
      .addr        (addr),
      .wdata       (wdata),
      .rdata       (rdata),
      .adc_valid   (adc_valid),
      .adc_data    (adc_data),
      .err_valid   (err_valid),
      .err_data    (err_data),
      .sh_wr_sel   (sh_wr_sel),
      .sh_wr_data  (sh_wr_data),
      .sh_wr_en    (sh_wr_en),
      .sh_rd_sel   (sh_rd_sel),
      .sh_rd_data  (sh_rd_data),
      .w_rd_sel    (w_rd_sel),
      .w_rd_data   (w_rd_data),
      .y_n         (y_n),
      .y_n_ready   (y_n_ready),
      .w_n_updated (w_n_updated),
      .x_f_ready   (x_f_ready),
      .x_f_valid   (x_f_valid),
      .mu_saturated(mu_saturated),
      .irq         (irq)
  );

  initial begin
    clk = 1'b0;
    forever #(CLK_PERIOD / 2) clk = ~clk;
  end

  initial begin
    // TODO: your test sequence here. See the header comment above for the
    // cases to cover (X/E pulse generation, SH auto-increment, combinational
    // rdata, sticky STATUS + CLEAR_FLAGS, irq).
    rst_n = 1'b0;
    wr = 1'b0;
    rd = 1'b0;
    addr = '0;
    wdata = '0;
    sh_rd_data = '0;
    w_rd_data = '0;
    y_n = '0;
    y_n_ready = 1'b0;
    w_n_updated = 1'b0;
    x_f_ready = 1'b0;
    x_f_valid = 1'b0;
    mu_saturated = 1'b0;
    repeat (4) @(posedge clk);
    rst_n = 1'b1;

    // TODO: add checks, then:
    $display("T=%0t: mmio_regs_tb done", $time);
    $finish;
  end

endmodule
