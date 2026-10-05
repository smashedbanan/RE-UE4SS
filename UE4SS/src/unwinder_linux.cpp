// libUE4SS.so and every C++ mod carry their own hidden copy of libgcc's unwinder (UE4SS/CMakeLists.txt).
// A copy fills its register table the first time it unwinds, and an exception that crosses into
// another module (a mod's throw caught by CppMod::start_mod, or the other way round) calls that
// module's copy: if it has never unwound, it aborts the game. One throw while the library loads
// fills the table. Compiled into UE4SS and, as an INTERFACE source, into every mod.
namespace
{
    [[maybe_unused]] const bool unwinder_ready = [] {
        try
        {
            throw 0;
        }
        catch (int)
        {
        }
        return true;
    }();
} // namespace
