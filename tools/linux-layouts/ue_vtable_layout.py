"""Builds the VTableLayout.ini of a LINUX Unreal server from its executable and its .sym.

UE4SS ships VTableLayout templates measured on MSVC builds. A Clang/Itanium build differs in two
ways that no fixed shift can fix: every root destructor takes two slots instead of one, and MSVC
groups overloads together in reverse declaration order while Itanium keeps the declaration order.
Off by one slot, a hook lands on the neighbouring function (AActor::BeginPlay landed on
RemoveTickPrerequisiteComponent on Dragonwilds) and a virtual call runs the wrong code.

Nothing here is guessed. With RTTI off, the vtable of class X sits in the executable's data as
{0, 0, complete destructor, deleting destructor, ...}, and the .sym names X's destructor in one of
those two slots. Every slot is named by the .sym, and each template entry is matched by its name
and the signature written above it in the template. Two traps, both seen on Dragonwilds:
  - the linker folds identical functions (ICF): UObject's complete destructor carries the name
    UObjectBase::~UObjectBase(), and the empty RegisterDependencies() the name of a destructor of
    an unrelated class. So a slot only counts when its name belongs to the section's own class
    hierarchy, and the vtable is found by EITHER destructor slot.
  - MSVC lists a const and a non-const overload in reverse order; the trailing const tells them apart.
Slots nobody matched become placeholders, so every index stays where the game has it.

Usage: ue_vtable_layout.py <executable> <VTableLayout_X_Y_Template.ini> > VTableLayout.ini
Only stdlib; the executable must be non-PIE (the .sym addresses are added to its load base).
"""
from __future__ import annotations

import mmap
import re
import struct
import sys
from typing import NamedTuple

RECORD = struct.Struct("<QIII")
PT_LOAD = 1
PF_X = 1
DTOR = "__vecDelDtor"
MAX_SLOTS = 1500
OBJECT_CHAIN = ("UObjectBase", "UObjectBaseUtility", "UObject")

# Section -> (classes whose vtable holds its slots, first found wins; bases as UE4SS sums them).
# The bases mirror UE4SSProgram's VTableLayout.ini reader; FMalloc is the reader's fixed "fexec_size".
SECTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...] | int]] = {
    "UObjectBase": (("UObject",), ()),
    "UObjectBaseUtility": (("UObject",), ("UObjectBase",)),
    "UObject": (("UObject",), ("UObjectBase", "UObjectBaseUtility")),
    "UField": (("UField",), OBJECT_CHAIN),
    "UEngine": (("UGameEngine", "UEngine"), OBJECT_CHAIN),
    "UScriptStruct::ICppStructOps": (("UScriptStruct::ICppStructOps",), ()),
    "FField": (("FField",), ()),
    "FProperty": (("FProperty",), ("FField",)),
    "FNumericProperty": (("FNumericProperty",), ("FField", "FProperty")),
    "FMulticastDelegateProperty": (("FMulticastDelegateProperty",), ("FField", "FProperty")),
    "FObjectPropertyBase": (("FObjectPropertyBase",), ("FField", "FProperty")),
    "UStruct": (("UStruct",), (*OBJECT_CHAIN, "UField")),
    "UClass": (("UClass",), (*OBJECT_CHAIN, "UField", "UStruct")),
    "UGameViewportClient": (("UGameViewportClient",), OBJECT_CHAIN),
    "FOutputDevice": (("FOutputDevice", "FOutputDeviceRedirector"), ()),
    "FMalloc": (("FMalloc", "FMallocBinned2", "FMallocBinned3", "FMallocAnsi"), 1),
    "AActor": (("AActor",), OBJECT_CHAIN),
    "AGameModeBase": (("AGameModeBase",), (*OBJECT_CHAIN, "AActor")),
    "AGameMode": (("AGameMode",), (*OBJECT_CHAIN, "AActor", "AGameModeBase")),
    "UPlayer": (("UPlayer",), OBJECT_CHAIN),
    "ULocalPlayer": (("ULocalPlayer",), (*OBJECT_CHAIN, "UPlayer")),
    "UDataTable": (("UDataTable",), OBJECT_CHAIN),
}
# Classes that may implement a slot of a section besides the section, its bases and the vtable's
# own class (an override in between, or an interface the engine folds into the same table).
EXTRA_OWNERS = {
    "UEngine": ("UEngine", "UGameEngine"),
    "FMalloc": ("FMalloc", "FExec", "FUseSystemMallocForNew"),
    "FOutputDevice": ("FOutputDevice",),
    "UPlayer": ("UPlayer", "ULocalPlayer"),
}
# Entries no MSVC template has, written under these names. Itanium gives an override of a
# secondary base's virtual a slot in the PRIMARY vtable too: UPlayer::Exec (from FExec) is there,
# callable with the ULocalPlayer itself as `this`. MSVC only has it in the FExec sub-table, which
# UE4SS reaches through FExecVTableOffsetInLocalPlayer - an offset that does not hold on Linux.
EXTRA_ENTRIES = {
    "UPlayer": [("Exec", "Exec(UWorld*, const wchar_t*, FOutputDevice&)")],
}

