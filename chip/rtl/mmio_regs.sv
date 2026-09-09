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
    input logic clk,
    input logic rst_n,

    // ---- transaction interface (driven by serial_bridge) ----
    input  logic        wr,     // 1-cycle write strobe
    input  logic        rd,     // 1-cycle read strobe (optional)
    input  logic [ 6:0] addr,   // register address (stable while wr/rd high)
    input  logic [15:0] wdata,  // write data
    output logic [15:0] rdata,  // read data (COMBINATIONAL from addr)

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
    input sample_t sh_rd_data,

    // ---- anchor_top: weight readback ----
    output logic [$clog2(TAP_LEN)-1:0] w_rd_sel,
    input sample_t w_rd_data,

    // ---- anchor_top: status / outputs ----
    input accum_t y_n,
    input logic   y_n_ready,
    input logic   w_n_updated,
    input logic   x_f_ready,
    input logic   x_f_valid,
    input logic   mu_saturated,

    output logic irq
);

// ==========================================================================
  // Register address decode constants
  // ==========================================================================
  localparam logic [6:0] ADDR_CMD     = 7'h00;
  localparam logic [6:0] ADDR_STATUS  = 7'h01;
  localparam logic [6:0] ADDR_X       = 7'h02;
  localparam logic [6:0] ADDR_E       = 7'h03;
  localparam logic [6:0] ADDR_Y_0     = 7'h04;
  localparam logic [6:0] ADDR_Y_1     = 7'h05;
  localparam logic [6:0] ADDR_Y_2     = 7'h06;
  localparam logic [6:0] ADDR_Y_3     = 7'h07;
  localparam logic [6:0] ADDR_SH_IDX  = 7'h10;
  localparam logic [6:0] ADDR_SH_DATA = 7'h11;
  localparam logic [6:0] ADDR_W_IDX   = 7'h20;
  localparam logic [6:0] ADDR_W_DATA  = 7'h21;

  localparam int SH_IDX_W = $clog2(TAP_LEN);
  localparam int W_IDX_W  = $clog2(TAP_LEN);

  // ---- internal state ----
  logic [SH_IDX_W-1:0] sh_idx;
  logic [W_IDX_W-1:0]  w_idx;

  logic [INPUT_WIDTH-1:0] adc_data_l;
  logic [INPUT_WIDTH-1:0] err_data_l;

  logic y_rdy_l, w_upd_l, xf_rdy_l;  // sticky done bits

  // ==========================================================================
  // Registered control / pulse generation
  // ==========================================================================
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      adc_valid  <= 1'b0;
      err_valid  <= 1'b0;
      adc_data_l <= '0;
      err_data_l <= '0;
      sh_idx     <= '0;
      w_idx      <= '0;
      y_rdy_l    <= 1'b0;
      w_upd_l    <= 1'b0;
      xf_rdy_l   <= 1'b0;
    end else begin
      // default: strobes are one-cycle
      adc_valid <= 1'b0;
      err_valid <= 1'b0;

      // X write -> latch sample + pulse adc_valid
      if (wr && addr == ADDR_X) begin
        adc_data_l <= wdata[INPUT_WIDTH-1:0];
        adc_valid  <= 1'b1;
      end

      // E write -> latch sample + pulse err_valid
      if (wr && addr == ADDR_E) begin
        err_data_l <= wdata[INPUT_WIDTH-1:0];
        err_valid  <= 1'b1;
      end

      // s_hat index write
      if (wr && addr == ADDR_SH_IDX) begin
        sh_idx <= wdata[SH_IDX_W-1:0];
      end

      // s_hat data write -> auto-increment (the actual regfile write strobe
      // sh_wr_en is generated combinationally below, so the coefficient lands
      // at the current sh_idx, and the index increments afterward).
      if (wr && addr == ADDR_SH_DATA) begin
        sh_idx <= sh_idx + 1'b1;
      end

      // weight index write
      if (wr && addr == ADDR_W_IDX) begin
        w_idx <= wdata[W_IDX_W-1:0];
      end
      // NOTE: no auto-increment on W_DATA *read* here. rdata is combinational
      // and the bridge latches it several cycles after the rd strobe, so an
      // increment keyed on rd would corrupt the in-flight read. W_IDX is set
      // explicitly by the MCU before each read (the testbench does exactly
      // that).

      // CLEAR_FLAGS (CMD bit 4): resets the sticky done bits. Applied as a
      // priority clear so it wins even if a done pulse coincides with the
      // clearing write in the same cycle.
      if (wr && addr == ADDR_CMD && wdata[4]) begin
        y_rdy_l  <= 1'b0;
        w_upd_l  <= 1'b0;
        xf_rdy_l <= 1'b0;
      end else begin
        y_rdy_l  <= y_rdy_l  | y_n_ready;
        w_upd_l  <= w_upd_l  | w_n_updated;
        xf_rdy_l <= xf_rdy_l | x_f_ready;
      end
    end
  end

  // ==========================================================================
  // Combinational read path (rdata must be a pure function of addr)
  // ==========================================================================
  always_comb begin
    case (addr)
      ADDR_STATUS: begin
        rdata = '0;
        rdata[0] = y_rdy_l;
        rdata[1] = w_upd_l;
        rdata[2] = x_f_valid;
        rdata[3] = xf_rdy_l;
        rdata[4] = mu_saturated;
        rdata[5] = 1'b0; // fault (not driven by anchor_top yet)
      end
      ADDR_Y_0: rdata = y_n[15:0];
      ADDR_Y_1: rdata = y_n[31:16];
      ADDR_Y_2: rdata = y_n[47:32];
      ADDR_Y_3: rdata = y_n[63:48];
      ADDR_SH_DATA: rdata = sample_t'(sh_rd_data);
      ADDR_W_DATA:  rdata = sample_t'(w_rd_data);
      default: rdata = '0;
    endcase
  end

  // ==========================================================================
  // Combinational datapath forwarding
  // ==========================================================================
  assign adc_data   = adc_data_l;
  assign err_data   = err_data_l;
  assign sh_wr_sel  = sh_idx;
  assign sh_wr_data = wdata;
  // write strobe: combinational from the SH_DATA write so the s_hat regfile
  // samples sh_wr_sel/sh_wr_data at the pre-increment sh_idx (one-cycle wr).
  assign sh_wr_en   = wr && (addr == ADDR_SH_DATA);
  assign sh_rd_sel  = sh_idx;
  assign w_rd_sel   = w_idx;
  assign irq        = y_rdy_l | w_upd_l | xf_rdy_l;

endmodule
