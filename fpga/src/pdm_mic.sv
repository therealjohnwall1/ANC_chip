// pdm_mic.sv -- on-board MEMS PDM microphone capture.
//
// Real Digital Blackboard mic: M_CLK = N15 (FPGA out), M_DATA = L14 (FPGA in).
// The mic outputs a 1-bit PDM stream clocked by M_CLK; summing DEC consecutive
// bits yields one PCM sample. 100 MHz / 50 = 2.0 MHz M_CLK, / 125 = 16 kHz PCM.
//
// This is a 1st-order (boxcar) decimator: adequate for a demo. A CIC would give
// better stop-band rejection later.

module pdm_mic #(
    parameter int MCLK_DIV = 50,   // 100 MHz / MCLK_DIV = 2.0 MHz
    parameter int DEC      = 125   // MCLK / DEC = 16 kHz
) (
    input  logic clk,
    input  logic rst_n,
    output logic m_clk,
    input  logic m_data,
    output logic signed [15:0] pcm,
    output logic pcm_valid,
    output logic [31:0] sample_idx
);

  // ---- M_CLK: 50% duty divide-by-MCLK_DIV (2 MHz) ----
  logic [$clog2(MCLK_DIV)-1:0] div;
  always_ff @(posedge clk) begin
    if (!rst_n) div <= '0;
    else div <= (div == MCLK_DIV - 1) ? '0 : div + 1'b1;
  end
  assign m_clk = (div < MCLK_DIV / 2);

  // ---- accumulate PDM bits on each M_CLK rising edge ----
  logic m_clk_r;
  wire  rise = m_clk & ~m_clk_r;

  logic [7:0] acc;  // 0..DEC
  logic [7:0] cnt;  // 0..DEC-1
  logic signed [15:0] pcm_r;
  logic valid_r;
  logic [31:0] idx_r, idx_out;
  wire [7:0] sum = acc + {7'b0, m_data};

  logic signed [15:0] centered;
  assign centered = 16'($signed({1'b0, sum}) - 9'sd62);  // ~0.5 * (DEC+1)

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      m_clk_r <= 1'b0;
      acc <= '0;
      cnt <= '0;
      pcm_r <= '0;
      valid_r <= 1'b0;
      idx_r <= '0;
      idx_out <= '0;
    end else begin
      m_clk_r <= m_clk;
      valid_r <= 1'b0;
      if (rise) begin
        if (cnt == DEC - 1) begin
          pcm_r   <= centered <<< 8;  // scale the narrow boxcar sum to 16-bit
          valid_r <= 1'b1;
          idx_out <= idx_r;           // index of the sample being emitted
          idx_r   <= idx_r + 1'b1;
          acc     <= '0;
          cnt     <= '0;
        end else begin
          acc <= sum;
          cnt <= cnt + 1'b1;
        end
      end
    end
  end

  assign pcm        = pcm_r;
  assign pcm_valid  = valid_r;
  assign sample_idx = idx_out;

endmodule
