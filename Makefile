# Verible SystemVerilog format + lint.
# The tools come from the 'verible' package installed under nvim/mason.
# Override VERIBLE_HOME if your verible lives elsewhere.

VERIBLE_HOME ?= $(HOME)/.local/share/nvim/mason/bin
FORMAT := $(VERIBLE_HOME)/verible-verilog-format
LINT   := $(VERIBLE_HOME)/verible-verilog-lint
RULES  := .rules.verible_lint

# Hand-written RTL + testbenches. Excludes generated *.svh vector files and
# synthesis netlists under chip/syn.
SV_SRCS := $(shell find chip/rtl chip/dv -name '*.sv' -not -path '*/obj_dir/*' | sort)

.PHONY: fmt fmt-check lint

fmt: ## Format all RTL in place
	$(FORMAT) --inplace $(SV_SRCS)

fmt-check: ## Fail if any RTL is not formatted
	@for f in $(SV_SRCS); do $(FORMAT) --verify $$f; done

lint: ## Lint all RTL
	$(LINT) --rules_config=$(RULES) $(SV_SRCS)