// pwm_audio.sv -- 8-bit PWM DAC for the on-board headphone jack (pin G18).
//
// The Blackboard brings PLAY_L(AUDIO)=G18 directly to the headphone jack through
// an RC/PWM filter. Drive it with an 8-bit PWM at clk/256 (25 MHz / 256 =
// 97.7 kHz carrier) holding a 16 kHz audio sample. The sample is gained x16
// (the mic/loop levels are small) and saturated before mapping to duty.

module pwm_audio (
    input  logic clk,
    input  logic rst_n,
    input  logic signed [15:0] sample,
    output logic pwm
);

  logic [7:0] cnt;
  always_ff @(posedge clk)
    if (!rst_n) cnt <= '0;
    else cnt <= cnt + 1'b1;

  logic signed [31:0] gained;
  logic signed [15:0] s;
  logic signed [15:0] dcenter;

  always_comb begin
    gained = {{16{sample[15]}}, sample} <<< 4;  // x16
    if (gained > 32'sd32767) s = 16'sd32767;
    else if (gained < -32'sd32768) s = -16'sd32768;
    else s = gained[15:0];
    dcenter = (s >>> 8) + 16'sd128;  // signed high byte -> duty 0..255 centered at 128
  end

  logic [7:0] duty;
  always_comb
    if (dcenter < 16'sd0) duty = 8'd0;
    else if (dcenter > 16'sd255) duty = 8'd255;
    else duty = dcenter[7:0];

  assign pwm = (cnt < duty);

endmodule
