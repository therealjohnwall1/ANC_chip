// error + NLMS weight-update stage -- chip truth: e(n) arrives directly
//
//   E[n]  = sum_k x_f[n-1-k]^2              
//   mu[n] = lr / (E[n] + eps)              
//                                         
//                                        
//   w[k] += round_sat(w[k] + mu*e*x_f)      Q1.15, rounded at the write
module error_block
  import globals::*;
(
  input logic clk,
  input logic rst_n,
);


endmodule

