"""Writes the UE4SS_Signatures/*.lua of a LINUX Unreal server from its executable and its .sym.

patternsleuth's AOB scans miss some engine functions in a game's own build (Dragonwilds:
FName::ToString, FName::FName and StaticConstructObject_Internal), and UE4SS then rescans until
SecondsToScanBeforeGivingUp and never starts. They also miss the optional FUObjectHashTables::Get,
GNatives and ConsoleManagerSingleton. The executable is non-PIE, so each file returns an address:
  - a function's entry from the .sym: FName_ToString, FName_Constructor, StaticConstructObject, and
    ConsoleManager (IConsoleManager::SetupSingleton, what patternsleuth's resolver returns too);
  - a global decoded from instructions of .sym-named functions: GNatives from FFrame::Step, and
    GUObjectHashTables from the inlined Get's constructor calls (UE4SS takes that singleton in place
    of Get). Every decoded instance must agree and lie in .data or .bss.

The .sym is a line table whose records carry the name of the function each line belongs to, so a
function's entry is its lowest address. Clang aligns function entries to 16 bytes; an address that is
not aligned, or not in code, is refused rather than written.

A refused file is not written and its reason goes to stderr; the others are, and the script exits 1.

The addresses hold for one build only: run this again after every game update, like ue_vtable_layout.py.

Usage: ue_signatures.py <executable> <UE4SS_Signatures directory>
Only stdlib.
"""
from __future__ import annotations

import bisect
import functools
import os
import re
import struct
import sys

from ue_vtable_layout import RECORD, Image, Symbols

ET_EXEC = 2

# Signature file -> the function UE4SS expects it to return (docs/guides/fixing-compatibility-problems.md).
FUNCTIONS = {
    "FName_ToString": "FName::ToString(FString&) const",
    "FName_Constructor": "FName::FName(char16_t const*, EFindName)",
    "StaticConstructObject": "StaticConstructObject_Internal(FStaticConstructObjectParameters const&)",
    # Not the Singleton variable: see the docstring.
    "ConsoleManager": "IConsoleManager::SetupSingleton()",
}
STEP = "FFrame::Step(UObject*, void*)"
STEP_GNATIVES = re.compile(re.escape(b"\x48\x8b\x0c\xcd"))  # mov rcx, QWORD PTR [rcx*8 + disp32]
HASH_TABLES_CTOR = "FUObjectHashTables::FUObjectHashTables()"
MOV_EDI = 0xBF  # mov edi, imm32
CALL = b"\xe8"  # call rel32


class Refused(Exception):
    """A value failed its checks: its file is not written."""


def data_sections(image: Image) -> list[tuple[int, int]]:
    """[start, end) of .data and .bss, where a mutable global lives."""
    d = image.data
    (shoff,) = struct.unpack_from("<Q", d, 40)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", d, 58)
    headers = [struct.unpack_from("<IIQQQQ", d, shoff + i * shentsize) for i in range(shnum)]
    names = headers[shstrndx][4]
    return [(addr, addr + size) for name, _t, _f, addr, _o, size in headers
            if d[names + name:d.find(b"\0", names + name)] in (b".data", b".bss")]


def entry(name: str, lines: set[int], image: Image) -> int:
    if not lines:
        raise Refused(f"{name}: not in the .sym")
    if min(lines) % 16 or not image.is_code(min(lines)):
        raise Refused(f"{name}: lowest address {min(lines):#x} is not a function entry")
    return min(lines)


def one_global(what: str, decoded: set[int], sections: list[tuple[int, int]]) -> int:
    if len(decoded) != 1:
        raise Refused(f"{what}: the expected instructions decode to {sorted(map(hex, decoded))}, not one address")
    (address,) = decoded
    if not any(start <= address < end for start, end in sections):
        raise Refused(f"{what}: decoded {address:#x}, which is not in .data or .bss")
    return address


def gnatives(image: Image, symbols: Symbols, lines: set[int]) -> set[int]:
    """FFrame::Step jumps to GNatives[*Code++]: mov rcx, QWORD PTR [rcx*8 + GNatives]."""
    start = entry(STEP, lines, image)
    # The .sym's records are sorted by address: the function ends where the record after its last line starts.
    i = bisect.bisect_right(range(symbols.count), max(lines) - symbols.base,
                            key=lambda r: RECORD.unpack_from(symbols.data, 4 + r * RECORD.size)[0])
    end = RECORD.unpack_from(symbols.data, 4 + i * RECORD.size)[0] + symbols.base
    vaddr, offset, _n, _x = next(s for s in image.segments if s[0] <= start < s[0] + s[2])
    code = image.data[offset + start - vaddr:offset + end - vaddr]
    return {struct.unpack_from("<i", code, m.end())[0] for m in STEP_GNATIVES.finditer(code)}


def hash_tables(image: Image, ctor: int) -> set[int]:
    """Get's `static FUObjectHashTables Singleton`: each inlined initializer does mov edi, &Singleton; call ctor."""
    d = image.data
    found = set()
    for vaddr, offset, filesz, executable in image.segments:
        if not executable:
            continue
        end = offset + filesz - 4
        call = d.find(CALL, offset + 5, end)
        while call != -1:
            (rel,) = struct.unpack_from("<i", d, call + 1)
            if d[call - 5] == MOV_EDI and vaddr + call - offset + 5 + rel == ctor:
                found.add(struct.unpack_from("<I", d, call - 4)[0])
            call = d.find(CALL, call + 1, end)
    return found


def main(argv: list[str]) -> int:
    exe, out_dir = argv[0], argv[1]
    image = Image(exe)
    (e_type,) = struct.unpack_from("<H", image.data, 16)
    if e_type != ET_EXEC:
        sys.exit(f"{exe}: not a non-PIE executable (e_type {e_type}), so .sym addresses are not runtime addresses")
    symbols = Symbols(exe + ".sym", image.base)
    found = symbols.addresses_of(set(FUNCTIONS.values()) | {STEP, HASH_TABLES_CTOR})
    sections = data_sections(image)
    # Each file's value is computed on its own, so that one refusal leaves the other files written.
    entries = {file: (name, functools.partial(entry, name, found[name], image)) for file, name in FUNCTIONS.items()}
    entries["GNatives"] = (f"GNatives, decoded in {STEP}",
                           lambda: one_global("GNatives", gnatives(image, symbols, found[STEP]), sections))
    entries["GUObjectHashTables"] = (
        f"FUObjectHashTables::Get()'s Singleton, decoded at the calls to {HASH_TABLES_CTOR}",
        lambda: one_global("GUObjectHashTables",
                           hash_tables(image, entry(HASH_TABLES_CTOR, found[HASH_TABLES_CTOR], image)), sections))
    os.makedirs(out_dir, exist_ok=True)
    refused = False
    for file, (what, value) in entries.items():
        try:
            address = value()
        except Refused as e:
            print(e, file=sys.stderr)
            refused = True
            continue
        with open(os.path.join(out_dir, file + ".lua"), "w") as f:
            f.write(f"-- {what}, from {os.path.basename(exe)}.sym by ue_signatures.py\nreturn {address:#x}\n")
        print(f"{file}.lua: {what} at {address:#x}", file=sys.stderr)
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
