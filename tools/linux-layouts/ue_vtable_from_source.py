"""Builds the Linux VTableLayout.ini of an engine VERSION from Unreal's source, no game needed.

ue_vtable_layout.py reads the layout out of one game's executable and needs its .sym; a game
without one (Palworld) or a client build gets nothing from it. The engine classes UE4SS calls
into have the same vtable in every stock build of a version, so the layout can come from the
compiler instead: compile, with the exact flags of a Linux target (UnrealBuildTool's compile
database), one file holding an empty subclass of each class with an out-of-line virtual. That
forces each vtable to be emitted, and -fdump-vtable-layouts prints every slot of it under its
declared signature - inherited ones included, no ICF, no guessing.

Usage (inside the engine tree, after UBT -Mode=GenerateClangDatabase for <target>):
  ue_vtable_from_source.py <engine root> <target> <VTableLayout template> > VTableLayout.ini
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue_vtable_layout as layout  # noqa: E402

# Header of each class the layout needs (the classes of ue_vtable_layout.SECTIONS).
HEADERS = [
    "CoreMinimal.h", "UObject/Object.h", "UObject/Class.h", "UObject/UnrealType.h", "HAL/MemoryBase.h",
    "Misc/OutputDevice.h", "Misc/OutputDeviceRedirector.h", "Engine/Engine.h", "Engine/GameEngine.h",
    "GameFramework/Actor.h", "GameFramework/GameModeBase.h", "GameFramework/GameMode.h", "Engine/Player.h",
    "Engine/LocalPlayer.h", "Engine/GameViewportClient.h", "Engine/DataTable.h",
]
PROBE_PREFIX = "FZzVTableProbe_"
ENTRY = re.compile(r"^\s*(\d+) \| (.*)$")
FLAGS = re.compile(r"\s*\[(pure|deleted|complete|deleting|scalar deleting|vector deleting|this adjustment[^\]]*|return adjustment[^\]]*)\]")


def probe_source(classes: list[str]) -> str:
    lines = ["// Generated: one empty subclass per class, so clang emits (and dumps) its vtable."]
    lines += [f'#include "{h}"' for h in HEADERS]
    for cls in classes:
        name = PROBE_PREFIX + cls.replace("::", "_")
        lines += [f"struct {name} : {cls} {{ virtual void ZzVTableProbe(); }};",
                  f"void {name}::ZzVTableProbe() {{}}"]
    return "\n".join(lines) + "\n"


def compile_command(root: str, target: str, rsp_out: str) -> tuple[str, list[str]]:
    """The directory and argv of an Engine-module file of <target>, minus its input and outputs.

    The flags stay in a response file (written next to the original): clang reads its quoting,
    which a re-split here would get wrong.
    """
    with open(os.path.join(root, "compile_commands.json"), encoding="utf-8") as f:
        db = json.load(f)
    # 5.6 compiles the Engine module in unity files (Module.Engine.N.cpp); 5.1's database lists
    # each .cpp, with the target only in the response file's path.
    def command_of(e: dict) -> str:
        return e.get("command") or " ".join(e["arguments"])
    entry = next((e for e in db if "/Engine/Module.Engine." in e["file"] and f"/{target}" in e["file"]), None) \
        or next(e for e in db if "/Runtime/Engine/" in e["file"] and f"/{target}/" in command_of(e))
    command = command_of(entry)
    compiler, rsp = re.match(r'"?([^"\s]+)"? @"([^"]+)"', command).groups()
    directory = entry["directory"]
    with open(os.path.join(directory, rsp), encoding="utf-8") as f:
        kept = [line for line in f.read().splitlines()
                if line.strip() and line.strip() not in ("-c", "-Werror")
                and not line.strip().startswith(("-MD", "-o ", '"'))]
    # Before 5.2 the engine compiles against its own libc++ (ThirdParty/Unix/LibCxx), whose
    # extension-less headers come from Setup.sh - and Epic's CDN no longer serves 5.1's packs. The
    # image's libc++ of the same major stands in: the standard library's headers do not change the
    # vtable of any engine class.
    bundled = os.path.join(root, "Engine/Source/ThirdParty/Unix/LibCxx/include/c++/v1")
    if os.path.isdir(bundled) and not os.path.exists(os.path.join(bundled, "initializer_list")):
        major = re.search(r"llvm-(\d+)|clang\+\+-(\d+)", os.path.realpath(compiler))
        major = major and (major.group(1) or major.group(2))
        system = f"/usr/lib/llvm-{major}/include/c++/v1"
        kept = [f'-isystem"{system}"' if "ThirdParty/Unix/LibCxx/include/c++/v1" in line
                else line for line in kept if not line.endswith('ThirdParty/Unix/LibCxx/include"')]
    with open(rsp_out, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    rest = command[command.index(rsp) + len(rsp) + 1:].split()
    return directory, [compiler, f"@{rsp_out}", *rest]


def parse_dump(text: str) -> dict[str, list[layout.Signature | None]]:
    """Probe class -> slots of its primary vtable (index 0 = address point)."""
    result: dict[str, list[layout.Signature | None]] = {}
    current: list[layout.Signature | None] | None = None
    seen_top = 0
    for line in text.splitlines():
        header = re.match(r"^Vtable for '([^']+)' \(\d+ entries\)\.", line)
        if header:
            name = header.group(1)
            current = result.setdefault(name[len(PROBE_PREFIX):], []) if name.startswith(PROBE_PREFIX) else None
            seen_top = 0
            continue
        if current is None:
            continue
        entry = ENTRY.match(line)
        if not entry:
            if not line.strip():
                current = None
            continue
        body = entry.group(2).strip()
        if body.startswith("offset_to_top"):
            seen_top += 1
            if seen_top > 1:  # a secondary vtable starts: the primary one is complete
                current = None
            continue
        if body.endswith("RTTI"):
            continue
        current.append(layout.parse(FLAGS.sub("", body)))
    return {k.replace("_", "::", 1) if k.startswith("UScriptStruct_") else k: v for k, v in result.items()}


EMPLACE = re.compile(r'VTableLayoutMap\.emplace\(STR\("([^"]+)"\),\s*(0x[0-9A-Fa-f]+)\)')
# Marker the Linux bodies leave in each map: UnrealInitializer's Itanium shift skips a map that has
# it (the offsets are already Itanium), and keeps shifting the MSVC ones of classes without a body.
ITANIUM_MARKER = "__itanium_layout"


def write_bodies(matches: dict[str, layout.Match | str], msvc_dir: str, version: str, out_dir: str,
                 engine: str, origin: str = "") -> list[str]:
    """One <version>_VTableOffsets_<Class>_FunctionBody.cpp per section, for UEPseudo's Linux include dir.

    The MSVC body of the same version gives every key UE4SS may look up: it registers a function
    under its plain and its decorated name at the same offset, and the template carries only one
    of them. Keys sharing an MSVC offset with a matched template name get that name's Linux slot.
    """
    written = []
    os.makedirs(out_dir, exist_ok=True)
    for section, match in matches.items():
        if isinstance(match, str) or not match.slots:
            continue
        stem = section.replace("::", "__")
        msvc_path = os.path.join(msvc_dir, f"{version}_VTableOffsets_{stem}_FunctionBody.cpp")
        aliases: dict[str, list[str]] = {}
        if os.path.exists(msvc_path):
            with open(msvc_path, encoding="utf-8") as f:
                by_offset: dict[str, list[str]] = {}
                for key, offset in EMPLACE.findall(f.read()):
                    by_offset.setdefault(offset.lower(), []).append(key)
            for group in by_offset.values():
                for key in group:
                    aliases[key] = group
        lines = [origin or f"// Generated by ue_vtable_from_source.py from the Unreal Engine {engine} source: the",
                 "// Itanium (Linux, Clang) slots of this class, read from the vtable clang emits."
                 if not origin else "// Itanium (Linux, Clang) slots of this class, read from its DWARF.",
                 f'{section}::VTableLayoutMap.emplace(STR("{ITANIUM_MARKER}"), 0x0);', ""]
        keys: dict[str, int] = {}
        for name, slot in sorted(match.slots.items(), key=lambda item: item[1]):
            for key in aliases.get(name, [name]):
                keys.setdefault(key, slot)
        for key, slot in sorted(keys.items(), key=lambda item: (item[1], item[0])):
            lines += [f'if (auto it = {section}::VTableLayoutMap.find(STR("{key}")); it == {section}::VTableLayoutMap.end())',
                      "{",
                      f'    {section}::VTableLayoutMap.emplace(STR("{key}"), 0x{slot * 8:X});',
                      "}", ""]
        path = os.path.join(out_dir, f"{version}_VTableOffsets_{stem}_FunctionBody.cpp")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))
        written.append(os.path.basename(path))
    return written


def main(argv: list[str]) -> int:
    # Optional: --bodies <MSVC FunctionBodies dir> <version tag, e.g. 5_06> <out dir> <engine label>
    bodies = None
    if "--bodies" in argv:
        i = argv.index("--bodies")
        bodies, argv = argv[i + 1:i + 5], argv[:i] + argv[i + 5:]
    root, target, template_path = argv
    # From source the class of each section itself is probed (FMalloc, UEngine, FOutputDevice):
    # in a game binary only a concrete subclass has a vtable, here every class does, and the
    # section's own one carries no override from a subclass. UObjectBase(Utility) live in UObject's.
    classes = sorted({"UObject" if s.startswith("UObjectBase") else s for s in layout.SECTIONS})
    # Not every candidate exists in every version (FMallocBinned3 is newer than 5.1); the compiler
    # names the ones it cannot find, and they are dropped before the real run.
    probe = os.path.join(root, "Engine", "Intermediate", "ZzVTableProbe.cpp")
    directory, argv_base = compile_command(root, target, probe + ".rsp")
    for _attempt in range(len(classes)):
        with open(probe, "w", encoding="utf-8") as f:
            f.write(probe_source(classes))
        run = subprocess.run([*argv_base, probe, "-Wno-error", "-S", "-emit-llvm", "-o", "/dev/null",
                              "-Xclang", "-fdump-vtable-layouts"],
                             cwd=directory, capture_output=True, text=True)
        unknown = set(re.findall(r"(?:no type named|unknown class name|expected class name)[^']*'([^']+)'", run.stderr))
        unknown |= set(re.findall(r"use of undeclared identifier '([^']+)'", run.stderr))
        dropped = [c for c in classes if c.rsplit("::", 1)[-1] in unknown or c in unknown]
        if run.returncode == 0 or not dropped:
            break
        classes = [c for c in classes if c not in dropped]
        print(f"not in this version: {', '.join(dropped)}", file=sys.stderr)
    if run.returncode != 0:
        print(run.stderr[-6000:], file=sys.stderr)
        return 1
    live = parse_dump(run.stdout)
    if os.environ.get("VT_SLOTS_JSON"):  # every slot, for comparing with a game's own vtable
        with open(os.environ["VT_SLOTS_JSON"], "w", encoding="utf-8") as f:
            json.dump({c: [f"{x.owner}::{x.method}({x.params})" if x else None for x in slots]
                       for c, slots in live.items()}, f, indent=0)
    template = layout.read_template(template_path)
    out, report = layout.build_ini(template, live, "ue_vtable_from_source.py")
    sys.stdout.buffer.write(("\n".join(out) + "\n").encode())
    if bodies:
        msvc_dir, version, out_dir, engine = bodies
        written = write_bodies(layout.match_layout(template, live), msvc_dir, version, out_dir, engine)
        print(f"{len(written)} bodies in {out_dir}", file=sys.stderr)
    for line in report:
        print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
