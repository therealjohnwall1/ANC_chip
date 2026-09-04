#!/usr/bin/env bash
# Top-level entry point for the noise_cancel simulation/golden/verification flow.
#
#   ./run.sh full            full-chip FxLMS integration: build + run + waveform
#   ./run.sh tb <name>       a single testbench (sample_in, anti_noise, regfile,
#                            error_block, s_hat_fir, anc)
#   ./run.sh golden          the golden python reference model (all layers)
#   ./run.sh gen [N]         regenerate full-system vectors (default 8192 samples)
#   ./run.sh all             unit TBs + golden + full, back to back

set -uo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DV="$REPO_ROOT/chip/dv"

PYTHON="$REPO_ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

cmd="${1:-help}"

case "$cmd" in
  full)
    "$DV/run_tb.sh" anc
    echo ""
    echo "flags right after a full run:"
    echo "  ./run.sh golden   # python reference (already bit-exact vs RTL; run to sanity-check)"
    ;;
  tb)
    shift
    "$DV/run_tb.sh" "$@"
    ;;
  golden)
    "$DV/run_golden.sh"
    ;;
  gen)
    shift
    PYTHONPATH="$REPO_ROOT:$DV" "$PYTHON" "$DV/gen_anc_vectors.py" "$@"
    ;;
  all)
    set -e
    for tb in sample_in anti_noise regfile error_block s_hat_fir; do
      "$DV/run_tb.sh" "$tb"
    done
    "$DV/run_golden.sh"
    "$DV/run_tb.sh" anc
    ;;
  help|*)
    echo "usage: $0 {full|tb <name>|golden|gen [N]|all}" >&2
    echo ""
    echo "  full      build+run the full-chip ANC integration, print gtkwave path"
    echo "  tb <name> one unit testbench: sample_in anti_noise regfile error_block s_hat_fir anc"
    echo "  golden    the golden fixed-point reference model"
    echo "  gen [N]   regenerate full-system vectors (ANC_N=N, default 8192)"
    echo "  all       every unit TB + golden + full integration"
    exit 1
    ;;
esac