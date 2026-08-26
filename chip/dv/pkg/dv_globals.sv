package dv_globals;

  // Output directory for all DV artifacts (waveforms, logs, dumps).
  // Testbenches produce their files here via $dumpfile({DV_OUT_DIR, ...}).
  localparam string DV_OUT_DIR = "out/";

endpackage
