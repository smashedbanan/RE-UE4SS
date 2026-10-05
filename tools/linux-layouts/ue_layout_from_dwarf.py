"""Builds UEPseudo's Linux layouts of an engine VERSION from one game's DWARF (its .debug file).

ue_vtable_from_source.py and ue_member_layout_from_source.py ask the compiler, and need Epic's
source of that version plus a working UnrealBuildTool - one Docker setup per version, and the older
the version the harder (Epic's CDN no longer serves 5.1's dependency packs). Many Linux servers ship
the answer already: the `.debug` next to the executable is the full DWARF of the build, and DWARF
has exactly what those tools extract:

- every virtual method with its slot (DW_AT_vtable_elem_location) - the primary vtable of a class
  is its primary base's, overlaid with its own virtuals (overrides keep the slot, new ones append;
  an override of a SECONDARY base's virtual also gets a primary slot, and DWARF says which);
- every field with its byte offset (DW_AT_data_member_location, DW_AT_data_bit_offset for bit
  fields) and every class's size.

The game is only the measuring device: its engine classes are the version's, unless the studio
changed them (Dragonwilds adds virtuals to AActor) - compare two games of one version when you can.

Output, like the source tools: VTableLayout.ini on stdout, and with --bodies the
<version>_VTableOffsets_*.cpp and <version>_MemberVariableLayout_DefaultSetter_*.cpp for
deps/first/Unreal/generated_include_linux/FunctionBodies.

Usage (needs llvm-dwarfdump and llvm-cxxfilt, e.g. in the ue-layout image):
  ue_layout_from_dwarf.py <game .debug> <VTableLayout template> \
      [--bodies <MSVC FunctionBodies dir> <version tag, e.g. 4_27> <out dir> <label>] > VTableLayout.ini
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue_member_layout_from_source as members  # noqa: E402
import ue_vtable_from_source as source  # noqa: E402
import ue_vtable_layout as layout  # noqa: E402

DIE = re.compile(r"^0x[0-9a-f]+:( +)(DW_TAG_\w+|NULL)")
ATTR = re.compile(r"^\s+(DW_AT_\w+)\s+\((.*)\)\s*$")
TYPE_NAME = re.compile(r'^0x[0-9a-f]+ "(.*)"$')
CLASS_TAGS = ("DW_TAG_class_type", "DW_TAG_structure_type")


def tool(name: str) -> str:
    """llvm-dwarfdump / llvm-cxxfilt, with or without the version suffix the distro adds."""
    for candidate in (name, *(f"{name}-{v}" for v in range(22, 12, -1))):
        if shutil.which(candidate):
            return candidate
    raise SystemExit(f"{name} not found")


class Node(NamedTuple):
    tag: str
    attrs: dict[str, str]
    children: list[Node]


def parse_dump(text: str) -> list[Node]:
    """llvm-dwarfdump --show-children output -> trees, nesting by the indentation of each DIE."""
    roots: list[Node] = []
    stack: list[tuple[int, Node]] = []
    current: Node | None = None
    for line in text.splitlines():
        head = DIE.match(line)
        if head:
            depth, tag = len(head.group(1)), head.group(2)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if tag == "NULL":
                current = None
                continue
            current = Node(tag, {}, [])
            (stack[-1][1].children if stack else roots).append(current)
            stack.append((depth, current))
            continue
        attr = ATTR.match(line)
        if attr and current is not None:
            current.attrs.setdefault(attr.group(1), attr.group(2))
    return roots


def number(value: str) -> int:
    """'0x48', '(DW_OP_constu 0x2c)' or 'DW_OP_plus_uconst 0x10' -> the integer."""
    found = re.findall(r"0x[0-9a-fA-F]+|\b\d+\b", value)
    return int(found[-1], 0) if found else 0


def type_name(value: str) -> str:
    match = TYPE_NAME.match(value.strip())
    return match.group(1) if match else ""


def unquote(value: str) -> str:
    return value.strip().strip('"')


class Dwarf:
    """Class definitions read on demand from one .debug, by name (the accelerator tables make each
    lookup take a second, so only the classes UE4SS needs and their bases are ever read)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.dwarfdump = tool("llvm-dwarfdump")
        self.cache: dict[str, Node | None] = {}

    def definition(self, qualified: str) -> Node | None:
        if qualified in self.cache:
            return self.cache[qualified]
        leaf = qualified.rsplit("::", 1)[-1]
        self.cache[qualified] = self._first_definition(leaf)
        return self.cache[qualified]

    def _first_definition(self, leaf: str) -> Node | None:
        return self.prefetch([leaf]).get(leaf)

    def prefetch(self, qualified_names: list[str]) -> dict[str, Node]:
        """Reads the first full definition of each name in ONE pass, and caches it.

        The lookup streams and stops once every name has a definition: a class used everywhere
        (UObject) is defined once per compilation unit, and dumping them all takes many minutes. Each
        llvm-dwarfdump run costs ~10 s on a 2 GB .debug however many names it gets, so callers
        prefetch a whole level of the class hierarchy at once."""
        by_leaf = {q.rsplit("::", 1)[-1]: q for q in qualified_names if q not in self.cache}
        if not by_leaf:
            return {}
        args = [self.dwarfdump, *(f"--name={leaf}" for leaf in sorted(by_leaf)), "--show-children", self.path]
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
        if proc.stdout is None:
            raise RuntimeError("llvm-dwarfdump without stdout")
        found: dict[str, Node] = {}
        block: list[str] = []
        root_depth = None
        try:
            for line in proc.stdout:
                head = DIE.match(line)
                depth = len(head.group(1)) if head else None
                if root_depth is None and depth is not None:
                    root_depth = depth
                if depth is not None and depth == root_depth and block:
                    self._collect(block, by_leaf, found)
                    if len(found) == len(by_leaf):
                        break
                    block = []
                block.append(line.rstrip("\n"))
            else:
                self._collect(block, by_leaf, found)
        finally:
            proc.kill()
            proc.wait()
        result = {}
        for leaf, qualified in by_leaf.items():
            self.cache[qualified] = found.get(leaf)
            if leaf in found:
                result[leaf] = found[leaf]
        return result

    @staticmethod
    def _collect(block: list[str], wanted: dict[str, str], found: dict[str, Node]) -> None:
        for node in parse_dump("\n".join(block)):
            name = unquote(node.attrs.get("DW_AT_name", ""))
            if node.tag in CLASS_TAGS and name in wanted and name not in found \
                    and "DW_AT_declaration" not in node.attrs and "DW_AT_byte_size" in node.attrs:
                found[name] = node


