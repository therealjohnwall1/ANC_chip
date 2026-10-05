// plant_fir.sv -- Q1.15 FIR, one MAC per cycle, for the simulated acoustic paths.
//
// Push a sample with `start`; `done` pulses NTAP cycles later and y_out holds
// the Q1.15 result. Products are Q2.30 accumulated in 64 bits, then shifted
// down 15 bits and saturated to 16-bit signed.

module plant_fir #(
    parameter int NTAP = 6,
    parameter logic signed [15:0] TAPS[0:NTAP-1] = '{default: 16'sd0}
) (
    input  logic clk,
    input  logic rst_n,
    input  logic start,
    input  logic signed [15:0] x_in,
    output logic signed [15:0] y_out,
    output logic done
);

  localparam int KW = $clog2(NTAP + 1);

  logic signed [15:0] line[0:NTAP-1];
  logic [KW-1:0] k;
  logic signed [63:0] acc;
  logic running;

  function automatic logic signed [15:0] sat16(input logic signed [63:0] v);
    if (v > 64'sd32767) sat16 = 16'sd32767;
    else if (v < -64'sd32768) sat16 = -16'sd32768;
    else sat16 = v[15:0];
  endfunction

  wire signed [15:0] tap = line[k];
  wire signed [63:0] acc_next = acc + (tap * TAPS[k]);
  wire signed [15:0] y_sat = sat16(acc_next >>> 15);

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int i = 0; i < NTAP; i++) line[i] <= '0;
      k <= '0;
      acc <= '0;
      running <= 1'b0;
      done <= 1'b0;
      y_out <= '0;
    end else begin
      done <= 1'b0;
      if (start && !running) begin
        for (int i = NTAP - 1; i > 0; i--) line[i] <= line[i-1];
        line[0] <= x_in;
        k <= '0;
        acc <= '0;
        running <= 1'b1;
      end else if (running) begin
        if (k == KW'(NTAP - 1)) begin
          y_out <= y_sat;
          running <= 1'b0;
          done <= 1'b1;
        end else begin
          acc <= acc_next;
          k <= k + 1'b1;
        end
      end
    end
  end

endmodule
