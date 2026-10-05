// libUE4SS.so and every C++ mod link their own hidden copy of libgcc's unwinder, and a copy that has
// never unwound aborts the game when an exception crosses into it. One throw at load readies each copy.
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
