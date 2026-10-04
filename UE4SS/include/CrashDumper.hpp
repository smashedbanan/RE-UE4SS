#pragma once

#include <memory>

namespace PLH
{
    class IatHook;
}

namespace RC
{
    class CrashDumper
    {
      private:
        bool enabled = false;
        void* m_previous_exception_filter = nullptr;
#ifdef _WIN32
        std::unique_ptr<PLH::IatHook> m_set_unhandled_exception_filter_hook;
        uint64_t m_hook_trampoline_set_unhandled_exception_filter_hook;
#endif

      public:
        CrashDumper();
        ~CrashDumper();

      public:
        void enable();
        void set_full_memory_dump(bool enabled);
    };

}; // namespace RC
