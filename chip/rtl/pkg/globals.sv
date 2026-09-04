package globals;

  parameter int WORD_LEN = 16;
  parameter int TAP_LEN = 32;
  // raw tap-line depth, rounded up to a power of two for free mod-2^k wrap.
  // y[n] reads x[n-1 .. n-TAP_LEN], which needs TAP_LEN+1 distinct samples
  // (golden/fir.py required_depth = max(N_W+1, N_S)).
  parameter int HIST_DEPTH = 1 << $clog2(TAP_LEN + 1);

  parameter int ACCUM_LEN = 64;
  parameter int CYCLES_PER_SAMP = 4125;
  parameter int INPUT_WIDTH = 12; // adc input width

  // fixed-point formats, golden/fmt.py DEFAULT
  parameter int XF_FRAC   = 14;   // x_f register Q2.14 (fmt.py:118)
  parameter int STEP_FRAC = 17;   // NLMS step Q15.17, OPEN (fmt.py:121)
  parameter int LR_FRAC   = 15;   // learning rate Q1.15
  parameter int EPS_FRAC  = 23;   // NLMS regularizer Q1.23 (fmt.py:123)
  parameter int LR_RAW    = 3277; // 0.1 in Q1.15
  parameter int EPS_RAW   = 84;   // 1e-5 in Q1.23


  typedef logic signed[WORD_LEN-1:0] sample_t;
  typedef logic signed[ACCUM_LEN-1:0] accum_t;
  typedef logic signed[15:0] xf_t;    // Q2.14 filtered reference
  typedef logic signed[31:0] step_t;  // Q15.17 NLMS step (mu)

endpackage
