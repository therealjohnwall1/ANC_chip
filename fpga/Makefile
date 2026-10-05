include design.mk

VIVADO ?= vivado
BUILD  := build
SRCS   := $(wildcard src/*.v src/*.sv src/pkg/*.sv src/*.vh src/*.svh src/*.vhd src/*.vhdl src/*.mem) \
          $(wildcard constr/*.xdc) $(wildcard ip/*.xci ip/*/*.xci)

all: $(BUILD)/$(TOP).bit

# Vivado runs inside build/ so .Xil, logs and checkpoints stay there.
$(BUILD)/$(TOP).bit: $(SRCS) build.tcl design.mk
	mkdir -p $(BUILD)
	cd $(BUILD) && $(VIVADO) -mode batch -nojournal -log vivado.log \
		-source ../build.tcl -tclargs $(TOP) $(PART)

clean:
	rm -rf $(BUILD) .Xil

.PHONY: all clean
