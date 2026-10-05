// LinkProbe: a C++ mod that reaches what libUE4SS.so must share with mods instead of letting them link
// copies of their own: static data that inline API reads, object lookup, the shared-object registry and
// Lua's C API. CI only links it; check.sh then reads its symbols.
#include <lua.hpp>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Mod/CppUserModBase.hpp>
#include <UE4SSProgram.hpp>
#include <Unreal/UClass.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/VirtualFunctionHelper.hpp>

using namespace RC;
using namespace RC::Unreal;

class LinkProbe : public CppUserModBase
{
  public:
    // get_program() and HasAnyFlags are inline: this mod's own code reads UE4SSProgram::s_program and
    // Container::UnrealObjectVC.
    auto on_unreal_init() -> void override
    {
        program = &UE4SSProgram::get_program();
        auto* actor = UObjectGlobals::StaticFindObject<UClass*>(nullptr, nullptr, STR("/Script/Engine.Actor"));
        is_class_default_object = actor && actor->HasAnyFlags(RF_ClassDefaultObject);
        shared = &SharedObjectManager::GetSharedObject<int>("LinkProbe");
    }

    using CppUserModBase::on_lua_start;
    auto on_lua_start(LuaMadeSimple::Lua& lua, LuaMadeSimple::Lua&, LuaMadeSimple::Lua&, LuaMadeSimple::Lua*) -> void override
    {
        lua_pushnil(lua.get_lua_state());
        lua_pop(lua.get_lua_state(), 1);
    }

    UE4SSProgram* program{};
    bool is_class_default_object{};
    int* shared{};
};

// Clang ignores __declspec(dllexport) on Linux: default visibility is what exports these.
#define LINK_PROBE_API __attribute__((visibility("default")))

extern "C"
{
    LINK_PROBE_API CppUserModBase* start_mod()
    {
        return new LinkProbe();
    }

    LINK_PROBE_API void uninstall_mod(CppUserModBase* mod)
    {
        delete mod;
    }
}
