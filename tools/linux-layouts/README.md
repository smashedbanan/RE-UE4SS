# Linux (Itanium) layouts for UEPseudo

UE4SS reads engine objects through layouts measured on MSVC builds: the vtable offset of each
virtual it calls or hooks, and the offset of each member it reads. A Linux build of the same
engine version (Clang, Itanium C++ ABI) differs in both:

- **vtables**: every root destructor takes two slots instead of one; MSVC groups overloads in
  reverse declaration order, Itanium keeps the declaration order; and Itanium also gives an
  override of a secondary base's virtual (`UPlayer::Exec`, from `FExec`) a slot in the primary
  vtable. No fixed shift converts one into the other (measured: 12 right, 346 wrong).
- **members**: Itanium reuses the tail padding of a non-POD base (`FProperty::ArrayDim` and
  `ElementSize` sit 4 bytes earlier than on MSVC), and platform types change size (`FRWLock` is
  8 bytes on Windows, 56 on Linux, which moves everything after it in `UClass` and `UWorld`).

These tools take both from the compiler instead of guessing. The output goes to
`deps/first/Unreal/generated_include_linux/FunctionBodies`, searched before `generated_include`
on non-Windows builds, so a version with Linux bodies uses them and any other version or class
keeps the MSVC body (vtable offsets shifted for the doubled destructor, see
`UnrealInitializer.cpp`).

## From the engine source (any stock build of a version, server or client)

