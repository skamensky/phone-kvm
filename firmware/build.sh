#!/usr/bin/env bash
# Build the firmware: `build.sh sim` for the simulated board (and its flash image for QEMU),
# `build.sh board` for the real one. Run inside `nix develop .#firmware`.
set -euo pipefail
cd "$(dirname "$0")"
variant=${1:?sim or board}
idf.py -B "build-$variant" -D SDKCONFIG="build-$variant/sdkconfig" \
    -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.$variant" build
if [ "$variant" = sim ]; then
    (cd build-sim && esptool.py --chip esp32s3 merge_bin -o flash.bin --fill-flash-size 4MB @flash_args)
fi
