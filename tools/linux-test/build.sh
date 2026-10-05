#!/usr/bin/env bash
# Builds libUE4SS.so (and the probe mods of CMakeLists.txt) for a Linux game server inside the
# Fedora 44 builder (Containerfile.builder). Output: $UE4SS_TEST_DIR/ue4ss-build/build.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
out=${UE4SS_TEST_DIR:-/tmp/claude-scratch}/ue4ss-build
mkdir -p "$out/cargo"

podman build -t localhost/ue4ss-builder:f44 -f "$here/Containerfile.builder" "$here"
podman run --rm -v "$repo:/src" -v "$out:/out" -e CARGO_HOME=/out/cargo \
  localhost/ue4ss-builder:f44 bash -c '
    set -e
    cmake -S /src/tools/linux-test -B /out/build -G Ninja \
      -DCMAKE_BUILD_TYPE=Game__Shipping__Linux \
      -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_LINKER_TYPE=LLD \
      -DCMAKE_INSTALL_LIBDIR=lib
    cmake --build /out/build --target UE4SS ProbeCpp'
