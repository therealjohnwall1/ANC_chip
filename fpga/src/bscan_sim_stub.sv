// bscan_sim_stub.sv -- passive BSCANE2 stand-in for Verilator lint/sim.
//
// Vivado provides the real BSCANE2 primitive; Verilator does not. This empty
// model (compiled only under Verilator) lets the design elaborate. It never
// asserts CAPTURE/SHIFT, so the JTAG streamer stays idle in simulation.

`ifdef VERILATOR
module BSCANE2 #(
    parameter int JTAG_CHAIN = 1
) (
    output logic CAPTURE,
    output logic DRCK,
    output logic RESET,
    output logic RUNTEST,
    output logic SEL,
    output logic SHIFT,
    output logic TCK,
    output logic TDI,
    output logic TMS,
    output logic UPDATE,
    input  logic TDO
);
  assign CAPTURE = 1'b0;
  assign DRCK    = 1'b0;
  assign RESET   = 1'b1;
  assign RUNTEST = 1'b0;
  assign SEL     = 1'b0;
  assign SHIFT   = 1'b0;
  assign TCK     = 1'b0;
  assign TDI     = 1'b0;
  assign TMS     = 1'b0;
  assign UPDATE  = 1'b0;
  logic _u;
  assign _u = ^JTAG_CHAIN;
endmodule
`endif
