#!/usr/bin/env bash
# Run the golden reference model's layer demos.
#
#   ./chip/dv/run_golden.sh            # all six, in layer order
#   ./chip/dv/run_golden.sh core demo  # just these
#
# Only golden.demo asserts anything (it exits 1 on a failed check); the other
# five print explanatory output and always succeed. Runnable from any cwd.

set -uo pipefail

DV_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$DV_DIR/../.." && pwd)"

# repo root for `scripts.ans`, chip/dv for the `golden` package itself
export PYTHONPATH="$REPO_ROOT:$DV_DIR${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="$REPO_ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
[ -n "$PYTHON" ] || { echo "no python found (looked for $REPO_ROOT/.venv/bin/python)" >&2; exit 127; }

ALL=(fmt mac fir update core demo)
MODULES=("${@:-}")
[ -z "${MODULES[0]}" ] && MODULES=("${ALL[@]}")

failed=()
for m in "${MODULES[@]}"; do
    printf '\n%s\n== golden.%s\n%s\n' "$(printf '=%.0s' {1..96})" "$m" "$(printf '=%.0s' {1..96})"
    "$PYTHON" -m "golden.$m" || failed+=("$m")
done

printf '\n%s\n' "$(printf '=%.0s' {1..96})"
if [ ${#failed[@]} -eq 0 ]; then
    echo "== ${#MODULES[@]}/${#MODULES[@]} ok"
else
    echo "== FAILED: ${failed[*]}"
    exit 1
fi
