// serial_bridge.sv -- SPI slave -> MMIO transaction bridge
//
// Converts serial SPI traffic into the 4-wire transaction interface that
// mmio_regs.sv consumes:
//
//     wr    : 1-cycle write strobe
//     rd    : 1-cycle read strobe (informational; rdata is combinational)
//     addr  : 7-bit register address (held after the header is decoded)
//     wdata : 16-bit write data (held at commit)
//     rdata : 16-bit read data (combinational from addr)
//
// SPI protocol (mode 0, CPOL=0/CPHA=0, MSB first, 24-bit frame):
//     bit[23]    : rw   (1 = write, 0 = read)
//     bit[22:16] : addr[6:0]
//     bit[15:0]  : data (write data on writes; ignored/dummy on reads)
// On a read, sdo drives the 16-bit result MSB-first during the data phase.
// sdi is sampled on the rising edge of sclk; sdo is updated on the falling
// edge of sclk (mode 0: master samples sdo on the following rising edge).
//
// sclk, cs_n and sdi are asynchronous inputs synchronized into the clk domain
// with 2-FF synchronizers and edge detection. Constraint: sclk <= clk/4 so
// edges are separated by >= 2 clk cycles and are detected reliably.
//
// Transaction timing (all in the clk domain):
//   * cs_n falls  -> begin frame, clear counters/shift regs
//   * 8th sclk rise  -> header done: latch rw_bit and addr; pulse rd on reads
//   * 8th sclk fall  -> (read only) load sh_out <= rdata
//   * 9th..23rd sclk fall -> (read only) shift sh_out, MSB first on sdo
//   * cs_n rises  -> commit: write frames pulse wr with held addr/wdata

module serial_bridge (
  input  logic clk,
  input  logic rst_n,

  input  logic sclk,   // SPI clock (async; must be <= clk/4)
  input  logic cs_n,   // active-low chip select
  input  logic sdi,    // master-out / slave-in  (MOSI)
  output logic sdo,    // master-in  / slave-out (MISO)

  // transaction interface -> mmio_regs
  output logic        wr,
  output logic        rd,
  output logic [6:0]  addr,
  output logic [15:0] wdata,
  input  logic [15:0] rdata
);

  // ---- input synchronization + edge detection ----
  logic sclk_meta, sclk_sync, sclk_prev;
  logic csn_meta,  csn_sync,  csn_prev;
  logic sdi_meta,  sdi_sync;

  always_ff @(posedge clk) begin
    sclk_meta <= sclk;  sclk_sync <= sclk_meta;  sclk_prev <= sclk_sync;
    csn_meta  <= cs_n;  csn_sync  <= csn_meta;   csn_prev  <= csn_sync;
    sdi_meta  <= sdi;   sdi_sync  <= sdi_meta;
  end

  wire sclk_rise =  sclk_sync & ~sclk_prev;
  wire sclk_fall = ~sclk_sync &  sclk_prev;
  wire csn_rise  =  csn_sync  & ~csn_prev;
  wire csn_fall  = ~csn_sync  &  csn_prev;

  // incoming shift register; next value = shift in the current sdi bit
  wire [23:0] sh_in_next = {sh_in[22:0], sdi_sync};

  // ---- frame state ----
  logic [4:0]  bit_cnt;    // bits received this frame (0..24)
  logic [23:0] sh_in;      // incoming bits (bit 0 = oldest = LSB position)
  logic [15:0] sh_out;     // outgoing read data (shifted MSB first)
  logic        rw_bit;     // decoded rw from header (1=write, 0=read)
  logic        in_frame;   // cs_n low => inside a frame

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      bit_cnt  <= '0;
      sh_in    <= '0;
      sh_out   <= '0;
      rw_bit   <= 1'b0;
      in_frame <= 1'b0;
      wr       <= 1'b0;
      rd       <= 1'b0;
      addr     <= '0;
      wdata    <= '0;
    end else begin
      // strobes are one-cycle by default
      wr <= 1'b0;
      rd <= 1'b0;

      // begin a new frame
      if (csn_fall) begin
        in_frame <= 1'b1;
        bit_cnt  <= '0;
        sh_in    <= '0;
        sh_out   <= '0;
      end

      // shift in on rising edge while the frame is open
      if (in_frame && sclk_rise && bit_cnt < 24) begin
        sh_in   <= sh_in_next;
        bit_cnt <= bit_cnt + 1'b1;

        // the 8th bit just arrived: header = {rw, addr[6:0]} is complete
        if (bit_cnt == 7) begin
          rw_bit <= sh_in_next[7];
          addr   <= sh_in_next[6:0];
          if (!sh_in_next[7])
            rd <= 1'b1;   // read frame: pulse rd (clear-on-read etc.)
        end
      end

      // read response: load rdata after the header, then shift it out
      if (in_frame && sclk_fall && !rw_bit) begin
        if (bit_cnt == 8)
          sh_out <= rdata;
        else if (bit_cnt > 8 && bit_cnt < 24)
          sh_out <= {sh_out[14:0], 1'b0};
      end

      // end of frame: commit writes
      if (csn_rise && in_frame) begin
        in_frame <= 1'b0;
        if (rw_bit) begin
          wr    <= 1'b1;
          wdata <= sh_in[15:0];
        end
      end
    end
  end

  // sdo: read data MSB-first during the read data phase, 0 otherwise
  assign sdo = (in_frame && !rw_bit && bit_cnt >= 8 && bit_cnt < 24) ? sh_out[15] : 1'b0;

endmodule
