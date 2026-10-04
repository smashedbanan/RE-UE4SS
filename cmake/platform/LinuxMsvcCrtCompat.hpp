#pragma once

// The UE4SS code base uses a handful of Microsoft "secure CRT" functions (printf_s in ~17 files,
// strncpy_s/sprintf_s in ErrorObject, localtime_s in two profilers). They do not exist in glibc.
// Instead of editing every caller, the Linux build force-includes this header (see the top
// CMakeLists.txt): same names and argument order, implemented over the standard functions.
#if !defined(_WIN32)

#include <cerrno>
#include <cstdarg>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <ctime>

using errno_t = int;

inline int printf_s(const char* format, ...)
{
    va_list args;
    va_start(args, format);
    int result = std::vprintf(format, args);
    va_end(args);
    return result;
}

inline int sprintf_s(char* buffer, size_t size, const char* format, ...)
{
    va_list args;
    va_start(args, format);
    int result = std::vsnprintf(buffer, size, format, args);
    va_end(args);
    return result;
}

template <size_t Size>
inline int sprintf_s(char (&buffer)[Size], const char* format, ...)
{
    va_list args;
    va_start(args, format);
    int result = std::vsnprintf(buffer, Size, format, args);
    va_end(args);
    return result;
}

// MSVC semantics: copies at most `count` characters and ALWAYS terminates (truncating if needed).
inline errno_t strncpy_s(char* destination, size_t destination_size, const char* source, size_t count)
{
    if (!destination || destination_size == 0) { return EINVAL; }
    size_t length = 0;
    while (length < count && length + 1 < destination_size && source && source[length]) { ++length; }
    std::memcpy(destination, source, length);
    destination[length] = '\0';
    return 0;
}

template <size_t Size>
inline errno_t strncpy_s(char (&destination)[Size], const char* source, size_t count)
{
    return strncpy_s(destination, Size, source, count);
}

// MSVC puts the destination first; POSIX localtime_r/gmtime_r take the source first.
inline errno_t localtime_s(std::tm* destination, const std::time_t* source)
{
    return localtime_r(source, destination) ? 0 : EINVAL;
}

inline errno_t gmtime_s(std::tm* destination, const std::time_t* source)
{
    return gmtime_r(source, destination) ? 0 : EINVAL;
}

#endif
