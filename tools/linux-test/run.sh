#!/usr/bin/env bash
# Runs the RuneScape: Dragonwilds server image, unchanged, with UE4SS preloaded and a fresh copy of
# Saved. Foreground; stop with Ctrl-C or `podman stop dw-ue4ss-test`. Build first: build.sh.
# Results: $dw/ue4ss/UE4SS.log, $dw/Saved/Logs/RSDragonwilds.log, PROBE lines on stdout.
# Env: UE4SS_TEST_DIR, DW_IMAGE, DW_SAVED, RUNESCAPE_UNIT, PARTYHATS_ZIP.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
base=${UE4SS_TEST_DIR:-/tmp/claude-scratch}
build=$base/ue4ss-build/build
dw=$base/dw-test
image=${DW_IMAGE:-localhost/dragonwilds:latest}
saved=${DW_SAVED:-$HOME/Games/Dragonwilds/Saved}
unit=${RUNESCAPE_UNIT:-$HOME/PROJECTS/repos/jj/POOP/stacks/runescape/runescape.container}
partyhats=${PARTYHATS_ZIP:-$HOME/Downloads/extract/runescape_mods_10-03/PartyHats.zip}
bin=/srv/rs_server/RSDragonwilds/Binaries/Linux
game=RSDragonwildsServer-Linux-Shipping
# Saved holds the server's WorldPassword, and cp -a keeps its modes: only the owner may enter $dw.
mkdir -p "$dw" && chmod 700 "$dw"

# 1. Signatures and vtable layout of this image's game build. They are absolute addresses of that
#    build (non-PIE), so they are keyed by image id and generator version (a hash of the two scripts
#    and the template), never reused across builds and regenerated when a generator changes.
id=$(podman image inspect --format '{{.Id}}' "$image")
gen=$(cat "$repo"/tools/linux-layouts/ue_{signatures,vtable_layout}.py \
  "$repo/assets/VTableLayoutTemplates/VTableLayout_5_06_Template.ini" | sha256sum | cut -c1-12)
layouts=$dw/layouts/$id-$gen
if [ ! -f "$layouts/VTableLayout.ini" ]; then
  tmp=$dw/layouts/tmp
  rm -rf "$tmp" && mkdir -p "$tmp/out"
  c=$(podman create "$image")
  podman cp "$c:$bin/$game" "$tmp/"
  podman cp "$c:$bin/$game.sym" "$tmp/"
  podman rm "$c" >/dev/null
  python3 -B "$repo/tools/linux-layouts/ue_signatures.py" "$tmp/$game" "$tmp/out/UE4SS_Signatures"
  python3 -B "$repo/tools/linux-layouts/ue_vtable_layout.py" "$tmp/$game" \
    "$repo/assets/VTableLayoutTemplates/VTableLayout_5_06_Template.ini" > "$tmp/out/VTableLayout.ini"
  rm -rf "$layouts" && mv "$tmp/out" "$layouts" && rm -rf "$tmp"
fi

# 2. The ue4ss directory: UE4SS's working directory (log, settings, layouts, Mods). An earlier run
#    may still have it and Saved mounted: remove that container first.
podman rm -f --ignore dw-ue4ss-test >/dev/null
u=$dw/ue4ss
rm -rf "$u" && mkdir -p "$u/Mods/ProbeCpp/dlls"
cp "$build/Game__Shipping__Linux/lib/libUE4SS.so" "$repo/assets/UE4SS-settings.ini" "$u/"
cp -r "$layouts/UE4SS_Signatures" "$layouts/VTableLayout.ini" "$u/"
cp -r "$repo/assets/Mods/shared" "$here/ProbeLua" "$u/Mods/"
cp "$build/probe/main.so" "$u/Mods/ProbeCpp/dlls/"
rm -rf "$dw/partyhats"
unzip -q "$partyhats" 'RSDragonwilds/Binaries/Win64/ue4ss/Mods/PartyHats/*' -d "$dw/partyhats"
mv "$dw/partyhats/RSDragonwilds/Binaries/Win64/ue4ss/Mods/PartyHats" "$u/Mods/"
rm -rf "$dw/partyhats"
printf '%s\n' 'ProbeLua : 1' 'ProbeCpp : 1' 'PartyHats : 1' > "$u/Mods/mods.txt"

# 3. A fresh copy of the save every run: the server autosaves into it.
rm -rf "$dw/Saved" && cp -a "$saved" "$dw/Saved"

# 4. The live unit's own arguments (t.MaxFPS, log filters), split without globbing: [Core.Log] is a
#    glob pattern. No -p: nothing can reach this instance. --init stands in for RunInit=true, and
#    keep-id maps the image's uid 65532 to the host user, who owns the bind mounts.
read -r -a args <<< "$(sed -n 's/^Exec=//p' "$unit")"
exec podman run --rm --init --name dw-ue4ss-test \
  --userns keep-id:uid=65532,gid=65532 --security-opt no-new-privileges \
  -v "$u:$bin/ue4ss" -v "$dw/Saved:/srv/rs_server/RSDragonwilds/Saved" \
  -e LD_PRELOAD="$bin/ue4ss/libUE4SS.so" \
  "$image" "${args[@]}"
