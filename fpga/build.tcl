# Non-project Vivado flow. Run from build/: vivado -mode batch -source ../build.tcl -tclargs TOP PART
set top  [lindex $argv 0]
set part [lindex $argv 1]
set root [file normalize [file join [file dirname [info script]]]]

create_project -in_memory -part $part

# Packages first: modules import globals::* in their port lists, so the
# package must be elaborated before the modules are read.
foreach f [glob -nocomplain $root/src/pkg/*.sv]            { read_verilog -sv $f }
foreach f [glob -nocomplain $root/src/*.v]                 { read_verilog $f }
foreach f [glob -nocomplain $root/src/*.sv]                { read_verilog -sv $f }
foreach f [glob -nocomplain $root/src/*.vhd $root/src/*.vhdl] { read_vhdl $f }
foreach f [glob -nocomplain $root/src/*.mem]               { add_files $f }
# On-chip self-test vector ROMs: copy next to the run dir so $readmemh
# (relative paths) resolves during synthesis.
foreach f [glob -nocomplain $root/src/mem/*.mem]           { file copy -force $f [file tail $f] }
foreach f [glob -nocomplain $root/constr/*.xdc]            { read_xdc $f }

# IP: copy each .xci into build/ so generated output never lands in the
# synced source tree (the next push would delete it).
foreach f [glob -nocomplain $root/ip/*.xci $root/ip/*/*.xci] {
    set d ip/[file rootname [file tail $f]]
    file mkdir $d
    file copy -force $f $d/
    read_ip $d/[file tail $f]
}
if {[llength [get_ips -quiet]]} {
    upgrade_ip [get_ips]
    generate_target all [get_ips]
    synth_ip [get_ips]
}

synth_design -top $top -part $part
report_utilization -file utilization_synth.rpt

opt_design
place_design
phys_opt_design
route_design
write_checkpoint -force post_route.dcp

report_timing_summary -file timing.rpt
report_utilization    -file utilization.rpt
report_drc            -file drc.rpt

set paths [get_timing_paths -quiet -max_paths 1 -nworst 1 -setup]
set wns [expr {[llength $paths] ? [get_property SLACK $paths] : ""}]
if {$wns ne "" && $wns < 0} {
    puts "WARNING: timing not met (WNS = $wns ns), see build/timing.rpt"
}

write_bitstream -force $top.bit
