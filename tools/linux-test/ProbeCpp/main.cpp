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