`ue_vtable_from_source.py` and `ue_member_layout_from_source.py` compile one probe file with the
exact flags of a Linux target, taken from UnrealBuildTool's compile database, and read
`-fdump-vtable-layouts` / `-fdump-record-layouts`. They need the Unreal source of that version
(Epic's GitHub, linked account) and run in Docker:

```bash
# Once per version: sparse checkout of the source (code, build rules, config, plugin sources).
git clone --filter=blob:none --no-checkout --depth 1 --branch 5.6.1-release \
    https://github.com/EpicGames/UnrealEngine.git UE-5.6.1
# ...sparse-checkout /Engine/Source/ /Engine/Build/ /Engine/Config/ /Engine/Programs/
#    /Engine/Plugins/**/*.uplugin /Engine/Plugins/**/Source/ /Engine/Shaders/**/*.cs
#    /Engine/Shaders/Shared/   (minus ThirdParty *.a/*.lib/*.so/*.dll), then copy it into a volume.

docker build -t ue-layout -f Dockerfile.clang18 .          # UE 5.2+ (clang 18 for 5.6)
docker build -t ue-layout-13 -f Dockerfile.clang13 .       # UE 5.1 (clang 13)

# Builds UBT, fakes the in-tree SDK with the image's clang, writes compile_commands.json.
docker run --rm -v ue-561:/ue -v $PWD:/t ue-layout bash /t/ue-setup.sh 18 UnrealServer

docker run --rm -v ue-561:/ue -v $PWD:/t -v <RE-UE4SS>:/repo ue-layout bash -c '
  python3 /t/ue_vtable_from_source.py /ue UnrealServer /repo/assets/VTableLayoutTemplates/VTableLayout_5_06_Template.ini \
      --bodies /repo/deps/first/Unreal/generated_include/FunctionBodies 5_06 /out 5.6.1 > /out/VTableLayout.ini
  python3 /t/ue_member_layout_from_source.py /ue UnrealServer \
      /repo/deps/first/Unreal/generated_include/FunctionBodies 5_06 /out 5.6.1'
```

Notes from doing it:

- UBT checks the clang major against `Linux_SDK.json` (5.2+) or `LinuxPlatformSDK.Versions.cs`
  (5.1): 18 for 5.6, 13 for 5.1. The vtable and record layouts do not depend on it.
- `gitdeps_fetch.py` pulls only the files UBT needs from `Commit.gitdeps.xml`. Epic's CDN no longer
  serves 5.1's packs (HTTP 403): its UBT builds with the 5.6 copies of
  `UnrealEngine.csproj.props`/`.CSharp.targets`, `Ionic.Zip.Reduced.dll`, the Oodle SDK and ISPC,
  plus `Microsoft.VisualStudio.Setup.Configuration.Interop.dll` from NuGet; and the bundled
  libc++ headers are replaced by the image's libc++ of the same major.
- 5.1's `-Mode=GenerateClangDatabase` does not run UHT: run `UnrealServer Linux Shipping
  -SkipBuild` once to get the `*.generated.h`.
- After adding or changing a body, touch `src/VersionedContainer/UnrealVirtualImpl/UnrealVirtualNNN.cpp`:
  ninja does not see a new file shadowing a header of a later include directory.

## From one game's executable (a modified engine)

A licensee can change engine classes (RuneScape: Dragonwilds adds virtuals to `AActor`), and then
only that game's own vtables are right. Servers built with Unreal's crash reporter ship a `.sym`
next to the executable; `ue_vtable_layout.py` finds each vtable in the executable (the pair of
destructor pointers after two null words, since RTTI is off), names every slot from the `.sym`
(tolerating identical code folding) and writes a `VTableLayout.ini` for that game. UE4SS reads it
from its folder, and it wins over the generated bodies.

```bash
python3 ue_vtable_layout.py <executable> VTableLayout_5_06_Template.ini > VTableLayout.ini
```

The same build can hide engine functions from patternsleuth's AOB scans (Dragonwilds:
`FName::ToString`, `FName::FName`, `StaticConstructObject_Internal`), and UE4SS then never starts.
`ue_signatures_from_sym.py` takes each one's entry from the `.sym` and writes a `UE4SS_Signatures/`
file that returns it. It also writes `GNatives.lua` (decoded from `FFrame::Step`), an optional value
the scans miss, which UE4SS otherwise runs without. A value that fails its checks is reported and its
file deleted, so that UE4SS scans for that value itself; the other files are written, and the script
exits 1. The addresses belong to that build: regenerate both files after every game update. It needs
Python 3.10 or newer.

```bash
python3 ue_signatures_from_sym.py <executable> UE4SS_Signatures
```

## Validated

- UE 5.6.1 source vs. Dragonwilds' measured vtables: every `UObject`, `UEngine`,
  `UGameViewportClient`, `FOutputDevice`, `FMalloc` and `UPlayer` slot matches; `AActor` differs
  only by the virtuals Jagex added.
- UE 5.1.1 source on Palworld (no `.sym`): all vtable hooks install, Lua runs `FindFirstOf`,
  `StaticFindObject`, Blueprint and native `RegisterHook`, `ExecuteInGameThread`. The member
  layouts fixed the native-hook crash (`FProperty::ElementSize`). Hooks on functions its
  animation Blueprints call from worker threads (KismetMathLibrary) also hold since the Lua
  RegisterHook callbacks take `LuaMod::m_thread_actions_mutex` (thousands of calls, 10 minutes,
  game thread still ticking).

## From a game's DWARF (no engine source needed)

A server that ships its `.debug` (Squad 44, MORDHAU, VEIN, QANGA, Citadel) carries every class
layout of its build. `ue_layout_from_dwarf.py` reads it with `llvm-dwarfdump` (one lookup per level
of the class hierarchy, streaming: a class used everywhere is defined once per compilation unit) and
writes the same bodies as the two source tools:

```bash
docker run --rm -v <game>:/g:ro -v $PWD:/t ue-layout bash -c 'cd /t && python3 ue_layout_from_dwarf.py \
    /g/<Server>.debug /repo/assets/VTableLayoutTemplates/VTableLayout_4_27_Template.ini \
    --bodies /repo/deps/first/Unreal/generated_include/FunctionBodies 4_27 /tmp/out "the DWARF of <game>"'
```

The game is only the measuring device: if its studio changed an engine class, so does the layout.
`--slots-json` also writes the absolute slot of every name, which the reference pack uses.

## Reference packs (games without symbols)

`ue_reference_pack.py <executable> <template> <version> <label>` turns one game with `.sym` and
`.debug` into the pack of its engine version (see docs/linux.md, "Why a game needs its own files").
Packs built so far: 4.26 (MORDHAU), 4.27 (Squad 44), 5.6 (VEIN), 5.7 (QANGA). They are the
`LinuxReferencePacks.tar.gz` of the release; the game panel's `ue_linux_layout.py` applies them.
