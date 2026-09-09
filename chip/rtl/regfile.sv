// combinational-read FF register file (no SRAM macro)

module regfile #(
    parameter int DEPTH = 32,
    parameter int WIDTH = 16
) (
    input logic clk,
    input logic rst_n,

    input logic [$clog2(DEPTH)-1:0] rd_sel_a,
    output logic [WIDTH-1:0] rd_data_a,

    input logic [$clog2(DEPTH)-1:0] rd_sel_b,
    output logic [WIDTH-1:0] rd_data_b,

    input logic [$clog2(DEPTH)-1:0] rd_sel_c,
    output logic [WIDTH-1:0] rd_data_c,

    input logic [$clog2(DEPTH)-1:0] wr_sel,
    input logic [WIDTH-1:0] wr_data,
    input logic wr_en
);

  logic [WIDTH-1:0] mem[DEPTH];

  assign rd_data_a = mem[rd_sel_a];
  assign rd_data_b = mem[rd_sel_b];
  assign rd_data_c = mem[rd_sel_c];

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int k = 0; k < DEPTH; k++) mem[k] <= '0;
    end else if (wr_en) begin
      mem[wr_sel] <= wr_data;
    end
  end

endmodule