# MSVC template spelling -> Itanium demangler spelling, compared without spaces.
TYPE_WORDS = [
    (r"\bwchar_t\b", "char16_t"), (r"\bTCHAR\b", "char16_t"), (r"\buint8\b", "unsignedchar"),
    (r"\buint16\b", "unsignedshort"), (r"\buint32\b", "unsignedint"), (r"\buint64\b", "unsignedlong"),
    (r"\bint8\b", "signedchar"), (r"\bint16\b", "short"), (r"\bint32\b", "int"), (r"\bint64\b", "long"),
    (r"\b(class|struct|enum)\s+", ""), (r"\bconst\b", ""), (r"\s+", ""),
]
METHOD = re.compile(r"(~?[A-Za-z_]\w*|operator\s*\S+)$")


class Signature(NamedTuple):
    owner: str      # "UObject" in "UObject::Serialize(FArchive&)"; empty in the template
    method: str
    params: str     # normalized
    const: bool


def normalize(params: str) -> str:
    for pattern, repl in TYPE_WORDS:
        params = re.sub(pattern, repl, params)
    return params


def parse(text: str) -> Signature | None:
    """'UObject::Serialize(FArchive&) const' or 'const FName& GetX() const' -> Signature."""
    close = text.rfind(")")
    if close < 0:
        return None
    depth = 0
    for i in range(close, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            depth -= 1
            if depth == 0:
                break
    else:
        return None
    head, params, tail = text[:i].rstrip(), text[i + 1:close], text[close + 1:]
    match = METHOD.search(head)
    if not match:
        return None
    qualified = head[:match.start()]
    # clang writes "void *FMalloc::Malloc(...)": the return type's * or & sticks to the owner.
    owner = re.split(r"[\s*&]", qualified[:-2])[-1] if qualified.endswith("::") else ""
    return Signature(owner, match.group(1).replace(" ", ""), normalize(params), "const" in tail)


def read_template(path: str) -> dict[str, list[tuple[str, Signature | None]]]:
    """Section -> [(ini name, signature)] in template order (index 0 is the destructor)."""
    sections: dict[str, list[tuple[str, Signature | None]]] = {}
    current: list[tuple[str, Signature | None]] | None = None
    signature = ""
    with open(path, encoding="utf-8") as f:
        lines = f.read().replace("\r", "").splitlines()
    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], [])
        elif line.startswith(";"):
            signature = line[1:].strip()
        elif line and current is not None:
            current.append((line, parse(signature) if signature else None))
            signature = ""
    return sections


