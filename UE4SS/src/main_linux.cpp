// Linux entry point: the counterpart of main_ue4ss_rewritten.cpp (DllMain) for a libUE4SS.so loaded
// with LD_PRELOAD. Same flow - build the program from the library's own path, then run init() on a
// thread of its own - with two Linux-only rules:
//  - LD_PRELOAD reaches every process the service starts (the game's launch script, readlink,
//    dirname...), not only the game. UE4SS starts only in an Unreal Linux binary
//    ("<Name>-Linux-<Configuration>"), or in the one named by UE4SS_TARGET_EXE.
//  - The constructor runs before main(), with the engine still empty: nothing waits here. init()
//    itself waits for the engine's objects (see UnrealInitializer, "Waiting for object construction").
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <thread>

#include <dlfcn.h>

#include <DynamicOutput/DynamicOutput.hpp>
#include <Helpers/String.hpp>
#include <UE4SSProgram.hpp>
#include <Unreal/UnrealInitializer.hpp>

using namespace RC;

namespace
{
    auto is_game_process() -> bool
    {
        std::error_code error;
        const auto executable = std::filesystem::read_symlink("/proc/self/exe", error);
        if (error)
        {
            return false;
        }
        const auto name = executable.filename().string();
        if (const char* target = std::getenv("UE4SS_TARGET_EXE"); target && *target)
        {
            return name == target;
        }
        return name.find("-Linux-") != std::string::npos;
    }

    auto library_path() -> std::filesystem::path
    {
        Dl_info info{};
        if (dladdr(reinterpret_cast<void*>(&library_path), &info) && info.dli_fname)
        {
            return std::filesystem::path{info.dli_fname};
        }
        return {};
    }

    auto thread_start(std::filesystem::path library) -> void
    {
        // Built here and not in the constructor: an ELF constructor may run before the static
        // initializers of the rest of libUE4SS.so (KeyDef's key table, among others), and parsing the
        // settings then fails on a valid key. On Windows DllMain only runs after all of them.
        auto program = new UE4SSProgram(library, {});
        program->init();
        if (auto e = program->get_error_object(); e->has_error())
        {
            if (!Output::has_internal_error())
            {
                Output::send<LogLevel::Error>(STR("Fatal Error: {}\n"), ensure_str(e->get_message()));
            }
            else
            {
                std::fprintf(stderr, "Error: %s\n", e->get_message());
            }
        }
    }
} // namespace

__attribute__((constructor)) static void ue4ss_linux_start()
{
    if (!is_game_process())
    {
        return;
    }
    std::thread{thread_start, library_path()}.detach();
}

__attribute__((destructor)) static void ue4ss_linux_stop()
{
    // Process exit: like DLL_PROCESS_DETACH with a non-null lpReserved on Windows, the program is
    // not torn down here (statics of the engine and the C++ runtime may already be gone).
    Unreal::UnrealInitializer::StaticStorage::bIsInitialized = false;
}
