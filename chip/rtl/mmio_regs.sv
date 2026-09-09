// ============================================================================
// mmio_regs.sv  --  MCU MMIO register bank for anchor   (HAND-WRITE THIS FILE)
// ============================================================================
//
// WHAT THIS FILE DOES
// -------------------
// This is the memory-mapped register block that sits between the SPI bridge
// (serial_bridge.sv) and the ANC datapath (anchor_top.sv). It implements the
// "MCU command and MMIO register interface" feature from overview.md.
//
// The SPI bridge (already written) turns serial traffic into a 4-wire
// transaction:
//
//     wr    : 1-cycle write strobe (high for exactly one clk cycle)
//     rd    : 1-cycle read  strobe (optional; see note below)
//     addr  : 7-bit register address (stable while wr/rd is high)
//     wdata : 16-bit write data (valid while wr is high)
//     rdata : 16-bit read data (must be COMBINATIONAL from addr)
//
// Your job: implement the register map below, wire the registers to
// anchor_top's parallel ports, and generate the two single-cycle handshake
// pulses that anchor_top expects:
//
//   * writing REG_X must pulse adc_valid for one cycle (adc_data held)
//   * writing REG_E must pulse err_valid for one cycle (err_data held)
//
// REGISTER MAP (16-bit words, 7-bit address)
// ----------------------------------------------------------------------------
// 0x00 CMD      W   bit0 RUN, bit1 PRIME, bit2 CLEAR_COEFFS, bit3 MUTE,
//                   bit4 CLEAR_FLAGS  (bits 0-3 reserved for a future FSM)
// 0x01 STATUS   R   bit0 y_n_ready,  bit1 w_n_updated, bit2 x_f_valid,
//                   bit3 x_f_ready,   bit4 mu_saturated, bit5 fault
// 0x02 X        W   adc_data[11:0]  -> pulse adc_valid
// 0x03 E        W   err_data[11:0]  -> pulse err_valid
// 0x04 Y_0      R   y_n[15:0]
// 0x05 Y_1      R   y_n[31:16]
// 0x06 Y_2      R   y_n[47:32]
// 0x07 Y_3      R   y_n[63:48]
// 0x10 SH_IDX   W   s_hat write/read index (0..TAP_LEN-1)
// 0x11 SH_DATA  R/W s_hat data; auto-increments SH_IDX after the access
// 0x20 W_IDX    W   weight readback index (0..TAP_LEN-1)
// 0x21 W_DATA   R   weight readback; auto-increments W_IDX after the read
//
// BEHAVIORAL REQUIREMENTS
// ------------------------
// 1. adc_valid/err_valid are ONE-CYCLE pulses (they are not levels).
//    They fire on the clk edge that sees the X/E write, and the corresponding
//    adc_data/err_data must be held stable at that same edge.
//
// 2. STATUS done-bits are STICKY. anchor_top pulses y_n_ready, w_n_updated
//    and x_f_ready for a single cycle, so you must latch them until the MCU
//    clears them. Clear them (and deassert irq) when the MCU writes CMD with
//    CLEAR_FLAGS set. Do NOT clear on read: that races with the SPI bridge,
//    which latches rdata a few cycles after the read strobe.
//
// 3. x_f_valid and mu_saturated can be read through as levels (they are not
//    single-cycle), but keeping them sticky too is harmless as long as
//    CLEAR_FLAGS resets them.
//
// 4. SH_IDX / W_IDX auto-increment AFTER the corresponding DATA register is
//    accessed (after a write to SH_DATA, after a read from W_DATA), not after
//    the index write itself.
//
// 5. rdata must be COMBINATIONAL from addr (a case/mux on addr). The SPI
//    bridge latches rdata into its shift register based only on addr.
//    For SH_DATA read, rdata = sh_rd_data at SH_IDX. For W_DATA read,
//    rdata = w_rd_data at W_IDX. For Y_*, slice y_n. For STATUS, the sticky
//    bits. Anything else returns 0.
//
// 6. irq is a level: high when any sticky done-bit is set, low after
//    CLEAR_FLAGS. You may also OR in the fault bit.
//
// 7. RUN/PRIME/CLEAR_COEFFS/MUTE (CMD bits 0-3) have no datapath connection
//    yet (anchor_top has no bring-up FSM). Store them if you like, but they
//    are not required for the current testbench. CLEAR_FLAGS (bit 4) IS used.
//
// NOTE: the datapath (anchor_top), the SPI bridge (serial_bridge) and the TT
// top (tt_um_anchor) are already written. This is the only file you fill in.
// DO NOT change the port list -- tt_um_anchor instantiates it by name.
// ============================================================================

module mmio_regs
  import globals::*;
(
  input  logic clk,
  input  logic rst_n,

  // ---- transaction interface (driven by serial_bridge) ----
  input  logic        wr,      // 1-cycle write strobe
  input  logic        rd,      // 1-cycle read strobe (optional)
  input  logic [6:0]  addr,    // register address (stable while wr/rd high)
  input  logic [15:0] wdata,   // write data
  output logic [15:0] rdata,   // read data (COMBINATIONAL from addr)

  // ---- anchor_top: ADC sample inputs (x and e) ----
  output logic adc_valid,
  output logic [INPUT_WIDTH-1:0] adc_data,
  output logic err_valid,
  output logic [INPUT_WIDTH-1:0] err_data,

  // ---- anchor_top: s_hat load / readback ----
  output logic [$clog2(TAP_LEN)-1:0] sh_wr_sel,
  output sample_t sh_wr_data,
  output logic sh_wr_en,
  output logic [$clog2(TAP_LEN)-1:0] sh_rd_sel,
  input  sample_t sh_rd_data,

  // ---- anchor_top: weight readback ----
  output logic [$clog2(TAP_LEN)-1:0] w_rd_sel,
  input  sample_t w_rd_data,

  // ---- anchor_top: status / outputs ----
  input  accum_t y_n,
  input  logic   y_n_ready,
  input  logic   w_n_updated,
  input  logic   x_f_ready,
  input  logic   x_f_valid,
  input  logic   mu_saturated,

  output logic   irq
);

  // ==========================================================================
  // TODO: YOUR IMPLEMENTATION HERE.
  //
  // Suggested register/flag declarations:
  //   logic [4:0] sh_idx;   // s_hat index (auto-inc)
  //   logic [4:0] w_idx;    // weight index (auto-inc)
  //   logic       y_rdy_l, w_upd_l, xf_rdy_l;  // sticky done bits
  //   ...
  //
  // Suggested structure:
  //   always_ff @(posedge clk) for adc_valid/err_valid pulse gen,
  //       sh_wr_en pulse, sticky latch, auto-increment, and CLEAR_FLAGS.
  //   always_comb (case on addr) for rdata.
  // ==========================================================================

  // placeholder so the module simulates without Xs -- delete and implement.
  assign rdata     = '0;
  assign adc_valid = 1'b0;
  assign adc_data  = '0;
  assign err_valid = 1'b0;
  assign err_data  = '0;
  assign sh_wr_sel = '0;
  assign sh_wr_data= '0;
  assign sh_wr_en  = 1'b0;
  assign sh_rd_sel = '0;
  assign w_rd_sel  = '0;
  assign irq       = 1'b0;

endmodule
