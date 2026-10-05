# UE4SS on Linux (this fork)

This fork (`linux` branch) builds and runs the official UE4SS inside **native Linux Unreal Engine
games** (dedicated servers built for Linux), loaded with `LD_PRELOAD`. It keeps the official code
and mechanisms - patternsleuth AOB scans, `UE4SS_Signatures`, `VTableLayout.ini`, Lua mods - and
changes only what Linux needs. Windows games running under Proton/Wine use the official Windows
build, not this one.

## Supported and tested games

Tested on the real dedicated server of each game, in Docker, with a probe Lua mod that runs
`FindFirstOf`, `StaticFindObject`, `RegisterHook` on Blueprint and native functions,
`ExecuteInGameThread` and `NotifyOnNewObject`, with every default hook enabled.

| Game | Engine | Extra files | Result |
|---|---|---|---|
| RuneScape: Dragonwilds (dedicated server) | 5.6.1, modified by the developer | `UE4SS_Signatures/` and `VTableLayout.ini`, generated from the server's `.sym` | everything works; every vtable hook checked against the `.sym` |
| Palworld (dedicated server) | 5.1.1 | none | everything works, including hooks fired from animation worker threads (10 min run) |

Engine versions with Linux layouts built in: **5.1, 5.6**. Any other version falls back to the MSVC
layouts with the destructor shift, and the hooks that read a vtable slot stay off (logged).

C++ mods are tested on Dragonwilds with probe mods: `StaticFindObject`, `FindFirstOf` (from
`on_update`, once the engine object exists), log output, and exceptions thrown and caught inside a
mod, thrown by UE4SS and caught by a mod, and thrown by a mod's `start_mod` and caught by UE4SS
(logged as a mod that failed to load).

Not tested yet: Linux game **clients** (the GUI and hotkeys are not built on Linux:
`UE4SS_GUI_ENABLED`/`UE4SS_INPUT_ENABLED` are off), other engine versions.

## Installing

From a release of this fork (`linux-*` tags): `libUE4SS.so`, `UE4SS-settings.ini` and
`ue4ss-mods-shared.tar.gz` (the Lua libraries of `Mods/shared`), with `SHA256SUMS`.

```
<Game>/Binaries/Linux/<Game>-Linux-Shipping      the game
<Game>/Binaries/Linux/ue4ss/libUE4SS.so
<Game>/Binaries/Linux/ue4ss/UE4SS-settings.ini
<Game>/Binaries/Linux/ue4ss/Mods/shared/...      from ue4ss-mods-shared.tar.gz
<Game>/Binaries/Linux/ue4ss/Mods/mods.txt
```

Start the game with `LD_PRELOAD=<...>/ue4ss/libUE4SS.so` (for a systemd service, an
`Environment=LD_PRELOAD=...` drop-in). The library starts only in a process whose executable name
contains `-Linux-` (or is `UE4SS_TARGET_EXE`), so the launcher script and the tools it runs are
left alone. `UE4SS.log` is written next to the library.

For a game with a modified engine (Dragonwilds), generate its files from the `.sym` the server
ships and put them in the same `ue4ss/` folder:

```bash
python3 tools/linux-layouts/ue_vtable_layout.py <executable> assets/VTableLayoutTemplates/VTableLayout_5_06_Template.ini > ue4ss/VTableLayout.ini
python3 tools/linux-layouts/ue_signatures.py <executable> ue4ss/UE4SS_Signatures
```

A C++ mod is a shared object, `ue4ss/Mods/<Mod>/dlls/main.so`, exporting `start_mod` and
`uninstall_mod`. Build it with `target_link_libraries(<mod> PUBLIC UE4SS)`, which on Linux also links
the mod's C++ runtime the way `libUE4SS.so` links its own (see below). It gets the API it gets on
Windows: the `RC_*_API` declarations.

## What changed for Linux, and why

- **Build**: Linux platform type and Clang 19 or newer (clang 18 hides libstdc++'s `std::expected`,
  which glaze needs); GUI and input optional; Windows-only libraries behind `WIN32`; POSIX ports of
  the file, mutex and scanner layers; `LD_PRELOAD` constructor entry.
- **Running inside the game**: libstdc++ and the unwinder are linked in and bound locally (the game
  exports its own libc++abi/libunwind, and every `throw` inside UE4SS died in them). A mod linking
  the `UE4SS` target inherits the same runtime flags (the game also exports `operator new/delete`),
  and both run one `throw` as they load: an exception crossing between a mod and UE4SS otherwise
  reaches a copy of the unwinder that has never run, and the game aborts.
- **Exports**: `libUE4SS.so` exports what `UE4SS.dll` exports, the `RC_*_API` declarations, and hides
  the rest (`-fvisibility-ms-compat`; clang ignores `__declspec(dllexport)` on Linux). C++ mods bind
  to the UE4SS and Unreal API, and the library's own Lua, fmt, Zydis and patternsleuth symbols, ahead
  of the game's in lookup as a preloaded library, cannot replace any of them.
- **patternsleuth** reads the ELF image (`image-elf`).
- **Itanium ABI**: the FName constructor takes `this` first; `ProcessLocalScriptFunction` is the
  tail jump of `ProcessInternal` under Clang; `ULocalPlayer::Exec` comes from the primary vtable.
- **Layouts**: vtable and member offsets of each engine version generated from Epic's source with
  clang (`tools/linux-layouts`, see its README): Itanium orders vtables differently and reuses tail
  padding, and platform types change size (`FRWLock`).
- **Lua**: `RegisterHook` callbacks take the Lua state lock (a hooked function can run on a worker
  thread; not Linux-specific).
- **Lua `io.open`**: turns every `\` of a mod's path into `/` (Windows-authored mods such as
  PartyHats join paths with `\`), so a filename containing a literal `\` cannot be opened with it.
