#include <Helpers/Casting.hpp>

#ifdef _WIN32
#define WINDOWS
#define NOMINMAX
#include "Windows.h"
#else
#include <sys/uio.h>
#include <unistd.h>
#endif

namespace RC::Helper::Casting
{

#ifdef WINDOWS
    auto check_readable(void* handle, void* src_ptr) -> bool
    {
        uintptr_t is_valid_ptr_buffer;
        size_t bytes_read;

        return ReadProcessMemory(*reinterpret_cast<HANDLE*>(handle), src_ptr, &is_valid_ptr_buffer, 0x8, &bytes_read) != 0;
    }
#else
    // Same probe as ReadProcessMemory on Windows: process_vm_readv on our own pid fails with EFAULT
    // on an unmapped address instead of faulting. The handle is not needed on Linux.
    auto check_readable([[maybe_unused]] void* handle, void* src_ptr) -> bool
    {
        uintptr_t is_valid_ptr_buffer;
        iovec local{&is_valid_ptr_buffer, sizeof(is_valid_ptr_buffer)};
        iovec remote{src_ptr, sizeof(is_valid_ptr_buffer)};
        return process_vm_readv(getpid(), &local, 1, &remote, 1, 0) == static_cast<ssize_t>(sizeof(is_valid_ptr_buffer));
    }
#endif

} // namespace RC::Helper::Casting
