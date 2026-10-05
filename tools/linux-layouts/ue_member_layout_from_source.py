"""Builds UEPseudo's member offsets of an engine VERSION for Linux, from Unreal's source.

UE4SS reads engine objects through member offsets measured on MSVC builds. The Itanium ABI lays
some classes out differently: it reuses the tail padding of a non-POD base, so a derived class's
first field can sit INSIDE the base's last 8 bytes. FField is 56 bytes with 52 used, and in a
Linux build FProperty::ArrayDim/ElementSize sit at 0x34/0x38 instead of MSVC's 0x38/0x3C - read
with the MSVC offsets, ElementSize is garbage and a native RegisterHook's memset of the parameters
crashed Palworld.

The compiler knows the real offsets: with the flags of a Linux target (UBT's compile database),
-fdump-record-layouts prints every field of each class. This writes, for each class UEPseudo has
an MSVC layout for, the same <version>_MemberVariableLayout_DefaultSetter_<Class>.cpp with the
Linux offsets, for generated_include_linux.

Usage: ue_member_layout_from_source.py <engine root> <target> <MSVC FunctionBodies dir> <version tag> <out dir> <engine label>
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue_vtable_from_source as source  # noqa: E402

EXTRA_HEADERS = ["UObject/Stack.h", "UObject/Script.h", "UObject/UObjectArray.h", "UObject/Package.h",
                 "UObject/EnumProperty.h", "UObject/FieldPathProperty.h", "Engine/World.h",
                 "Serialization/Archive.h", "Internationalization/Text.h", "Engine/NetDriver.h", "Engine/Console.h"]
SETTER = re.compile(r"^(\d+_\d+)_MemberVariableLayout_DefaultSetter_(\w+)\.cpp$")
EMPLACE = re.compile(r'(\w+(?:::\w+)*)::MemberOffsets\.emplace\(STR\("(\w+)"\),\s*(0x[0-9A-Fa-f]+|\d+)\)')
# clang's layout lines: "  OFFSET[:bits] |<indent><type and name>"; direct members of the dumped
# class are at exactly three spaces after the bar, a base's own fields further in.
FIELD = re.compile(r"^\s*(\d+)(?::\d+-\d+)? \|   (\S.*)$")
TOTAL_SIZE = "UEP_TotalSize"


def classes_of(msvc_dir: str, version: str) -> dict[str, str]:
    """C++ class name -> setter file stem, for every MSVC member layout of the version."""
    found = {}
    for name in os.listdir(msvc_dir):
        match = SETTER.match(name)
        if match and match.group(1) == version:
            stem = match.group(2)
            found[stem.replace("__", "::")] = stem
    return found


def parse_layouts(text: str, wanted: set[str]) -> dict[str, tuple[dict[str, int], int]]:
    """Class -> (direct field -> byte offset, sizeof), from -fdump-record-layouts output."""
    result: dict[str, tuple[dict[str, int], int]] = {}
    blocks = text.split("*** Dumping AST Record Layout")
    for block in blocks[1:]:
        lines = block.strip("\n").splitlines()
        head = re.match(r"\s*0 \| (?:class|struct) (.+)$", lines[0]) if lines else None
        if not head or head.group(1).strip() not in wanted or head.group(1).strip() in result:
            continue
        fields: dict[str, int] = {}
        size = 0
        for line in lines[1:]:
            sized = re.search(r"\[sizeof=(\d+)", line)
            if sized:
                size = int(sized.group(1))
                break
            field = FIELD.match(line)
            if not field:
                continue
            text_part = field.group(2)
            if text_part.endswith(("(base)", "(primary base)", "(virtual base)")) or "vtable pointer" in text_part:
                continue
            name = re.search(r"(\w+)$", text_part)
            if name:
                fields.setdefault(name.group(1), int(field.group(1)))
        result[head.group(1).strip()] = (fields, size)
    return result


def write_member_bodies(layouts: dict[str, tuple[dict[str, int], int]], classes: dict[str, str],
                        msvc_dir: str, version: str, out_dir: str, header: list[str]) -> None:
    """One <version>_MemberVariableLayout_DefaultSetter_<Class>.cpp per class with a Linux layout:
    every key of the MSVC body of the version, at the Linux offset where the layout has the name."""
    os.makedirs(out_dir, exist_ok=True)
    for cls, stem in sorted(classes.items()):
        if cls not in layouts:
            print(f"{cls}: no layout", file=sys.stderr)
            continue
        fields, size = layouts[cls]
        with open(os.path.join(msvc_dir, f"{version}_MemberVariableLayout_DefaultSetter_{stem}.cpp"), encoding="utf-8") as f:
            msvc = [(key, int(value, 0)) for _owner, key, value in EMPLACE.findall(f.read())]
        out = [*header, ""]
        changed, kept = [], []
        for key, msvc_offset in msvc:
            if key == TOTAL_SIZE:
                offset = size
            elif key in fields:
                offset = fields[key]
            else:
                offset = msvc_offset
                kept.append(key)
            if offset != msvc_offset:
                changed.append(f"{key} {msvc_offset:#x}->{offset:#x}")
            note = "  // MSVC offset: not in the Linux layout" if key in kept else ""
            out += [f'if (auto it = {cls}::MemberOffsets.find(STR("{key}")); it == {cls}::MemberOffsets.end())',
                    "{", f'    {cls}::MemberOffsets.emplace(STR("{key}"), 0x{offset:X});{note}', "}", ""]
        with open(os.path.join(out_dir, f"{version}_MemberVariableLayout_DefaultSetter_{stem}.cpp"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out))
        if changed or kept:
            print(f"{cls}: {len(changed)} differ from MSVC ({', '.join(changed[:6])}{' ...' if len(changed) > 6 else ''})"
                  + (f"; {len(kept)} kept MSVC ({', '.join(kept[:4])})" if kept else ""), file=sys.stderr)


def main(argv: list[str]) -> int:
    root, target, msvc_dir, version, out_dir, engine = argv
    classes = classes_of(msvc_dir, version)
    probe = os.path.join(root, "Engine", "Intermediate", "ZzMemberProbe.cpp")
    directory, base_argv = source.compile_command(root, target, probe + ".rsp")
    names = sorted(classes)
    for _attempt in range(len(names)):
        lines = [f'#include "{h}"' for h in source.HEADERS + EXTRA_HEADERS]
        lines += [f"char ZzSize_{n.replace('::', '_')}[sizeof({n})];" for n in names]
        with open(probe, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        run = subprocess.run([*base_argv, probe, "-Wno-error", "-fno-color-diagnostics", "-fsyntax-only", "-Xclang", "-fdump-record-layouts"],
                             cwd=directory, capture_output=True, text=True)
        if run.returncode == 0:
            break
        # A class this version does not declare (or not in these headers) is reported on the line
        # of its sizeof: drop those and try again.
        bad_lines = {int(n) for n in re.findall(r"ZzMemberProbe\.cpp:(\d+):\d+: error", run.stderr)}
        first = len(source.HEADERS + EXTRA_HEADERS) + 1
        dropped = [names[i - first] for i in sorted(bad_lines) if 0 <= i - first < len(names)]
        if not dropped:
            print(run.stderr[-4000:], file=sys.stderr)
            return 1
        print(f"not declared here: {', '.join(dropped)}", file=sys.stderr)
        names = [n for n in names if n not in dropped]
    layouts = parse_layouts(run.stdout, set(names))
    header = [f"// Generated by ue_member_layout_from_source.py from the Unreal Engine {engine} source:",
              "// the member offsets of a Linux (Clang, Itanium) build, read from clang's record layout.",
              "// A name clang did not list keeps the MSVC offset (marked below)."]
    write_member_bodies(layouts, {c: classes[c] for c in names}, msvc_dir, version, out_dir, header)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
