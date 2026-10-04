#!/usr/bin/env bash
# Prepares an Unreal source tree (in /ue) to emit the compile database of a Linux target, with no
# Setup.sh and no Epic toolchain: only the few gitdeps files UBT itself needs, UBT built by the
# image's .NET SDK, and an "in-tree SDK" folder whose clang is the image's own.
# Usage: ue-setup.sh <clang major> <target>   (run inside ue-layout / ue-layout-13)
set -euo pipefail
CLANG=$1; TARGET=$2
HERE=$(cd "$(dirname "$0")" && pwd)
cd /ue
python3 "$HERE/gitdeps_fetch.py" /ue \
  "^Engine/Source/Programs/(Shared|UnrealBuildTool|UnrealHeaderTool)/" \
  "^Engine/Binaries/DotNET/Ionic" "^Engine/Source/ThirdParty/Intel/ISPC/bin/Linux/" > /tmp/gitdeps.log 2>&1 || echo "gitdeps: o CDN recusou parte (versao antiga); os arquivos ja presentes valem"; head -1 /tmp/gitdeps.log
chmod +x Engine/Source/ThirdParty/Intel/ISPC/bin/Linux/ispc 2>/dev/null || true
dotnet build Engine/Source/Programs/UnrealBuildTool/UnrealBuildTool.csproj -c Development -v q -nologo \
  -o Engine/Binaries/DotNET/UnrealBuildTool 2>&1 | grep -E "error|Error\(s\)" | sort -u | head -5 || true

# The SDK version UBT wants, from its own source (Linux_SDK.json since 5.2, a .cs before).
VERSION=$(python3 - <<'EOF'
import json, os, re
p = "Engine/Config/Linux/Linux_SDK.json"
if os.path.exists(p):
    print(json.load(open(p))["MainVersion"])
else:
    s = open("Engine/Source/Programs/UnrealBuildTool/Platform/Linux/LinuxPlatformSDK.Versions.cs").read()
    print(re.search(r'GetMainVersion\(\)\s*\{\s*return "([^"]+)"', s).group(1))
EOF
)
ROOT=/ue/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/$VERSION
SDK=$ROOT/x86_64-unknown-linux-gnu
mkdir -p "$SDK/bin" "$SDK/lib" "$SDK/include/c++"
echo "$VERSION" > "$ROOT/ToolchainVersion.txt"
for tool in clang clang++ ld.lld llvm-ar; do ln -sfn "/usr/bin/$tool-$CLANG" "$SDK/bin/$tool"; done
ln -sfn /usr "$SDK/usr"
ln -sfn /lib64 "$SDK/lib64"
ln -sfn "/usr/lib/llvm-$CLANG/lib/clang" "$SDK/lib/clang"
ln -sfn "/usr/lib/llvm-$CLANG/include/c++/v1" "$SDK/include/c++/v1"
echo "SDK $VERSION -> clang $("$SDK/bin/clang++" --version | head -1)"
bash "$HERE/ubt-db.sh" "$TARGET"
