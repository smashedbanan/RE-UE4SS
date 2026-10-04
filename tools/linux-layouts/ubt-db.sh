#!/usr/bin/env bash
# Runs UBT's GenerateClangDatabase for a target, creating each missing third-party folder the
# rules enumerate (we never link, so an empty folder is as good as the real libraries).
# Usage: ubt-db.sh <target> [tries]
cd /ue
for i in $(seq 1 ${2:-60}); do
  out=$(timeout 1500 dotnet Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll "$1" Linux Shipping -Mode=GenerateClangDatabase 2>&1)
  missing=$(printf '%s\n' "$out" | grep -oE "Could not find a part of the path '[^']+'" | head -1 | sed -E "s/.*'([^']+)'/\1/")
  if [ -n "$missing" ]; then mkdir -p "$missing"; echo "criada: $missing"; continue; fi
  missing=$(printf '%s\n' "$out" | grep -oE "Could not find file '[^']+'" | head -1 | sed -E "s/.*'([^']+)'/\1/")
  if [ -n "$missing" ]; then mkdir -p "$(dirname "$missing")"; : > "$missing"; echo "arquivo vazio: $missing"; continue; fi
  printf '%s\n' "$out" | grep -v "was not resolvable\|does not exist" | tail -15
  break
done
ls -la /ue/compile_commands.json 2>&1