class Virtual(NamedTuple):
    slot: int
    linkage: str      # mangled name ('' for a destructor DWARF declares without one)
    destructor: str   # "C::~C()" when it is the destructor


def own_virtuals(cls: str, node: Node) -> list[Virtual]:
    found = []
    for child in node.children:
        location = child.attrs.get("DW_AT_vtable_elem_location")
        if child.tag != "DW_TAG_subprogram" or location is None:
            continue
        name = unquote(child.attrs.get("DW_AT_name", ""))
        leaf = cls.rsplit("::", 1)[-1]
        dtor = f"{cls}::~{leaf}()" if name.startswith("~") else ""
        found.append(Virtual(number(location), unquote(child.attrs.get("DW_AT_linkage_name", "")), dtor))
    return found


def bases(node: Node) -> list[tuple[str, int]]:
    return [(type_name(c.attrs.get("DW_AT_type", "")), number(c.attrs.get("DW_AT_data_member_location", "0")))
            for c in node.children if c.tag == "DW_TAG_inheritance"]


class VTables:
    """Class -> its primary vtable as signature strings, slot by slot."""

    def __init__(self, dwarf: Dwarf) -> None:
        self.dwarf = dwarf
        self.memo: dict[str, dict[int, str | Virtual]] = {}

    def is_dynamic(self, cls: str) -> bool:
        node = self.dwarf.definition(cls)
        if node is None:
            return False
        if "DW_AT_containing_type" in node.attrs or own_virtuals(cls, node):
            return True
        return any(self.is_dynamic(base) for base, _ in bases(node))

    def slots(self, cls: str) -> dict[int, str | Virtual]:
        if cls in self.memo:
            return self.memo[cls]
        node = self.dwarf.definition(cls)
        table: dict[int, str | Virtual] = {}
        if node is not None:
            # The primary base is the first dynamic one at offset 0 (an empty base like
            # FUseSystemMallocForNew shares that offset without a vtable).
            primary = next((b for b, offset in bases(node) if offset == 0 and self.is_dynamic(b)), None)
            if primary:
                table.update(self.slots(primary))
            for virtual in own_virtuals(cls, node):
                table[virtual.slot] = virtual
                if virtual.destructor:
                    # Itanium: complete and deleting destructor, two slots for one declaration.
                    table[virtual.slot + 1] = virtual
        self.memo[cls] = table
        return table


def demangle(names: list[str]) -> dict[str, str]:
    if not names:
        return {}
    run = subprocess.run([tool("llvm-cxxfilt")], input="\n".join(names) + "\n",
                         capture_output=True, text=True, check=True)
    return dict(zip(names, run.stdout.splitlines(), strict=True))


