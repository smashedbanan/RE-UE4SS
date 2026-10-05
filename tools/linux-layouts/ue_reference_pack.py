"""Builds the reference pack of an engine VERSION from one game that ships symbols (.sym + .debug).

A Linux server without symbols cannot be laid out from its own binary, and a per-version layout is
not enough either: studios change engine classes. Measured on 4.27 servers: The Front adds 0x18
bytes to FUObjectArray, Soulmask adds 62 virtuals to AGameModeBase, Squad 44 (the reference itself)
adds one to AActor. What does carry over between two games of one version is the CODE of each engine
function - same compiler, same source - and the target's own vtables, which UE servers export in
.dynsym (_ZTV*). The pack holds what a target needs to be laid out from that alone:

- signatures: for each function UE4SS scans for (FName::ToString, the FName constructor,
  StaticConstructObject_Internal, GNatives through FFrame::Step) and each global (GUObjectArray,
  the console manager), a long run of the reference's code with every relocated operand masked. The
  target keeps the shortest prefix that is unique in its own executable;
- sections: the complete vtable layout from the reference's DWARF (absolute slot of each name), plus
  a fingerprint of every slot of each class's vtable, so a target's exported vtable can be aligned
  slot by slot, function by function;
- fuobjectarray: the reference's member offsets and how often its code touches each one, so a
  target with extra members is detected by the shift of that histogram.

Only masked byte patterns, slot numbers and engine names are written - the same kind of data
UE4SS already distributes as AOB signatures.

Usage (ue-layout image: llvm-dwarfdump, llvm-cxxfilt):
  ue_reference_pack.py <executable> <VTableLayout template> <version, e.g. 4.27> <label> > pack.json
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue_layout_from_dwarf as dwarf_tool  # noqa: E402
import ue_vtable_layout as layout  # noqa: E402

WINDOW = 128          # bytes of code kept per signature: the target picks the shortest unique prefix
FINGERPRINT = 24      # bytes per vtable slot: enough to tell functions apart, short enough to survive
MEMBER_SPAN = 0xC0    # bytes of FUObjectArray whose accesses are counted

# Opcodes with ModRM that read [rip+disp32] even without a REX prefix (cmpb $0,[rip+d] and the like).
RIP_OPCODES = frozenset((0x80, 0x81, 0x83, 0xC6, 0xC7, 0x8B, 0x89, 0x8D, 0x3B, 0x39, 0x3A, 0x38, 0x84, 0x85,
                         0xF6, 0xF7, 0xFF, 0x03, 0x01, 0x2B, 0x29, 0x33, 0x31, 0x0B, 0x09, 0x23, 0x21))

FUNCTIONS = {
    "FName_ToString": ("FName::ToString(FString&) const",),
    "FName_Constructor": ("FName::FName(char16_t const*, EFindName)", "FName::FName(wchar_t const*, EFindName)"),
    "StaticConstructObject": ("StaticConstructObject_Internal(",),     # prefix: the signature changed in 4.26
}
FRAME_STEP = "FFrame::Step(UObject*, void*)"
GLOBALS = {
    # lua -> (DWARF variable, anchor functions in the .sym, bytes past the start that still count)
    "GUObjectArray": ("GUObjectArray", ("UObjectBase::~UObjectBase()", "UObjectBase::IsValidLowLevel()",
                                        "UObjectBase::AddObject("), 0x40),
    "ConsoleManager": ("Singleton", ("IConsoleManager::SetupSingleton()", "IConsoleManager::Get()"), 0),
}


def wildcard_mask(code: bytes) -> list[bool]:
    """True = fixed byte; the operand of call/jmp/jcc rel32 and of [rip+disp32] is masked."""
    keep = [True] * len(code)
    i = 0
    while i < len(code):
        b = code[i]
        if b in (0xE8, 0xE9) and i + 5 <= len(code):
            keep[i + 1:i + 5] = [False] * 4
            i += 5
        elif b & 0xF0 == 0x40 and i + 7 <= len(code) and code[i + 2] & 0xC7 == 0x05:
            keep[i + 3:i + 7] = [False] * 4
            i += 7
        elif b == 0x0F and i + 6 <= len(code) and 0x80 <= code[i + 1] <= 0x8F:
            keep[i + 2:i + 6] = [False] * 4
            i += 6
        elif b in RIP_OPCODES and i + 6 <= len(code) and code[i + 1] & 0xC7 == 0x05:
            # [rip+disp32] without a REX prefix (cmpb $0,[rip+d] and the like): the displacement differs per game.
            keep[i + 2:i + 6] = [False] * 4
            i += 6
        else:
            i += 1
    return keep


def masked(code: bytes, keep: list[bool]) -> str:
    return " ".join(f"{code[k]:02X}" if keep[k] else "??" for k in range(len(code)))


def read(image: layout.Image, va: int, size: int) -> bytes:
    for vaddr, offset, filesz, _x in image.segments:
        if vaddr <= va < vaddr + filesz:
            return bytes(image.data[offset + va - vaddr:offset + min(va - vaddr + size, filesz)])
    return b""


def functions_by_prefix(symbols: layout.Symbols, prefixes: tuple[str, ...]) -> dict[str, int]:
    """Name -> lowest address, for every .sym name starting with one of the prefixes."""
    text = symbols.data[symbols.strings:]
    offsets: dict[int, str] = {}
    for prefix in prefixes:
        for m in re.finditer(b"\n" + re.escape(prefix.encode()) + b"[^\n]*", text):
            offsets[m.start() + 1] = m.group(0)[1:].decode(errors="replace")
    found: dict[str, int] = {}
    view = memoryview(symbols.data)[4:symbols.strings]
    try:
        for addr, _l, _f, name in layout.RECORD.iter_unpack(view):
            if name in offsets:
                n = offsets[name]
                found[n] = min(found.get(n, 1 << 62), addr + symbols.base)
    finally:
        view.release()
    return found


def dynsym(path: str) -> dict[str, int]:
    """Exported symbol -> value (the vtables, _ZTV*, are what the target is aligned by)."""
    d = open(path, "rb").read()
    (shoff,) = struct.unpack_from("<Q", d, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", d, 0x3A)
    secs = [struct.unpack_from("<IIQQQQIIQQ", d, shoff + i * shentsize) for i in range(shnum)]
    out: dict[str, int] = {}
    for sec in secs:
        if sec[1] != 11:   # SHT_DYNSYM
            continue
        strtab = secs[sec[6]]
        for i in range(sec[5] // 24):
            st_name, _info, _other, _shndx, value, _size = struct.unpack_from("<IBBHQQ", d, sec[4] + i * 24)
            end = d.index(b"\0", strtab[4] + st_name)
            if value:
                out[d[strtab[4] + st_name:end].decode(errors="replace")] = value
    return out


def mangled_vtable(cls: str) -> str:
    parts = cls.split("::")
    return f"_ZTV{len(cls)}{cls}" if len(parts) == 1 else "_ZTVN" + "".join(f"{len(p)}{p}" for p in parts) + "E"


def vtable_slots(image: layout.Image, address_point: int) -> list[int]:
    slots, at = [], address_point
    while True:
        value = image.qword(at)
        if value is None or not image.is_code(value):
            return slots
        slots.append(value)
        at += 8


def fingerprint(image: layout.Image, va: int) -> str:
    code = read(image, va, FINGERPRINT)
    return masked(code, wildcard_mask(code))


def function_signatures(image: layout.Image, symbols: layout.Symbols) -> dict[str, dict]:
    out = {}
    for lua, names in FUNCTIONS.items():
        exact = {n: min(a) for n, a in symbols.addresses_of({n for n in names if n.endswith(")")}).items() if a}
        prefixed = functions_by_prefix(symbols, tuple(n for n in names if not n.endswith(")") or n.endswith("(")))
        va = next(iter(exact.values()), None) or next(iter(prefixed.values()), None)
        if va:
            code = read(image, va, WINDOW)
            out[lua] = {"code": masked(code, wildcard_mask(code)), "match": "return MatchAddress"}
    step = next(iter(symbols.addresses_of({FRAME_STEP})[FRAME_STEP]), None)
    code = read(image, step, 512) if step else b""
    for i in range(len(code) - 8):
        rex, opcode, modrm, sib = code[i:i + 4]
        # mov r64, [disp32 + reg*8] with no base: the read of the Blueprint VM table, disp32 = GNatives.
        if rex & 0xF8 == 0x48 and opcode == 0x8B and modrm & 0xC7 == 0x04 and sib & 0xC7 == 0xC5:
            window = code[i:i + WINDOW]
            keep = wildcard_mask(window)
            keep[4:8] = [False] * 4
            out["GNatives"] = {"code": masked(window, keep), "match": "return DerefToInt32(MatchAddress + 0x4)",
                               "min": 8}
            break
    return out


def dwarf_addresses(debug: str, name: str) -> set[int]:
    run = subprocess.run([dwarf_tool.tool("llvm-dwarfdump"), f"--name={name}", debug],
                         capture_output=True, text=True, errors="replace", check=False)
    return {int(a, 16) for a in re.findall(r"DW_OP_addr 0x([0-9a-f]+)", run.stdout) if int(a, 16)}


def find_reference(code: bytes, va: int, targets: set[int], span: int) -> tuple[int, str, int, int] | None:
    """(operand position, 'abs' | 'rip+N', global, offset into it) of the first reference to a target."""
    def hit(addr: int) -> int | None:
        if not span:    # the console manager's "Singleton" has thousands of namesakes: a set lookup
            return addr if addr in targets else None
        return next((t for t in targets if t <= addr <= t + span), None)
    for i in range(len(code) - 4):
        (v,) = struct.unpack_from("<i", code, i)
        t = hit(v)
        if t is not None:
            return i, "abs", t, v - t
        for tail in (0, 1, 4):
            t = hit(va + i + 4 + tail + v)
            if t is not None:
                return i, f"rip+{4 + tail}", t, va + i + 4 + tail + v - t
    return None


def global_signatures(image: layout.Image, symbols: layout.Symbols, debug: str) -> dict[str, list[dict]]:
    """Every anchor that reaches the global, in order: a studio's build may have changed the code
    of one of them (Squad 44's ~UObjectBase does not carry over to The Front; IsValidLowLevel does)."""
    out: dict[str, list[dict]] = {}
    for lua, (variable, anchors, span) in GLOBALS.items():
        addresses = dwarf_addresses(debug, variable)
        for name, va in sorted(functions_by_prefix(symbols, anchors).items(), key=lambda kv: len(kv[0])):
            code = read(image, va, 4096)
            found = find_reference(code, va, addresses, span)
            if not found:
                continue
            pos, kind, address, delta = found
            window = code[pos:pos + WINDOW]
            keep = wildcard_mask(window)
            keep[0:4] = [False] * 4
            if kind == "abs":
                match = f"return DerefToInt32(MatchAddress) - 0x{delta:X}"
            else:
                match = f"return MatchAddress + 0x{int(kind[4:]):X} + DerefToInt32(MatchAddress) - 0x{delta:X}"
            # The window starts AT the operand (masked), so the pattern is read from MatchAddress.
            out.setdefault(lua, []).append({"code": masked(window, keep), "match": match, "anchor": name,
                                            "global": address, "min": 12})
    return out


def section_pack(executable: str, debug: str, template_path: str) -> dict[str, dict]:
    image = layout.Image(executable)
    exported = dynsym(executable)
    dwarf = dwarf_tool.Dwarf(debug)
    classes = sorted({c for candidates, _b in layout.SECTIONS.values() for c in candidates})
    dwarf_tool.prefetch_hierarchy(dwarf, classes)
    live = dwarf_tool.live_vtables(dwarf, classes)
    matches = layout.match_layout(layout.read_template(template_path), live)
    out = {}
    for section, m in matches.items():
        if isinstance(m, str):
            continue
        vt = exported.get(mangled_vtable(m.cls))
        if not vt:
            continue
        out[section] = {"cls": m.cls, "base": m.base, "slots": m.slots,
                        "fingerprints": [fingerprint(image, f) for f in vtable_slots(image, vt + 16)]}
    return out


def fuobjectarray_pack(executable: str, debug: str) -> dict:
    image = layout.Image(executable)
    address = next(iter(dwarf_addresses(debug, "GUObjectArray")), None)
    if address is None:
        return {}
    dwarf = dwarf_tool.Dwarf(debug)
    offsets, size = dwarf_tool.member_layouts(dwarf, ["FUObjectArray"]).get("FUObjectArray", ({}, 0))
    counts: dict[int, int] = {}
    for vaddr, offset, filesz, executable_segment in image.segments:
        if not executable_segment:
            continue
        view = image.data[offset:offset + filesz]
        for m in re.finditer(rb"[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", view):
            i = m.start() + 1
            if i + 4 > len(view):
                continue
            (d,) = struct.unpack_from("<i", view, i)
            for tail in (0, 1, 4):
                a = vaddr + i + 4 + tail + d
                if address <= a < address + MEMBER_SPAN:
                    counts[a - address] = counts.get(a - address, 0) + 1
                    break
    return {"members": offsets, "size": size, "access_counts": {f"{k:#x}": v for k, v in sorted(counts.items())}}


def unique_length(image: layout.Image, code: str, minimum: int) -> int:
    """Shortest prefix (from `minimum`, 4 by 4) the reference itself has only once: a target must not
    accept less - a short pattern can be unique there and still sit on the wrong code."""
    tokens = [None if t == "??" else int(t, 16) for t in code.split()]
    for n in range(max(12, minimum), len(tokens) + 1, 4):
        regex = b"".join(b"." if t is None else re.escape(bytes([t])) for t in tokens[:n])
        hits = 0
        for _ in re.finditer(regex, image.data, re.DOTALL):
            hits += 1
            if hits > 1:
                break
        if hits == 1:
            return n
    return len(tokens)


def main(argv: list[str]) -> int:
    executable, template_path, version, label = argv
    debug = executable + ".debug"
    image = layout.Image(executable)
    symbols = layout.Symbols(executable + ".sym", image.base)
    pack = {
        "version": version,
        "reference": label,
        "signatures": {**function_signatures(image, symbols), **global_signatures(image, symbols, debug)},
        "sections": section_pack(executable, debug, template_path),
        "fuobjectarray": fuobjectarray_pack(executable, debug),
    }
    for value in pack["signatures"].values():
        for entry in value if isinstance(value, list) else [value]:
            entry["min"] = unique_length(image, entry["code"], entry.get("min", 12))
    for name in ("FName_ToString", "FName_Constructor", "GNatives"):
        if name not in pack["signatures"]:
            print(f"warning: {name} not found in the reference", file=sys.stderr)
    json.dump(pack, sys.stdout, indent=1)
    print(f"{len(pack['signatures'])} signatures, {len(pack['sections'])} sections", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
