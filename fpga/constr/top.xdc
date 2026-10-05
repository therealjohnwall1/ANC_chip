## Real Digital Blackboard (xc7z007sclg400-1)
## Pins taken from Real Digital's official blackboard.xdc.

## 100 MHz PL clock
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports clk]
create_clock -period 10.000 -name sys_clk [get_ports clk]

## Datapath clock: clk / 4 = 25 MHz (BUFG output in top.sv)
create_generated_clock -name clk_anc -source [get_ports clk] -divide_by 4 \
    [get_pins u_bufg_anc/O]

## LEDs
set_property -dict {PACKAGE_PIN N20 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN P20 IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN R19 IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN T20 IOSTANDARD LVCMOS33} [get_ports {led[3]}]
set_property -dict {PACKAGE_PIN T19 IOSTANDARD LVCMOS33} [get_ports {led[4]}]
set_property -dict {PACKAGE_PIN U13 IOSTANDARD LVCMOS33} [get_ports {led[5]}]
set_property -dict {PACKAGE_PIN V20 IOSTANDARD LVCMOS33} [get_ports {led[6]}]
set_property -dict {PACKAGE_PIN W20 IOSTANDARD LVCMOS33} [get_ports {led[7]}]
set_property -dict {PACKAGE_PIN W19 IOSTANDARD LVCMOS33} [get_ports {led[8]}]
set_property -dict {PACKAGE_PIN Y19 IOSTANDARD LVCMOS33} [get_ports {led[9]}]

## RGB LEDs {R G B}
set_property -dict {PACKAGE_PIN W18 IOSTANDARD LVCMOS33} [get_ports {rgb_a[0]}]
set_property -dict {PACKAGE_PIN W16 IOSTANDARD LVCMOS33} [get_ports {rgb_a[1]}]
set_property -dict {PACKAGE_PIN Y18 IOSTANDARD LVCMOS33} [get_ports {rgb_a[2]}]
set_property -dict {PACKAGE_PIN Y14 IOSTANDARD LVCMOS33} [get_ports {rgb_b[0]}]
set_property -dict {PACKAGE_PIN Y16 IOSTANDARD LVCMOS33} [get_ports {rgb_b[1]}]
set_property -dict {PACKAGE_PIN Y17 IOSTANDARD LVCMOS33} [get_ports {rgb_b[2]}]

## Slide switches
set_property -dict {PACKAGE_PIN R17 IOSTANDARD LVCMOS33} [get_ports {sw[0]}]
set_property -dict {PACKAGE_PIN U20 IOSTANDARD LVCMOS33} [get_ports {sw[1]}]
set_property -dict {PACKAGE_PIN R16 IOSTANDARD LVCMOS33} [get_ports {sw[2]}]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports {sw[3]}]
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33} [get_ports {sw[4]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33} [get_ports {sw[5]}]
set_property -dict {PACKAGE_PIN L15 IOSTANDARD LVCMOS33} [get_ports {sw[6]}]
set_property -dict {PACKAGE_PIN M15 IOSTANDARD LVCMOS33} [get_ports {sw[7]}]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports {sw[8]}]
set_property -dict {PACKAGE_PIN T12 IOSTANDARD LVCMOS33} [get_ports {sw[9]}]
set_property -dict {PACKAGE_PIN T11 IOSTANDARD LVCMOS33} [get_ports {sw[10]}]
set_property -dict {PACKAGE_PIN T14 IOSTANDARD LVCMOS33} [get_ports {sw[11]}]

## Push buttons
set_property -dict {PACKAGE_PIN W14 IOSTANDARD LVCMOS33} [get_ports {btn[0]}]
set_property -dict {PACKAGE_PIN W13 IOSTANDARD LVCMOS33} [get_ports {btn[1]}]
set_property -dict {PACKAGE_PIN P15 IOSTANDARD LVCMOS33} [get_ports {btn[2]}]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33} [get_ports {btn[3]}]

## PmodA -- SPI/MMIO host interface to tt_um_anchor
set_property -dict {PACKAGE_PIN F16 IOSTANDARD LVCMOS33} [get_ports pmod_sclk] ;# JA1_P
set_property -dict {PACKAGE_PIN F17 IOSTANDARD LVCMOS33} [get_ports pmod_cs_n] ;# JA1_N
set_property -dict {PACKAGE_PIN G19 IOSTANDARD LVCMOS33} [get_ports pmod_sdi]  ;# JA2_P
set_property -dict {PACKAGE_PIN G20 IOSTANDARD LVCMOS33} [get_ports pmod_sdo]  ;# JA2_N
set_property -dict {PACKAGE_PIN E18 IOSTANDARD LVCMOS33} [get_ports pmod_irq]  ;# JA3_P

## On-board MEMS PDM microphone (SPK0833LM4H, wired to the PL)
set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33} [get_ports m_clk]  ;# M_CLK
set_property -dict {PACKAGE_PIN L14 IOSTANDARD LVCMOS33} [get_ports m_data] ;# M_DATA
