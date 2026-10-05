#!/usr/bin/env bash
# check.sh <libUE4SS.so> <libLinkProbe.so>: fails when the probe mod, linked the way a C++ mod is, would
# use a copy of its own of something it must share with libUE4SS.so. UE4SS never sets up such a copy:
# a mod's get_program() returned a null reference that way.
set -uo pipefail
lib=$1 mod=$2 fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# "<type letter> <demangled name>" per symbol
exports=$(nm -D -C --defined-only "$lib" | cut -c18-)
mod_syms=$(nm -C "$mod" | cut -c18-)

# Out-of-line API and data: the mod must import them from libUE4SS.so.
for name in 'RC::Unreal::Container::UnrealObjectVC' 'RC::Unreal::UObjectGlobals::FindObject(' \
            'SharedObjectManager::GetSharedObjectInternal(' 'SharedObjectManager::SetSharedObjectInternal(' \
            'lua_settop'; do
    if ! grep -qF -- " $name" <<<"$exports"; then bad "libUE4SS.so does not export $name"
    elif ! grep -F -- " $name" <<<"$mod_syms" | grep -q '^U '; then bad "the mod does not import $name"
    else ok "the mod imports $name"; fi
done

# An inline variable: the mod defines it too, and its references reach libUE4SS.so's copy only when
# libUE4SS.so exports it and the mod's copy is not local.
name='RC::UE4SSProgram::s_program'
if ! grep -qF -- " $name" <<<"$exports"; then bad "libUE4SS.so does not export $name"
elif ! grep -F -- " $name" <<<"$mod_syms" | grep -q '^[UVuW] '; then bad "the mod's $name is local or missing"
else ok "the mod shares $name"; fi

none() { # <found> <ok message> <fail message>
    if [ -z "$1" ]; then ok "$2"; else bad "$3 ($(wc -l <<<"$1"), the first 20):"$'\n'"$(head -20 <<<"$1")"; fi
}
none "$(grep -E '^[bBdDgGsS] .*RC::' <<<"$mod_syms")" \
    "the mod has no RC:: data of its own" "the mod has RC:: data of its own"
none "$(grep -E '^[^U] (lua_|luaL_|luaopen_|LuaLock)' <<<"$mod_syms")" \
    "the mod has no Lua of its own" "the mod has Lua of its own, and so a second LuaLock"
# A preloaded library comes first in every lookup of the game's process.
none "$(grep 'std::' <<<"$exports" | grep -v 'RC::')" \
    "libUE4SS.so exports no std::-only names" "libUE4SS.so exports std::-only names"

exit $fail
