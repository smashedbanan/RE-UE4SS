"""Writes the UE4SS_Signatures/*.lua of a LINUX Unreal server from its executable and its .sym.

patternsleuth's AOB scans miss some engine functions in a game's own build (Dragonwilds:
FName::ToString, FName::FName and StaticConstructObject_Internal), and UE4SS then rescans until
SecondsToScanBeforeGivingUp and never starts. The .sym names every function and the executable is
non-PIE, so each file simply returns the function's address.

The .sym is a line table whose records carry the name of the function each line belongs to, so a
function's entry is its lowest address. Clang aligns function entries to 16 bytes; an address that is
not aligned, or not in code, is refused rather than written.

The addresses hold for one build only: run this again after every game update, like ue_vtable_layout.py.

Usage: ue_signatures.py <executable> <UE4SS_Signatures directory>
Only stdlib.
"""
from __future__ import annotations

import os
import struct
import sys

from ue_vtable_layout import Image, Symbols

ET_EXEC = 2

# Signature file -> the function UE4SS expects it to return (docs/guides/fixing-compatibility-problems.md).
FUNCTIONS = {
    "FName_ToString": "FName::ToString(FString&) const",
    "FName_Constructor": "FName::FName(char16_t const*, EFindName)",
    "StaticConstructObject": "StaticConstructObject_Internal(FStaticConstructObjectParameters const&)",
}


def main(argv: list[str]) -> int:
    exe, out_dir = argv[0], argv[1]
    image = Image(exe)
    (e_type,) = struct.unpack_from("<H", image.data, 16)
    if e_type != ET_EXEC:
        sys.exit(f"{exe}: not a non-PIE executable (e_type {e_type}), so .sym addresses are not runtime addresses")
    found = Symbols(exe + ".sym", image.base).addresses_of(set(FUNCTIONS.values()))
    entries = {}
    for file, name in FUNCTIONS.items():
        if not found[name]:
            sys.exit(f"{name}: not in the .sym")
        entry = min(found[name])
        if entry % 16 or not image.is_code(entry):
            sys.exit(f"{name}: lowest address {entry:#x} is not a function entry")
        entries[file] = entry
    os.makedirs(out_dir, exist_ok=True)
    for file, entry in entries.items():
        with open(os.path.join(out_dir, file + ".lua"), "w") as f:
            f.write(f"-- {FUNCTIONS[file]}, from {os.path.basename(exe)}.sym by ue_signatures.py\nreturn {entry:#x}\n")
        print(f"{file}.lua: {FUNCTIONS[file]} at {entry:#x}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
