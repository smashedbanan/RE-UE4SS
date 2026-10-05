# UE4SS in the Local Dragonwilds Container: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the unchanged `localhost/dragonwilds:latest` image with this repo's `libUE4SS.so`
preloaded, and prove UE4SS works there with a Lua probe, a C++ probe, PartyHats and one autosave.

**Architecture:**
- A Fedora 44 builder container builds `libUE4SS.so` and a C++ probe mod. Fedora 44 is the game
  image's own base, so glibc matches (2.43).
- The probe comes from an outer CMake project that `add_subdirectory`'s this repo, which is the way
  RuneSchema consumes UE4SS.
- `run.sh`:
  1. generates the build-specific signatures and vtable layout from the image's `.sym`;
  2. assembles a `ue4ss/` directory;
  3. copies `Saved` fresh;
  4. runs the image with `ue4ss/` bind-mounted and `LD_PRELOAD` set.

**Tech Stack:** Podman (rootless), Fedora 44 minimal, clang/lld, CMake + Ninja, Rust (Corrosion),
UE4SS C++ and Lua APIs, Python 3 (stdlib only) for `tools/linux-layouts`.

**Spec:** `docs/superpowers/specs/2026-10-05-ue4ss-dragonwilds-container-design.md`

## Global Constraints

- Branch: `dragonwilds-container`. Each commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Builder base: `quay.io/fedora/fedora-minimal:44-x86_64`, the same image the game's Containerfile uses (glibc 2.43).
- Clang 19 or newer. Build type: `Game__Shipping__Linux`.
- Build output goes to `/tmp/claude-scratch/ue4ss-build`, test runs to `/tmp/claude-scratch/dw-test`. `UE4SS_TEST_DIR` overrides the `/tmp/claude-scratch` base.
- Never write to `~/Games/Dragonwilds/Saved`, never modify `~/PROJECTS/repos/jj/POOP`, and never rebuild or retag `localhost/dragonwilds`. `run.sh` only **reads** `runescape.container`.
- Test runs publish no port and use the container name `dw-ue4ss-test`. The server identity stays unchanged (the user's choice).
- Do not touch `cppmods/`, and do not change Windows behavior.
- Defects in the port are fixed at the root cause, after a probe line reproduces them. Use superpowers:systematic-debugging.

## Facts the tasks rely on

- Game binary in the image: `/srv/rs_server/RSDragonwilds/Binaries/Linux/RSDragonwildsServer-Linux-Shipping`, with `RSDragonwildsServer-Linux-Shipping.sym` next to it. It is non-PIE.
- `tools/linux-layouts/ue_signatures.py <exe> <out dir>` writes `UE4SS_Signatures/*.lua`, creating the directory. `ue_vtable_layout.py <exe> <template> > VTableLayout.ini` writes the layout. Both read `<exe>.sym` and exit non-zero on failure.
- UE4SS's working directory is the directory `libUE4SS.so` was loaded from: log, settings, signatures, `VTableLayout.ini` and `Mods/` all live there. `assets/UE4SS-settings.ini` already has `ConsoleEnabled = 1`, so UE4SS output also reaches stdout.
- A C++ mod is `Mods/<Mod>/dlls/main.so`, exporting `start_mod` and `uninstall_mod`. Clang ignores `__declspec(dllexport)` on Linux; use `__attribute__((visibility("default")))`.
- Top-level `CMakeLists.txt` lines 15–30 set the Linux options at **directory** scope: the force-included `cmake/platform/LinuxMsvcCrtCompat.hpp`, the `RC_*_API` visibility defines and `-fvisibility-ms-compat`. An outer project's targets do not get them. UE4SS headers use `sprintf_s` (`deps/first/Helpers/include/Helpers/Format.hpp`, `deps/first/MProgram/include/ErrorObject.hpp`).
- Libraries land in `${CMAKE_BINARY_DIR}/<CONFIG>/${CMAKE_INSTALL_LIBDIR}`. `build.sh` pins `CMAKE_INSTALL_LIBDIR=lib`, because Fedora's default is `lib64`.
- Game log milestones (`Saved/Logs/RSDragonwilds.log`):
  - world loaded: `World load SUCCEEDED (slot: POOPoverworld)`
  - autosave: `Save completed SUCCESSFULLY (slot: POOPoverworld)`, every 5 minutes
  - the server loads `/Game/Maps/Server/L_ServerStartup` first, then `/Game/Maps/World/L_World`
- UE4SS prints `Using engine version: {}.{}` once the engine version is known.

---

### Task 1: Fedora 44 builder produces libUE4SS.so

**Files:**
- Create: `tools/linux-test/Containerfile.builder`
- Create: `tools/linux-test/CMakeLists.txt`
- Create: `tools/linux-test/build.sh`

**Interfaces:**
- Produces: `build.sh` (no arguments) → `/tmp/claude-scratch/ue4ss-build/build/Game__Shipping__Linux/lib/libUE4SS.so`. Image `localhost/ue4ss-builder:f44`. CMake project `UE4SSLinuxTest` with UE4SS added as binary subdir `ue4ss`.

- [ ] **Step 1: Write the check that fails now**

Run:
```bash
f=/tmp/claude-scratch/ue4ss-build/build/Game__Shipping__Linux/lib/libUE4SS.so
test -f "$f" && echo PRESENT || echo MISSING
```
Expected: `MISSING`.

- [ ] **Step 2: Create `tools/linux-test/Containerfile.builder`**

```dockerfile
# Toolchain for a Linux UE4SS build that loads inside the RuneScape: Dragonwilds server image. Same
# base as that image (Fedora 44, glibc 2.43): a library built against a newer glibc, such as the
# host's, can need symbols the game's image does not have and then fails to load.
# gcc-c++ and libstdc++-static: clang uses GCC's libstdc++ and libgcc, and UE4SS links both
# statically (UE4SS/CMakeLists.txt, -static-libstdc++ -static-libgcc).
FROM quay.io/fedora/fedora-minimal:44-x86_64
RUN microdnf -y install --setopt=install_weak_deps=0 --nodocs \
      clang lld cmake ninja-build gcc-c++ libstdc++-static rust cargo git-core python3 \
 && microdnf clean all
```

- [ ] **Step 3: Create `tools/linux-test/CMakeLists.txt`**

```cmake
# Builds UE4SS the way a C++ mod outside this repository consumes it: RuneSchema brings UE4SS in
# with FetchContent, which is add_subdirectory underneath. Run by build.sh.
cmake_minimum_required(VERSION 3.22)
project(UE4SSLinuxTest CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../.." ue4ss)
```

- [ ] **Step 4: Create `tools/linux-test/build.sh`** and make it executable (`chmod +x`)

```bash
#!/usr/bin/env bash
# Builds libUE4SS.so (and the probe mods of CMakeLists.txt) for a Linux game server inside the
# Fedora 44 builder (Containerfile.builder). Output: $UE4SS_TEST_DIR/ue4ss-build/build.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
out=${UE4SS_TEST_DIR:-/tmp/claude-scratch}/ue4ss-build
mkdir -p "$out/cargo"

podman build -t localhost/ue4ss-builder:f44 -f "$here/Containerfile.builder" "$here"
podman run --rm -v "$repo:/src" -v "$out:/out" -e CARGO_HOME=/out/cargo \
  localhost/ue4ss-builder:f44 bash -c '
    set -e
    cmake -S /src/tools/linux-test -B /out/build -G Ninja \
      -DCMAKE_BUILD_TYPE=Game__Shipping__Linux \
      -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_LINKER_TYPE=LLD \
      -DCMAKE_INSTALL_LIBDIR=lib
    cmake --build /out/build'
```

- [ ] **Step 5: Run the build**

Run `tools/linux-test/build.sh` from the repo root, using `run_in_background`. The first build compiles Rust and C++ with LTO and can take 10–30 minutes.

Expected: it ends with ninja's last `Linking CXX shared library .../libUE4SS.so` line and exit code 0.

If it fails:
- **A cargo build script reports a missing system tool** (for example `perl`): add that package to the `microdnf` line and rerun.
- **The LTO link fails under lld:** read the error before changing anything. Report it rather than turning LTO off.

- [ ] **Step 6: Verify the library**

Run:
```bash
f=/tmp/claude-scratch/ue4ss-build/build/Game__Shipping__Linux/lib/libUE4SS.so
test -f "$f" && echo PRESENT
objdump -T "$f" | grep -o 'GLIBC_2\.[0-9.]*' | sort -uV | tail -1
readelf -d "$f" | grep NEEDED
nm -D --defined-only "$f" | grep -c ' T '
git status --short
```
Expected:
- `PRESENT`
- the highest glibc version is **≤ `GLIBC_2.43`**
- `NEEDED` lists no `libstdc++.so` and no `libgcc_s.so`
- the exported `T` count is in the thousands (docs record 4728)
- `git status` shows only the three new `tools/linux-test/` files, so the build wrote nothing into the source tree

- [ ] **Step 7: Commit**

```bash
git add tools/linux-test/Containerfile.builder tools/linux-test/CMakeLists.txt tools/linux-test/build.sh
git commit -F - <<'EOF'
tools/linux-test: build libUE4SS.so in a Fedora 44 container

The RuneScape: Dragonwilds server image is Fedora 44 (glibc 2.43); a host build against a newer
glibc can need symbols that image lacks. The outer CMake project consumes UE4SS through
add_subdirectory, the way an out-of-tree C++ mod (RuneSchema) does.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: An out-of-tree C++ mod builds against UE4SS on Linux

**Files:**
- Create: `tools/linux-test/ProbeCpp/main.cpp`
- Modify: `tools/linux-test/CMakeLists.txt` (append the `ProbeCpp` target)
- Modify (only if Step 3 fails as predicted): `UE4SS/CMakeLists.txt`, in the `else()` branch that ends with `target_sources(UE4SS INTERFACE ".../src/unwinder_linux.cpp")` (currently line 193)

**Interfaces:**
- Consumes: `build.sh` and the outer project from Task 1.
- Produces: `/tmp/claude-scratch/ue4ss-build/build/probe/main.so`, exporting `start_mod` and `uninstall_mod`. It logs these exact lines:
  - `PROBE PASS cpp:start_mod`
  - `PROBE PASS cpp:StaticFindObject <full name>` or `PROBE FAIL cpp:StaticFindObject returned null`

- [ ] **Step 1: Create `tools/linux-test/ProbeCpp/main.cpp`**

```cpp
// ProbeCpp: the smallest C++ mod that exercises what RuneSchema needs from UE4SS on Linux - being
// loaded as Mods/ProbeCpp/dlls/main.so, logging, and finding an object once Unreal is initialized.
// Prints "PROBE PASS cpp:<check>" / "PROBE FAIL cpp:<check>" lines (tools/linux-test/run.sh).
#include <DynamicOutput/DynamicOutput.hpp>
#include <Mod/CppUserModBase.hpp>
#include <Unreal/UClass.hpp>
#include <Unreal/UObjectGlobals.hpp>

using namespace RC;
using namespace RC::Unreal;

class ProbeCpp : public CppUserModBase
{
  public:
    ProbeCpp()
    {
        ModName = STR("ProbeCpp");
        ModVersion = STR("1.0");
        ModDescription = STR("Linux C++ mod probe");
        ModAuthors = STR("tools/linux-test");
        Output::send<LogLevel::Normal>(STR("[ProbeCpp] PROBE PASS cpp:start_mod\n"));
    }

    auto on_unreal_init() -> void override
    {
        auto* actor_class = UObjectGlobals::StaticFindObject<UClass*>(nullptr, nullptr, STR("/Script/Engine.Actor"));
        if (actor_class)
        {
            Output::send<LogLevel::Normal>(STR("[ProbeCpp] PROBE PASS cpp:StaticFindObject {}\n"), actor_class->GetFullName());
        }
        else
        {
            Output::send<LogLevel::Error>(STR("[ProbeCpp] PROBE FAIL cpp:StaticFindObject returned null\n"));
        }
    }
};

// Clang ignores __declspec(dllexport) on Linux: default visibility is what exports these.
#define PROBE_API __attribute__((visibility("default")))

extern "C"
{
    PROBE_API CppUserModBase* start_mod()
    {
        return new ProbeCpp();
    }

    PROBE_API void uninstall_mod(CppUserModBase* mod)
    {
        delete mod;
    }
}
```

- [ ] **Step 2: Append the target to `tools/linux-test/CMakeLists.txt`**

```cmake

# Mods/ProbeCpp/dlls/main.so for run.sh.
add_library(ProbeCpp SHARED ProbeCpp/main.cpp)
target_link_libraries(ProbeCpp PUBLIC UE4SS)
set_target_properties(ProbeCpp PROPERTIES
    OUTPUT_NAME main
    PREFIX ""
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/probe")
```

- [ ] **Step 3: Build, expecting a failure**

Run `tools/linux-test/build.sh`.

Expected: `ProbeCpp/main.cpp` **fails to compile** with an error such as `use of undeclared identifier 'sprintf_s'` (or `printf_s`), coming from `Helpers/Format.hpp` or `ErrorObject.hpp`. The cause: the secure-CRT shim is force-included at the top-level directory's scope only, so an out-of-tree consumer never gets it.

If it compiles cleanly instead, skip Step 4 and note in the Task 2 commit message that the prediction was wrong.

- [ ] **Step 4: Fix: give the shim to every consumer of the UE4SS target**

In `UE4SS/CMakeLists.txt`, directly after
`target_sources(UE4SS INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/src/unwinder_linux.cpp")`, insert:

```cmake
    # A mod built outside this tree (FetchContent or add_subdirectory, as RuneSchema does) is not under
    # the top-level CMakeLists.txt's directory options, and UE4SS's headers call the secure-CRT names
    # (sprintf_s in Helpers/Format.hpp): it gets the shim from here. In-tree targets get it twice,
    # which #pragma once absorbs.
    target_compile_options(UE4SS INTERFACE
        "$<$<COMPILE_LANGUAGE:CXX>:SHELL:-include ${CMAKE_CURRENT_SOURCE_DIR}/../cmake/platform/LinuxMsvcCrtCompat.hpp>")
```

Rerun `tools/linux-test/build.sh`.

If the build then fails on `__declspec` (for example `'__declspec' attributes are not enabled`), the `RC_*_API` defines are missing too. Add this directly below the block above, then rerun:

```cmake
    # The same for the RC_*_API meaning (default visibility) the top-level CMakeLists.txt defines.
    foreach(api RC_UE4SS_API RC_UE_API RC_ASM_API RC_DYNOUT_API RC_FILE_API RC_HELPERS_API
                RC_INI_PARSER_API RC_INPUT_API RC_JSON_API RC_LMS_API RC_PB_API RC_SPSS_API)
        target_compile_definitions(UE4SS INTERFACE "${api}=__attribute__((visibility(\"default\")))")
    endforeach()
```

Expected: the build exits 0.

- [ ] **Step 5: Verify the mod's exports and imports**

Run:
```bash
b=/tmp/claude-scratch/ue4ss-build/build
nm -D --defined-only $b/probe/main.so | grep -E ' T (start_mod|uninstall_mod)$'
comm -23 \
  <(nm -D --undefined-only $b/probe/main.so | awk '$1=="U"{print $2}' | grep -v '@' | sort -u) \
  <(nm -D --defined-only $b/Game__Shipping__Linux/lib/libUE4SS.so | awk '{print $3}' | sort -u)
objdump -T $b/probe/main.so | grep -o 'GLIBC_2\.[0-9.]*' | sort -uV | tail -1
```
Expected:
- both `start_mod` and `uninstall_mod` are listed
- the `comm` output is **empty**: every unversioned import resolves to a `libUE4SS.so` export
- the highest glibc version is ≤ `GLIBC_2.43`

- [ ] **Step 6: Confirm the Windows path is untouched**

Run `git diff UE4SS/CMakeLists.txt`.

Expected: the only change sits inside the non-`WIN32` `else()` branch.

- [ ] **Step 7: Commit**

```bash
git add tools/linux-test/ProbeCpp/main.cpp tools/linux-test/CMakeLists.txt UE4SS/CMakeLists.txt
git commit -F - <<'EOF'
Linux: a C++ mod built outside this tree gets the secure-CRT shim

The shim header (printf_s, sprintf_s over glibc) was force-included at the top-level directory's
scope, which an out-of-tree consumer - FetchContent or add_subdirectory, the way RuneSchema
builds - never inherits, and UE4SS's headers use sprintf_s. The UE4SS target now carries it as an
INTERFACE compile option. tools/linux-test/ProbeCpp is the out-of-tree mod that showed it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

If Step 4 also added the `RC_*_API` block, add one sentence about it to the message. If Step 4 was skipped, use the subject `tools/linux-test: out-of-tree C++ probe mod` and describe only the probe.

---

### Task 3: First boot: UE4SS initializes inside the game container

**Files:**
- Create: `tools/linux-test/ProbeLua/Scripts/main.lua`
- Create: `tools/linux-test/run.sh`

**Interfaces:**
- Consumes: `libUE4SS.so` (Task 1) and `probe/main.so` (Task 2), both under `/tmp/claude-scratch/ue4ss-build/build`.
- Produces: `run.sh` (no arguments; runs in the foreground). Environment overrides: `UE4SS_TEST_DIR`, `DW_IMAGE`, `DW_SAVED`, `RUNESCAPE_UNIT`, `PARTYHATS_ZIP`. Outputs:
  - `/tmp/claude-scratch/dw-test/ue4ss/UE4SS.log`
  - `/tmp/claude-scratch/dw-test/Saved/Logs/RSDragonwilds.log`
  - `/tmp/claude-scratch/dw-test/layouts/<image id>/`
- ProbeLua prints `[ProbeLua] PROBE PASS lua:<check>` or `[ProbeLua] PROBE FAIL lua:<check>` for these checks: `StaticFindObject`, `ExecuteInGameThread`, `NotifyOnNewObject`, `FindFirstOf`, `RegisterHook.native`, `RegisterHook.blueprint`. At the end it prints `[ProbeLua] PROBE DONE pass=<n> fail=<n>`.

- [ ] **Step 1: Create `tools/linux-test/ProbeLua/Scripts/main.lua`**

```lua
-- ProbeLua: one "PROBE PASS lua:<check>" / "PROBE FAIL lua:<check>" line per Lua API the Linux port
-- has to support (tools/linux-test/run.sh). Built for a dedicated server: it needs no player.
--   at load:          StaticFindObject, ExecuteInGameThread, NotifyOnNewObject
--   once L_World is up: FindFirstOf (its GameState), RegisterHook on a native function (hooked, then
--                     called directly), RegisterHook on Blueprint functions (the event graphs and
--                     ticks of live Blueprint classes, which run on their own)
local TAG = "[ProbeLua] "
local WORLD = "/Game/Maps/World/L_World.L_World:PersistentLevel."
local BLUEPRINT_DEADLINE = 300 -- seconds after L_World is up for a hooked Blueprint function to run
local MAX_BLUEPRINT_HOOKS = 50

local results = {}
local function report(name, ok, detail)
    if results[name] ~= nil then return end
    results[name] = ok
    print(string.format("%sPROBE %s lua:%s%s\n", TAG, ok and "PASS" or "FAIL", name, detail and (" " .. detail) or ""))
end

-- At load ------------------------------------------------------------------------------------------
local actorClass = StaticFindObject("/Script/Engine.Actor")
if actorClass and actorClass:IsValid() then
    report("StaticFindObject", true, actorClass:GetFullName())
else
    report("StaticFindObject", false, "/Script/Engine.Actor not found")
end

ExecuteInGameThread(function() report("ExecuteInGameThread", true) end)

NotifyOnNewObject("/Script/Engine.Actor", function(obj)
    report("NotifyOnNewObject", true, obj:GetFullName())
    return true
end)

-- Once L_World is up -------------------------------------------------------------------------------
local function nativeCheck(gameState)
    local path = "/Script/Engine.Actor:K2_GetActorLocation"
    local pre, post = RegisterHook(path, function() report("RegisterHook.native", true, path) end)
    gameState:K2_GetActorLocation() -- on the game thread, through ProcessEvent: the hook runs now
    pcall(UnregisterHook, path, pre, post)
    if results["RegisterHook.native"] == nil then
        report("RegisterHook.native", false, "hook did not run on a direct call of " .. path)
    end
end

local blueprintHooks = {}
local function hookBlueprints()
    local seen = {}
    local function hookClassOf(object)
        if #blueprintHooks >= MAX_BLUEPRINT_HOOKS then return end
        local class = object:GetClass()
        local className = class:GetFullName()
        if seen[className] or not className:find("^BlueprintGeneratedClass ") then return end
        seen[className] = true
        class:ForEachFunction(function(fn)
            local name = fn:GetFName():ToString()
            if name == "ReceiveTick" or name:find("^ExecuteUbergraph") then
                local path = fn:GetFullName()
                local ok, pre, post = pcall(RegisterHook, path, function()
                    report("RegisterHook.blueprint", true, path)
                end)
                if ok then table.insert(blueprintHooks, { path = path, pre = pre, post = post }) end
            end
            return #blueprintHooks >= MAX_BLUEPRINT_HOOKS
        end)
    end
    for _, className in ipairs({ "Actor", "ActorComponent" }) do
        for _, object in ipairs(FindAllOf(className) or {}) do hookClassOf(object) end
    end
    return #blueprintHooks
end

local function unhookBlueprints()
    for _, h in ipairs(blueprintHooks) do pcall(UnregisterHook, h.path, h.pre, h.post) end
    blueprintHooks = {}
end

local startedAt, worldAt, done = os.time(), nil, false
local loop
loop = LoopInGameThreadWithDelay(5000, function()
    if done then return end
    if worldAt == nil then
        local gameState = FindFirstOf("GameStateBase")
        if gameState:IsValid() and gameState:GetFullName():find(WORLD, 1, true) then
            worldAt = os.time()
            report("FindFirstOf", true, gameState:GetFullName())
            nativeCheck(gameState)
            local hooked = hookBlueprints()
            print(string.format("%shooked %d Blueprint functions\n", TAG, hooked))
            if hooked == 0 then
                report("RegisterHook.blueprint", false, "no ReceiveTick/ExecuteUbergraph in any live Blueprint class")
            end
        elseif os.time() - startedAt > 900 then
            report("FindFirstOf", false, "no GameStateBase in L_World after 900 s")
            worldAt = os.time()
        end
        return
    end
    if results["RegisterHook.blueprint"] == nil and os.time() - worldAt <= BLUEPRINT_DEADLINE then return end
    if results["RegisterHook.blueprint"] == nil then
        report("RegisterHook.blueprint", false, "no hooked Blueprint function ran in " .. BLUEPRINT_DEADLINE .. " s")
    end
    unhookBlueprints()
    for _, name in ipairs({ "StaticFindObject", "ExecuteInGameThread", "NotifyOnNewObject", "FindFirstOf", "RegisterHook.native" }) do
        if results[name] == nil then report(name, false, "never ran") end
    end
    local pass, fail = 0, 0
    for _, ok in pairs(results) do if ok then pass = pass + 1 else fail = fail + 1 end end
    print(string.format("%sPROBE DONE pass=%d fail=%d\n", TAG, pass, fail))
    done = true
    CancelDelayedAction(loop)
end)
```

- [ ] **Step 2: Create `tools/linux-test/run.sh`** and make it executable (`chmod +x`)

```bash
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

# 1. Signatures and vtable layout of this image's game build. They are absolute addresses of that
#    build (non-PIE), so they are keyed by image id and never reused across builds.
id=$(podman image inspect --format '{{.Id}}' "$image")
layouts=$dw/layouts/$id
if [ ! -f "$layouts/VTableLayout.ini" ]; then
  tmp=$dw/layouts/tmp
  rm -rf "$tmp" && mkdir -p "$tmp/out"
  c=$(podman create "$image")
  podman cp "$c:$bin/$game" "$tmp/"
  podman cp "$c:$bin/$game.sym" "$tmp/"
  podman rm "$c" >/dev/null
  python3 "$repo/tools/linux-layouts/ue_signatures.py" "$tmp/$game" "$tmp/out/UE4SS_Signatures"
  python3 "$repo/tools/linux-layouts/ue_vtable_layout.py" "$tmp/$game" \
    "$repo/assets/VTableLayoutTemplates/VTableLayout_5_06_Template.ini" > "$tmp/out/VTableLayout.ini"
  rm -rf "$layouts" && mv "$tmp/out" "$layouts" && rm -rf "$tmp"
fi

# 2. The ue4ss directory: UE4SS's working directory (log, settings, layouts, Mods).
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
exec podman run --rm --replace --init --name dw-ue4ss-test \
  --userns keep-id:uid=65532,gid=65532 --security-opt no-new-privileges \
  -v "$u:$bin/ue4ss" -v "$dw/Saved:/srv/rs_server/RSDragonwilds/Saved" \
  -e LD_PRELOAD="$bin/ue4ss/libUE4SS.so" \
  "$image" "${args[@]}"
```

- [ ] **Step 3: Start the server**

Run with `run_in_background`:
```bash
mkdir -p /tmp/claude-scratch/dw-test && tools/linux-test/run.sh 2>&1 | tee /tmp/claude-scratch/dw-test/stdout.log
```

- [ ] **Step 4: Check that the layouts were generated**

About 30 seconds after the start, run:
```bash
ls /tmp/claude-scratch/dw-test/layouts/*/UE4SS_Signatures/
wc -l /tmp/claude-scratch/dw-test/layouts/*/VTableLayout.ini
```
Expected: `.lua` files including `FName_ToString.lua`, `FName_Constructor.lua` and `StaticConstructObject.lua`, and a non-empty `VTableLayout.ini`.

If either script exited non-zero, `run.sh` stopped before starting the container. Debug the script against the `.sym` and never hand-write addresses. Fixes to `tools/linux-layouts/*.py` are commits of their own.

- [ ] **Step 5: Check UE4SS's startup**

About 2 minutes after the start, run:
```bash
d=/tmp/claude-scratch/dw-test
head -5 $d/stdout.log
grep -m1 'Using engine version' $d/ue4ss/UE4SS.log
grep -n -i -e 'fatal' -e 'not found' -e 'failed' -e 'could not' $d/ue4ss/UE4SS.log
grep -h 'PROBE' $d/ue4ss/UE4SS.log
grep -m1 'World load SUCCEEDED' $d/Saved/Logs/RSDragonwilds.log
podman ps --filter name=dw-ue4ss-test --format '{{.Status}}'
```
Expected:
- no `GLIBC_` or `undefined symbol` errors in the first lines of stdout
- `Using engine version: 5.6`
- no fatal, not-found, failed or could-not lines that concern signatures, hooks or mod loading; investigate any that do
- `PROBE PASS cpp:start_mod`, `PROBE PASS cpp:StaticFindObject ...`, and `PROBE PASS lua:StaticFindObject`, `lua:ExecuteInGameThread` and `lua:NotifyOnNewObject`
- the world-loaded line is present
- the container is `Up`

On a failure, follow the spec's failure table:
- **Load error:** fix the builder (Task 1).
- **Segfault after UE4SS init:** read `$d/Saved/Crashes/` and the end of `UE4SS.log`. Then turn the `[Hooks]` entries in `$d/ue4ss/UE4SS-settings.ini` to 0 one at a time and rerun to find the hook. That edit is a diagnosis only; the fix goes in `UE4SS/`.
- **`PROBE FAIL`:** an API defect. Use superpowers:systematic-debugging, fix it in `UE4SS/`, rerun `build.sh`, then rerun this task from Step 3.

- [ ] **Step 6: Stop the server**

```bash
podman stop dw-ue4ss-test
```

- [ ] **Step 7: Commit**

```bash
git add tools/linux-test/ProbeLua/Scripts/main.lua tools/linux-test/run.sh
git commit -F - <<'EOF'
tools/linux-test: run the Dragonwilds image with UE4SS preloaded

run.sh generates the build's UE4SS_Signatures and VTableLayout.ini from the image's .sym (keyed by
image id: they are absolute addresses), assembles the ue4ss directory with the Lua and C++ probes
and PartyHats, copies Saved fresh, and runs the unchanged image with the live unit's Exec=
arguments, no published port, and libUE4SS.so in LD_PRELOAD. ProbeLua prints one PROBE line per
Lua API, without needing a player.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

Commit any fixes made during Step 5 separately, before this commit, with messages that describe the defect.

---

### Task 4: Acceptance run: every success criterion on one run

**Files:**
- Modify: only what a defect found here requires (`UE4SS/`, `deps/first/`, `tools/linux-layouts/`), one commit per defect.

**Interfaces:**
- Consumes: `build.sh` and `run.sh` from Tasks 1–3, and ProbeLua's line format.

- [ ] **Step 1: Record the starting save time**

Run:
```bash
stat -c '%Y %y' ~/Games/Dragonwilds/Saved/SaveGames/POOPoverworld.sav
```
`cp -a` keeps this mtime in the copy. Note the value.

- [ ] **Step 2: Start a fresh run**

Run with `run_in_background`:
```bash
mkdir -p /tmp/claude-scratch/dw-test && tools/linux-test/run.sh 2>&1 | tee /tmp/claude-scratch/dw-test/stdout.log
```

- [ ] **Step 3: After about 7 minutes, check every criterion**

Run:
```bash
d=/tmp/claude-scratch/dw-test
grep -m1 'Using engine version' $d/ue4ss/UE4SS.log                  # criterion 1
grep -m1 'World load SUCCEEDED' $d/Saved/Logs/RSDragonwilds.log     # criterion 1
grep -h 'PROBE' $d/ue4ss/UE4SS.log                                  # criteria 2 and 3
grep -h '\[PartyHats\]' $d/ue4ss/UE4SS.log                          # criterion 4
grep -n -i -e 'lua error' -e 'stack traceback' -e 'attempt to' $d/ue4ss/UE4SS.log   # criterion 4
grep -c 'Save completed SUCCESSFULLY' $d/Saved/Logs/RSDragonwilds.log   # criterion 5
stat -c '%Y %y' $d/Saved/SaveGames/POOPoverworld.sav                 # criterion 5
podman ps --filter name=dw-ue4ss-test --format '{{.Status}}'
```

Expected:
- **Criterion 1:** `Using engine version: 5.6`, and the world-loaded line.
- **Criteria 2 and 3:** `PASS` lines for `lua:StaticFindObject`, `lua:ExecuteInGameThread`, `lua:NotifyOnNewObject`, `lua:FindFirstOf`, `lua:RegisterHook.native`, `lua:RegisterHook.blueprint`, `cpp:start_mod` and `cpp:StaticFindObject`. No `PROBE FAIL`. `PROBE DONE pass=6 fail=0` (it can take up to `BLUEPRINT_DEADLINE`, 300 s, after L_World).
- **Criterion 4:** no Lua error lines. PartyHats prints only when `Verbose` is set or a hat asset is missing, so silence from `[PartyHats]` is fine as long as there's no error. `UE4SS.log` must list PartyHats among the started mods; check with `grep -n PartyHats $d/ue4ss/UE4SS.log`.
- **Criterion 5:** the save count is at least 1, and the `.sav` mtime is newer than the value from Step 1.
- The container is still `Up`.

- [ ] **Step 4: Handle failures**

For each failed criterion, follow superpowers:systematic-debugging:

| Failure | Meaning | Next step |
|---|---|---|
| `lua:RegisterHook.blueprint` FAIL with "no hooked Blueprint function ran" while `hooked N` > 0 | **inconclusive**, not a defect: none of the hooked classes ran in 300 s | Raise `BLUEPRINT_DEADLINE` to 900, rerun, and only then treat it as a defect |
| `lua:RegisterHook.blueprint` FAIL with `hooked 0` | the probe found no Blueprint classes | Print the class names `FindAllOf("Actor")` returns, then fix the probe or the API |
| `lua:RegisterHook.native` FAIL | native hook defect | Defect in the port's UFunction hook path; fix in `UE4SS/` |
| A PartyHats Lua error | an API gap the probe missed | Reduce it to a new line in `ProbeLua`, then fix |
| Crash or exit before the autosave | crash | Read `$d/Saved/Crashes/` and the end of `UE4SS.log` |

After any fix: `podman stop dw-ue4ss-test`, `tools/linux-test/build.sh`, then repeat from Step 2. Commit each fix on its own, with a message that describes the defect and the probe line that showed it.

- [ ] **Step 5: Stop the server**

```bash
podman stop dw-ue4ss-test
```

- [ ] **Step 6: Report**

Report to the user, with the actual log lines as evidence:
- each criterion and its result
- the `PROBE DONE` line
- the save count and the mtime advance
- every defect fixed, with its commit

Leave `/tmp/claude-scratch/ue4ss-build` and `/tmp/claude-scratch/dw-test` in place, because sub-project 2 (RuneSchema) reuses the build and the run harness. Then the next steps: decide release packaging, and brainstorm sub-project 2.