class Image:
    def __init__(self, path: str) -> None:
        self.file = open(path, "rb")  # noqa: SIM115 - kept open for the mmap's lifetime
        self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        (phoff,) = struct.unpack_from("<Q", self.data, 32)
        phentsize, phnum = struct.unpack_from("<HH", self.data, 54)
        self.segments = []
        for i in range(phnum):
            p_type, flags, offset, vaddr, _pa, filesz, _memsz = struct.unpack_from(
                "<IIQQQQQ", self.data, phoff + i * phentsize)
            if p_type == PT_LOAD:
                self.segments.append((vaddr, offset, filesz, bool(flags & PF_X)))
        self.base = min(s[0] for s in self.segments)

    def is_code(self, va: int) -> bool:
        return any(x and v <= va < v + n for v, _o, n, x in self.segments)

    def qword(self, va: int) -> int | None:
        for vaddr, offset, filesz, _x in self.segments:
            if vaddr <= va and va + 8 <= vaddr + filesz:
                return struct.unpack_from("<Q", self.data, offset + va - vaddr)[0]
        return None

    def occurrences(self, values: dict[int, str]) -> dict[str, list[int]]:
        """Per name: every data address holding a qword that carries that name."""
        hits: dict[str, list[int]] = {}
        for vaddr, offset, filesz, executable in self.segments:
            if executable:
                continue
            view = memoryview(self.data)[offset:offset + filesz - filesz % 8]
            try:
                for i, (value,) in enumerate(struct.iter_unpack("<Q", view)):
                    name = values.get(value)
                    if name is not None:
                        hits.setdefault(name, []).append(vaddr + i * 8)
            finally:
                view.release()
        return hits


class Symbols:
    """The .sym: count, 20-byte records {u64 addr, u32 line, u32 file, u32 name}, then text."""

    def __init__(self, path: str, base: int) -> None:
        self.file = open(path, "rb")  # noqa: SIM115 - kept open for the mmap's lifetime
        self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        (self.count,) = struct.unpack_from("<I", self.data, 0)
        self.strings = 4 + self.count * RECORD.size
        self.base = base

    def text(self, offset: int) -> str:
        end = self.data.find(b"\n", self.strings + offset)
        return self.data[self.strings + offset:end].decode("utf-8", "replace")

    def addresses_of(self, names: set[str]) -> dict[str, set[int]]:
        offsets: dict[int, str] = {}
        for name in names:
            needle = b"\n" + name.encode() + b"\n"
            pos = self.data.find(needle, self.strings - 1)
            while pos != -1:
                offsets[pos + 1 - self.strings] = name
                pos = self.data.find(needle, pos + 1)
        found: dict[str, set[int]] = {name: set() for name in names}
        view = memoryview(self.data)[4:self.strings]
        try:
            for addr, _l, _f, name in RECORD.iter_unpack(view):
                hit = offsets.get(name)
                if hit:
                    found[hit].add(addr + self.base)
        finally:
            view.release()
        return found

    def names_at(self, addresses: set[int]) -> dict[int, str]:
        wanted = {a - self.base for a in addresses}
        names: dict[int, str] = {}
        view = memoryview(self.data)[4:self.strings]
        try:
            for addr, _l, _f, name in RECORD.iter_unpack(view):
                if addr in wanted and addr + self.base not in names:
                    names[addr + self.base] = self.text(name)
        finally:
            view.release()
        return names


def address_point(image: Image, hit: int, names: dict[int, str]) -> int | None:
    """The vtable start for a destructor found at `hit` (it may be the complete or the deleting one)."""
    for start in (hit, hit - 8):
        first, second = image.qword(start), image.qword(start + 8)
        top, rtti = image.qword(start - 16), image.qword(start - 8)
        if top == 0 and rtti == 0 and first and second and "::~" in names.get(first, "") \
                and "::~" in names.get(second, ""):
            return start
    return None


def find_vtables(image: Image, symbols: Symbols, classes: list[str]) -> dict[str, int]:
    dtor_names = {f"{c}::~{c.rsplit('::', 1)[-1]}()": c for c in classes}
    by_address = {a: n for n, addrs in symbols.addresses_of(set(dtor_names)).items() for a in addrs}
    hits = image.occurrences(by_address)
    around = {image.qword(h + d) for hs in hits.values() for h in hs for d in (-8, 0, 8)}
    names = symbols.names_at({a for a in around if a})
    vtables: dict[str, int] = {}
    for name, addresses in hits.items():
        for hit in addresses:
            start = address_point(image, hit, names)
            if start is not None:
                vtables[dtor_names[name]] = start
                break
    return vtables


class Match(NamedTuple):
    cls: str                  # the class whose vtable was read
    base: int                 # slots before the section, as UE4SS's ini reader sums them
    slots: dict[str, int]     # template name -> absolute slot in that vtable
    missing: list[str]


