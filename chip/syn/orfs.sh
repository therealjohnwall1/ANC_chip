#!/usr/bin/env bash
# Run the OpenROAD-flow-scripts flow for anchor_top inside the ORFS container.
#
# Usage: ./orfs.sh [make-target ...]
#   ./orfs.sh synth        synthesis only
#   ./orfs.sh floorplan    floorplan
#   ./orfs.sh place        global + detailed placement
#   ./orfs.sh cts          clock tree synthesis
#   ./orfs.sh route        global + detailed routing
#   ./orfs.sh final        signoff: timing report + GDS
#   ./orfs.sh drc          klayout DRC
#   ./orfs.sh lvs          klayout LVS
#   ./orfs.sh gui_final    open the final GDS in the klayout GUI
#   ./orfs.sh clean_all    wipe results/logs/reports
#   ./orfs.sh bash         interactive shell in the container
#
# Env overrides:
#   ORFS_IMAGE  container image (default docker.io/openroad/orfs:latest)
#
# The whole repo is mounted at /work. ORFS results/logs/reports land in
# chip/syn/work (gitignored), keyed off WORK_HOME. Paths in config.mk and
# constraint.sdc are container paths (/work/...).

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

IMAGE="${ORFS_IMAGE:-docker.io/openroad/orfs:latest}"
DESIGN_CONFIG="/work/chip/syn/config.mk"
WORK_HOME="/work/chip/syn/work"

mkdir -p "$REPO_ROOT/chip/syn/work"

if test -t 0; then
  DOCKER_INTERACTIVE="-ti"
else
  DOCKER_INTERACTIVE="-i"
fi

# GUI support: forward X11 when a display is present.
XSOCK=/tmp/.X11-unix
XAUTH=/tmp/.docker.xauth
XARGS=()
if [[ -n "${DISPLAY:-}" ]]; then
  rm -f "$XAUTH" 2>/dev/null || true
  xauth nlist "${DISPLAY:-:0}" 2>/dev/null | sed -e 's/^..../ffff/' | xauth -f "$XAUTH" nmerge - 2>/dev/null || true
  XARGS=(-e "DISPLAY=${DISPLAY}" -e XAUTHORITY="$XAUTH" -v "$XSOCK:$XSOCK" -v "$XAUTH:$XAUTH")
fi

exec podman run --rm "$DOCKER_INTERACTIVE" \
  --privileged \
  --userns=keep-id \
  --user "$(id -u):$(id -g)" \
  --network host \
  -e LIBGL_ALWAYS_SOFTWARE=1 \
  -e QT_X11_NO_MITSHM=1 \
  -e QT_XKB_CONFIG_ROOT=/usr/share/X11/xkb \
  -e XDG_RUNTIME_DIR=/tmp/xdg-run \
  "${XARGS[@]}" \
  -e WORK_HOME="$WORK_HOME" \
  -e FLOW_HOME=/OpenROAD-flow-scripts/flow/ \
  -v "$REPO_ROOT:/work" \
  "$IMAGE" \
  bash -c '. /OpenROAD-flow-scripts/env.sh; cd /OpenROAD-flow-scripts/flow; make DESIGN_CONFIG='"$DESIGN_CONFIG"' "$@"' _ "$@"