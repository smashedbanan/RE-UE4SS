# UE4SS in the local Dragonwilds server container

Sub-project 1 of 2. The overall goal is UE4SS, then RuneSchema, running in the RuneScape:
Dragonwilds dedicated server container. This sub-project gets UE4SS working **locally**. Sub-project
2 ports RuneSchema's loaders, gets its own spec, and starts only after this one passes.

## Context

- The game image is `localhost/dragonwilds:latest`, built from
  `~/PROJECTS/repos/jj/POOP/stacks/runescape/Dragonwilds.Containerfile`:
  - Steam build 25630937 on Fedora 44, glibc 2.43.
  - The image runs as uid 65532, and `/srv/rs_server` is root-owned.
  - It ships `RSDragonwildsServer-Linux-Shipping.sym` next to the binary.
- The live server runs on another machine (cachy-srv). `~/Games/Dragonwilds/Saved` is a copy of its
  data.
- UE4SS uses the directory that holds `libUE4SS.so` as its working directory. That one directory
  holds the log, `UE4SS-settings.ini`, `UE4SS_Signatures/`, `VTableLayout.ini` and `Mods/`.
- The host has glibc 2.44. A library built on the host could require `GLIBC_2.44` symbols and then
  fail to load in the image, so the build runs in a Fedora 44 container.

## Scope

In scope:

- Build `libUE4SS.so` (`Game__Shipping__Linux`) from this checkout in a Fedora 44 builder container.
- Generate Dragonwilds' `UE4SS_Signatures/` and `VTableLayout.ini` from the binary and `.sym` in
  `localhost/dragonwilds:latest`.
- Run the **unchanged** image with UE4SS bind-mounted in and `LD_PRELOAD` set, against a fresh copy
  of `Saved` on every run.
- A Lua probe mod and a C++ probe mod, plus PartyHats' Lua script as a real mod.

Out of scope:

- Changes to the Containerfile or the `runescape.container` unit.
- Release packaging; it gets decided once this passes.
- Deploying to cachy-srv.
- RuneSchema.

Test runs use the live server's identity (`ServerGuid`, `ServerName`, `OwnerId`) unchanged, by the
user's choice. They publish no port, so no player can reach a test instance.

## Success criteria

All of the following hold on a single run:

1. The server reaches world-loaded. `UE4SS.log` shows engine 5.6, every signature resolved, and the
   default hooks installed.
2. The Lua probe logs `PROBE PASS` for each of `FindFirstOf`, `StaticFindObject`, `RegisterHook` on a
   native function, `RegisterHook` on a Blueprint function, `ExecuteInGameThread` and
   `NotifyOnNewObject`, and logs no `PROBE FAIL`.
3. The C++ probe, loaded from `Mods/ProbeCpp/dlls/main.so`, logs `PROBE PASS` for `start_mod` and
   for a `StaticFindObject` made from `on_unreal_init`.
4. PartyHats loads, reads its `config.txt` and runs its game-thread loop without Lua errors. It idles,
   because a dedicated server has no local player controller.
5. The server stays up through at least one autosave (`dom.StateSaveFrequencyMins` is 5), and the
   mtime of `SaveGames/*.sav` in the copy advances.

## Components

New files, in `tools/linux-test/` on branch `dragonwilds-container`:

| File | Purpose |
|---|---|
| `Containerfile.builder` | Fedora 44 with clang (19 or newer), cmake, ninja, rust/cargo, git, python3 |
| `CMakeLists.txt` | Outer project: `add_subdirectory(<repo root>)`, then a `ProbeCpp` shared library linked `PUBLIC UE4SS` |
| `ProbeCpp/main.cpp` | `CppUserModBase` mod; logs `PROBE PASS`/`FAIL` from `start_mod` and from a `StaticFindObject` in `on_unreal_init` |
| `ProbeLua/Scripts/main.lua` | One `PROBE PASS`/`FAIL` line per Lua API in success criterion 2 |
| `build.sh` | Builds the builder image, then runs it with the repo bind-mounted; writes `libUE4SS.so` and `main.so` to `/tmp/claude-scratch/ue4ss-build` |
| `run.sh` | Generates the layouts, assembles the `ue4ss/` directory, copies `Saved` fresh, runs the container |

The outer CMake project consumes UE4SS the way RuneSchema does: RuneSchema's CMake brings UE4SS in
with FetchContent and links `UE4SS`. One build therefore produces both libraries and proves the
out-of-tree C++ mod path that sub-project 2 depends on. `cppmods/` is not touched.

## Data flow of `run.sh`

