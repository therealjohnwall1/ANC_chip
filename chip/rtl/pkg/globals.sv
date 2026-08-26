package globals;

  parameter int WORD_LEN = 16;
  parameter int TAP_LEN = 32;
  parameter int TAP_LEN_IDX = 5;

  parameter int ACCUM_LEN = 64;
  parameter int CYCLES_PER_SAMP = 4125;
  parameter int INPUT_WIDTH = 12; // adc input width


  typedef logic signed[WORD_LEN-1:0] sample_t;
  typedef logic signed[ACCUM_LEN-1:0] accum_t;


endpackage
