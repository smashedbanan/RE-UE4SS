cmake_minimum_required(VERSION 3.22)
project(UE4SS)

message("Configuring UE4SS")

# Add proxy generator subproject
add_subdirectory("proxy_generator")

# Release settings
option(UE4SS_LIB_BETA_IS_STARTED "Have beta releases started for the current major version" ON)
option(UE4SS_LIB_IS_BETA "Is this a beta release" ON)

# Define generated directories
set(UE4SS_GENERATED_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/generated_include")
set(UE4SS_GENERATED_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/generated_src")

# ---------------------------------------------------------------------------
# Version Information
# ---------------------------------------------------------------------------

# Read version from file
file(READ ${UE4SS_GENERATED_SOURCE_DIR}/version.cache UE4SS_LIB_VERSION)
string(REGEX MATCHALL "([0-9]+).([0-9]+).([0-9]+).([0-9]+).([0-9]+)" VERSION_PARTS "${UE4SS_LIB_VERSION}")
set(UE4SS_LIB_VERSION_MAJOR ${CMAKE_MATCH_1})
set(UE4SS_LIB_VERSION_MINOR ${CMAKE_MATCH_2})
set(UE4SS_LIB_VERSION_HOTFIX ${CMAKE_MATCH_3})
set(UE4SS_LIB_VERSION_PRERELEASE ${CMAKE_MATCH_4})  # Publicly available pre-release
set(UE4SS_LIB_VERSION_BETA ${CMAKE_MATCH_5})  # Pre-pre-release version

# Get Git SHA
find_package(Git QUIET)
if(GIT_FOUND)
    execute_process(
        COMMAND ${GIT_EXECUTABLE} rev-parse --short HEAD
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        RESULT_VARIABLE GIT_RESULT
        OUTPUT_VARIABLE UE4SS_LIB_BUILD_GITSHA
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
    )
    if(NOT GIT_RESULT EQUAL 0)
        set(UE4SS_LIB_BUILD_GITSHA "unknown")
    endif()
else()
    set(UE4SS_LIB_BUILD_GITSHA "no-git")
endif()

message("UE4SS Version: ${UE4SS_LIB_VERSION_MAJOR}.${UE4SS_LIB_VERSION_MINOR}.${UE4SS_LIB_VERSION_HOTFIX}.${UE4SS_LIB_VERSION_PRERELEASE}.${UE4SS_LIB_VERSION_BETA} (${UE4SS_LIB_BUILD_GITSHA})")

# ---------------------------------------------------------------------------
# Target Configuration
# ---------------------------------------------------------------------------

# Find source files
file(GLOB_RECURSE UE4SS_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/src/**.cpp")
# Each platform has its own entry point: DllMain on Windows, an LD_PRELOAD constructor on Linux.
if(WIN32)
    list(FILTER UE4SS_SOURCES EXCLUDE REGEX "/src/(main|unwinder)_linux\\.cpp$")
else()
    list(FILTER UE4SS_SOURCES EXCLUDE REGEX "/src/main_ue4ss_rewritten\\.cpp$")
endif()
if(NOT UE4SS_GUI_ENABLED)
    # Dumpers.cpp also holds the dump functions the Lua API calls; only its tab needs the GUI.
    list(FILTER UE4SS_SOURCES EXCLUDE REGEX "/src/GUI/")
    list(APPEND UE4SS_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/src/GUI/Dumpers.cpp")
endif()
file(GLOB_RECURSE UE4SS_HEADERS "${CMAKE_CURRENT_SOURCE_DIR}/include/**.hpp")

# Create the main library
add_library(UE4SS SHARED ${UE4SS_SOURCES})

# Set C++23 standard 
target_compile_features(UE4SS PUBLIC cxx_std_23)

# Compile the UE4SS SDK generator backend in from the same JSON that ships in assets/, so the
# generator is usable without any files on disk and the built-in can never drift from the shipped one.
set(UE4SS_SDK_BACKEND_JSON_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../assets/UE4SS_SDK_Backends/UE4SS.json")
file(READ "${UE4SS_SDK_BACKEND_JSON_PATH}" UE4SS_SDK_BACKEND_JSON)
configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/include/SDKGenerator/BuiltinSDKBackend.hpp.in"
    "${CMAKE_CURRENT_BINARY_DIR}/generated/SDKGenerator/BuiltinSDKBackend.hpp"
    @ONLY
)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${UE4SS_SDK_BACKEND_JSON_PATH}")

# Add include directories
target_include_directories(UE4SS PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/generated_include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}/generated>
)

# Add version definitions with PUBLIC visibility so external consumers get them
target_compile_definitions(UE4SS PUBLIC
    UE4SS_LIB_VERSION_MAJOR=${UE4SS_LIB_VERSION_MAJOR}
    UE4SS_LIB_VERSION_MINOR=${UE4SS_LIB_VERSION_MINOR}
    UE4SS_LIB_VERSION_HOTFIX=${UE4SS_LIB_VERSION_HOTFIX}
    UE4SS_LIB_VERSION_PRERELEASE=${UE4SS_LIB_VERSION_PRERELEASE}
    UE4SS_LIB_VERSION_BETA=${UE4SS_LIB_VERSION_BETA}
    UE4SS_LIB_BETA_STARTED=$<BOOL:${UE4SS_LIB_BETA_IS_STARTED}>
    UE4SS_LIB_IS_BETA=$<BOOL:${UE4SS_LIB_IS_BETA}>
    UE4SS_LIB_BUILD_GITSHA="${UE4SS_LIB_BUILD_GITSHA}"
    UE4SS_CONFIGURATION="$<CONFIG>"
)

# Add headers as sources for IDE integration
target_sources(UE4SS PUBLIC
    FILE_SET ue4ss_headers TYPE HEADERS
    BASE_DIRS include
    FILES ${UE4SS_HEADERS}
)

# Organize in IDE
source_group(TREE "${CMAKE_CURRENT_LIST_DIR}" FILES ${UE4SS_SOURCES} ${UE4SS_HEADERS})

# Add natvis file for Visual Studio debugging (works with MSVC and clang-cl)
if(MSVC OR CMAKE_CXX_SIMULATE_ID STREQUAL "MSVC")
    target_sources(UE4SS PRIVATE
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/../UE4SS.natvis>
    )
endif()

# First party static build definitions
target_compile_definitions(UE4SS PRIVATE
    RC_UE4SS_EXPORTS
    RC_ASM_HELPER_BUILD_STATIC
    RC_FILE_BUILD_STATIC
    RC_DYNAMIC_OUTPUT_BUILD_STATIC
    RC_SINGLE_PASS_SIG_SCANNER_BUILD_STATIC
    RC_SINGLE_PASS_SIG_SCANNER_STATIC
    RC_UNREAL_BUILD_STATIC
    RC_INPUT_BUILD_STATIC
    RC_LUA_MADE_SIMPLE_BUILD_STATIC
    RC_FUNCTION_TIMER_BUILD_STATIC
    RC_PARSER_BASE_BUILD_STATIC
    RC_INI_PARSER_BUILD_STATIC
    RC_JSON_BUILD_STATIC
    RC_JSON_PARSER_BUILD_STATIC
    RC_LUA_WRAPPER_GENERATOR_BUILD_STATIC
    RC_HELPERS_BUILD_STATIC
)

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

# Link first-party dependencies
target_link_libraries(UE4SS PUBLIC
    File glaze::glaze DynamicOutput
    Unreal SinglePassSigScanner LuaMadeSimple
    Function IniParser JSON
    Input Constructs Helpers
    MProgram ScopedTimer Profiler
    patternsleuth_bind
)

# Link third-party dependencies
target_link_libraries(UE4SS PUBLIC fmt PolyHook_2)
if(UE4SS_GUI_ENABLED)
    target_link_libraries(UE4SS PUBLIC ImGui glfw glad)
    target_compile_definitions(UE4SS PUBLIC HAS_GUI)
endif()
if(WIN32)
    target_link_libraries(UE4SS PUBLIC dbghelp psapi ws2_32 ntdll userenv)
    if(UE4SS_GUI_ENABLED)
        target_link_libraries(UE4SS PUBLIC d3d11 opengl32)
    endif()
else()
    # The library lives inside a game that brings its own C++ runtime: an Unreal Linux binary exports
    # libc++abi (__cxa_throw and company), and the executable comes first in symbol lookup. Without
    # these flags a throw inside UE4SS - even one caught a few lines below - runs the game's
    # __cxa_throw with another library's personality routine, and the process dies (seen on Palworld
    # with SIGSEGV, on Dragonwilds with "libc++abi: terminating"). The runtime is linked in and every
    # reference binds inside the library, and none of it is exported back to the game.
    # The unwinder too: Rust's runtime asks for -lgcc_s ahead of everything else, and the game's own
    # _Unwind_RaiseException (LLVM libunwind, exported by Dragonwilds) then raised our exceptions
    # straight into std::terminate. -u pulls libgcc_eh.a in before any object needs it.
    # --exclude-libs names the runtime archives only, not ALL: the first-party libraries (Unreal, File,
    # DynamicOutput, ...) are static archives too, and hiding them left a C++ mod nothing to bind to.
    # The linker then copied them into the mod, a private and never-initialized UObjectGlobals that
    # found nothing. patternsleuth_bind is listed too: it is Rust, out of reach of the visibility flags
    # of the top-level CMakeLists.txt, and UE4SS.dll does not export it either.
    target_link_options(UE4SS PRIVATE -static-libstdc++ -static-libgcc -Wl,-Bsymbolic
                        -Wl,--exclude-libs=libstdc++.a:libgcc.a:libgcc_eh.a:libpatternsleuth_bind.a
                        -Wl,-u,_Unwind_RaiseException -Wl,--push-state,-Bstatic,-lgcc_eh,--pop-state)
    # A C++ mod is loaded into the same game and needs the same isolation, so every target linking UE4SS
    # inherits it. Without it the mod's operator delete bound to the game's (FMallocBinned2) and freed
    # memory the game never allocated: SIGSEGV in FMallocBinned2::FreeExternal on Dragonwilds.
    # No -Bsymbolic: an inline or template static that a mod and UE4SS both define must resolve to one copy.
    target_link_options(UE4SS INTERFACE -static-libstdc++ -static-libgcc -Wl,--exclude-libs,ALL
                        -Wl,-u,_Unwind_RaiseException -Wl,--push-state,-Bstatic,-lgcc_eh,--pop-state)
    # Each of those unwinder copies must have unwound once before an exception crosses into it.
    target_sources(UE4SS INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/src/unwinder_linux.cpp")
endif()

# ---------------------------------------------------------------------------
# Output Configuration
# ---------------------------------------------------------------------------

# Enable link-time optimization
set_property(TARGET UE4SS PROPERTY INTERPROCEDURAL_OPTIMIZATION ON)

# Set runtime library to dynamic CRT (/MD, /MDd)
# This matches Visual Studio's default, so external mods don't need to set it explicitly.
set_property(TARGET UE4SS PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")

# Set output name and standard
set_target_properties(UE4SS PROPERTIES 
    OUTPUT_NAME "UE4SS"
    CXX_STANDARD 23
)

# Use the organize_targets function from IDEOrganization.cmake
# The organize_special_targets function will also handle this target
organize_targets("^UE4SS$" "RE-UE4SS")