1. **Layouts.** If no layouts exist yet for the current `localhost/dragonwilds:latest` image ID:
   - copy `RSDragonwildsServer-Linux-Shipping` and its `.sym` out of the image;
   - run `tools/linux-layouts/ue_signatures.py` and `ue_vtable_layout.py` (with
     `assets/VTableLayoutTemplates/VTableLayout_5_06_Template.ini`);
   - save the output under that image ID in `/tmp/claude-scratch/dw-test/layouts/`.
2. **Assemble `/tmp/claude-scratch/dw-test/ue4ss/`:**
   - `libUE4SS.so`;
   - `UE4SS-settings.ini`: `assets/UE4SS-settings.ini` unchanged. Its `ConsoleEnabled = 1` already
     sends UE4SS output to stdout;
   - `UE4SS_Signatures/` and `VTableLayout.ini` from step 1;
   - `Mods/`: `shared` from `assets/Mods/shared`, `ProbeLua`, `ProbeCpp/dlls/main.so`, and
     `PartyHats` from `~/Downloads/extract/runescape_mods_10-03/PartyHats.zip`, each enabled in
     `mods.txt`.
3. **Fresh save.** Delete `/tmp/claude-scratch/dw-test/Saved` and copy `~/Games/Dragonwilds/Saved`
   into it.
4. **Run:**

   ```
   podman run --rm --init --name dw-ue4ss-test \
     --userns keep-id:uid=65532,gid=65532 \
     -v /tmp/claude-scratch/dw-test/ue4ss:/srv/rs_server/RSDragonwilds/Binaries/Linux/ue4ss \
     -v /tmp/claude-scratch/dw-test/Saved:/srv/rs_server/RSDragonwilds/Saved \
     -e LD_PRELOAD=/srv/rs_server/RSDragonwilds/Binaries/Linux/ue4ss/libUE4SS.so \
     localhost/dragonwilds:latest <Exec= arguments>
   ```

   - The `Exec=` arguments are read from `stacks/runescape/runescape.container` at run time, so
     `t.MaxFPS=60` and the log filters match live. `RUNESCAPE_UNIT` overrides the unit's path.
   - There is no `-p`. The default rootless network still lets the server reach Epic.
   - `--init` stands in for the unit's `RunInit=true`.
   - `keep-id` maps container uid 65532 to the host user, so the bind mounts are writable.

`run.sh` only reads the unit file. It writes nothing outside `/tmp/claude-scratch/dw-test`.

## Reading results

| Output | Location on the host |
|---|---|
| UE4SS log | `/tmp/claude-scratch/dw-test/ue4ss/UE4SS.log` |
| Game log | `/tmp/claude-scratch/dw-test/Saved/Logs/RSDragonwilds.log` |
| Crash info | `/tmp/claude-scratch/dw-test/Saved/Crashes/` |
| Combined stdout | the terminal that ran `run.sh` |

The criteria are checked by grepping `PROBE` lines, signature and hook lines, and PartyHats'
`[PartyHats]` lines, and by reading the `.sav` mtime. There is no checker script.

## Failure handling

| Symptom | Where | Meaning, next step |
|---|---|---|
| `GLIBC_2.44 not found` or `undefined symbol` at load | stdout, first lines | builder or toolchain mismatch; fix the builder |
| A signature not found | `UE4SS.log` | `ue_signatures.py` does not cover this build; fix the script against the `.sym`, never hand-write addresses |
| Segfault after UE4SS init | `Saved/Crashes/`, end of `UE4SS.log` | likely a vtable or layout mismatch; turn the default hooks off one at a time in `UE4SS-settings.ini` to find it |
| A `PROBE FAIL` | `UE4SS.log` | API defect in the port; fix in `UE4SS/` as a commit on the branch |
| PartyHats Lua error | `UE4SS.log` | API gap the probe missed; reduce it to a new probe line, then fix |

Defects in the port are fixed at the root cause, in shared code, after a probe line reproduces them.

## Cost per iteration

- After a UE4SS change: an incremental `build.sh`, then `run.sh`.
- A full pass needs 6–7 minutes: server start plus one autosave.
- Probe-only iterations can stop once the `PROBE` lines are logged.

## After this passes

- Decide release packaging, with the earlier options on the table: a release asset with a pinned
  sha256 plus layouts generated at image build time, or a local `--build-context`.
- Start sub-project 2: RuneSchema, loaders only and headless. That means:
  - no ImGui, no Helpy, no visual features;
  - its Windows-only code ported;
  - its server check by Win64 executable name fixed;
  - its Windows PE byte-pattern hooks replaced or guarded;
  - its pak mounting made to work on Linux.

  PartyHats and SummoningPotions (items, recipes and paks) are its acceptance mods.