def live_vtables(dwarf: Dwarf, classes: list[str]) -> dict[str, list[layout.Signature | None]]:
    tables = VTables(dwarf)
    raw = {cls: tables.slots(cls) for cls in classes}
    raw = {cls: table for cls, table in raw.items() if table}
    names = demangle(sorted({v.linkage for t in raw.values() for v in t.values()
                             if isinstance(v, Virtual) and v.linkage}))
    live: dict[str, list[layout.Signature | None]] = {}
    for cls, table in raw.items():
        out: list[layout.Signature | None] = []
        for slot in range(max(table) + 1):
            entry = table.get(slot)
            text = ""
            if isinstance(entry, Virtual):
                text = entry.destructor or names.get(entry.linkage, "")
            out.append(layout.parse(text) if text else None)
        live[cls] = out
    return live


def member_layouts(dwarf: Dwarf, classes: list[str]) -> dict[str, tuple[dict[str, int], int]]:
    """Class -> (direct field -> byte offset, sizeof), like the clang record-layout dump."""
    result = {}
    for cls in classes:
        node = dwarf.definition(cls)
        if node is None:
            continue
        fields: dict[str, int] = {}
        for child in node.children:
            if child.tag != "DW_TAG_member" or "DW_AT_external" in child.attrs:
                continue
            name = unquote(child.attrs.get("DW_AT_name", ""))
            if "DW_AT_data_member_location" in child.attrs:
                fields.setdefault(name, number(child.attrs["DW_AT_data_member_location"]))
            elif "DW_AT_data_bit_offset" in child.attrs:
                fields.setdefault(name, number(child.attrs["DW_AT_data_bit_offset"]) // 8)
        result[cls] = (fields, number(node.attrs["DW_AT_byte_size"]))
    return result


def prefetch_hierarchy(dwarf: Dwarf, names: list[str]) -> None:
    """The classes and every base up the chain, one llvm-dwarfdump run per level."""
    pending = sorted(set(names))
    while pending:
        dwarf.prefetch(pending)
        found = [dwarf.cache.get(n) for n in pending]
        pending = sorted({base for node in found if node for base, _o in bases(node)} - set(dwarf.cache))


def main(argv: list[str]) -> int:
    slots_json = None
    if "--slots-json" in argv:
        i = argv.index("--slots-json")
        slots_json, argv = argv[i + 1], argv[:i] + argv[i + 2:]
    bodies = None
    if "--bodies" in argv:
        i = argv.index("--bodies")
        bodies, argv = argv[i + 1:i + 5], argv[:i] + argv[i + 5:]
    debug_path, template_path = argv
    dwarf = Dwarf(debug_path)
    # Every candidate class of each section: in a game's DWARF the abstract ones (UEngine, FMalloc)
    # are defined too, and the section's own class carries no override from a subclass.
    classes = sorted({c for candidates, _b in layout.SECTIONS.values() for c in candidates})
    wanted = members.classes_of(bodies[0], bodies[1]) if bodies else {}
    prefetch_hierarchy(dwarf, [*classes, *wanted])
    live = live_vtables(dwarf, classes)
    template = layout.read_template(template_path)
    out, report = layout.build_ini(template, live, "ue_layout_from_dwarf.py")
    if slots_json:
        # Secao -> classe medida, base e posicao ABSOLUTA de cada nome: e o que um jogo de outro
        # estudio da mesma versao reposiciona, alinhando as vtables pelo codigo (ver o README).
        import json
        matches = layout.match_layout(template, live)
        with open(slots_json, "w", encoding="utf-8") as f:
            json.dump({s: {"cls": m.cls, "base": m.base, "slots": m.slots}
                       for s, m in matches.items() if not isinstance(m, str)}, f, indent=1)
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode())
    for line in report:
        print(line, file=sys.stderr)
    if bodies:
        msvc_dir, version, out_dir, label = bodies
        written = source.write_bodies(layout.match_layout(template, live), msvc_dir, version, out_dir, label,
                                      origin=f"// Generated by ue_layout_from_dwarf.py from {label}: the")
        print(f"{len(written)} vtable bodies in {out_dir}", file=sys.stderr)
        layouts = member_layouts(dwarf, sorted(wanted))
        header = [f"// Generated by ue_layout_from_dwarf.py from {label}:",
                  "// the member offsets of a Linux (Clang, Itanium) build, read from its DWARF.",
                  "// A name the DWARF did not list keeps the MSVC offset (marked below)."]
        members.write_member_bodies(layouts, wanted, msvc_dir, version, out_dir, header)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