def match_layout(template: dict[str, list[tuple[str, Signature | None]]],
                 live_by_class: dict[str, list[Signature | None]]) -> dict[str, Match | str]:
    """Section -> where each template entry really is, or the reason the section was skipped."""
    sizes: dict[str, int] = {}
    result: dict[str, Match | str] = {}
    for section, (candidates, bases) in SECTIONS.items():
        cls = next((c for c in candidates if c in live_by_class), None)
        entries = template.get(section)
        if cls is None or not entries:
            result[section] = "no vtable" if cls is None else "not in template"
            continue
        base = bases if isinstance(bases, int) else sum(sizes.get(b, 0) for b in bases)
        owners = {section, cls, *(() if isinstance(bases, int) else bases), *EXTRA_OWNERS.get(section, ())}
        owners |= {o.rsplit("::", 1)[-1] for o in owners}
        live = live_by_class[cls]
        taken: set[int] = set()
        slots: dict[str, int] = {}
        missing = []
        extra = [(name, parse(signature)) for name, signature in EXTRA_ENTRIES.get(section, [])]
        for ini_name, wanted in entries[1:] + extra:
            if wanted is None:
                missing.append(ini_name)
                continue
            found = [s for s in range(base + 1, len(live))
                     if s not in taken and live[s] and live[s].method == wanted.method
                     and live[s].owner.rsplit("::", 1)[-1] in owners]
            if len(found) > 1:
                exact = [s for s in found if live[s].params == wanted.params and live[s].const == wanted.const]
                found = exact or [s for s in found if live[s].params == wanted.params] or found
            if not found:
                missing.append(ini_name)
                continue
            taken.add(found[0])
            slots[ini_name] = found[0]
        # The reader stacks each section on the sizes of its bases; ending a section at its last
        # matched slot keeps that sum equal to the real slot of whatever comes next.
        sizes[section] = max(slots.values(), default=base) - base
        result[section] = Match(cls, base, slots, missing)
    return result


def report_lines(matches: dict[str, Match | str]) -> list[str]:
    lines = []
    for section, match in matches.items():
        if isinstance(match, str):
            lines.append(f"{section}: skipped ({match})")
            continue
        missing = match.missing
        lines.append(f"{section}: {match.cls}, {len(match.slots)} placed, {len(missing)} not found"
                     + (f" ({', '.join(missing[:5])}{', ...' if len(missing) > 5 else ''})" if missing else ""))
    return lines


def build_ini(template: dict[str, list[tuple[str, Signature | None]]],
              live_by_class: dict[str, list[Signature | None]], source: str) -> tuple[list[str], list[str]]:
    """The VTableLayout.ini lines for the slots of each class, and one report line per section."""
    matches = match_layout(template, live_by_class)
    out = [f"; Generated by {source} for a Clang/Itanium (Linux) build: the slot order of each class's",
           "; real vtable, matched to the template names by method and signature. Placeholders keep the",
           "; index of slots nobody matched.", ""]
    for section, match in matches.items():
        if isinstance(match, str):
            continue
        by_index = {slot - match.base: name for name, slot in match.slots.items()}
        size = max(by_index, default=0)
        out += [f"[{section}]", DTOR]
        out += [by_index.get(i, f"__linux_slot_{i + match.base}") for i in range(1, size + 1)]
        out.append("")
    return out, report_lines(matches)


def main(argv: list[str]) -> int:
    exe, template_path = argv[0], argv[1]
    image = Image(exe)
    symbols = Symbols(exe + ".sym", image.base)
    template = read_template(template_path)

    classes = sorted({c for candidates, _b in SECTIONS.values() for c in candidates})
    vtables = find_vtables(image, symbols, classes)
    slots: dict[str, list[int]] = {}
    for cls, start in vtables.items():
        entries = []
        for i in range(MAX_SLOTS):
            value = image.qword(start + i * 8)
            if value is None or not image.is_code(value):
                break
            entries.append(value)
        slots[cls] = entries
    names = symbols.names_at({a for entries in slots.values() for a in entries})

    live_by_class = {cls: [parse(names.get(a, "")) for a in entries] for cls, entries in slots.items()}
    out, report = build_ini(template, live_by_class, "ue_vtable_layout.py")
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode())
    for line in report:
        print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
