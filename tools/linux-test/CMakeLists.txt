# Builds UE4SS the way a C++ mod outside this repository consumes it: RuneSchema brings UE4SS in
# with FetchContent, which is add_subdirectory underneath. Run by build.sh.
cmake_minimum_required(VERSION 3.22)
project(UE4SSLinuxTest CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../.." ue4ss)
