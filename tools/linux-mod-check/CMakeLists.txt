# Builds UE4SS the way a C++ mod outside this repository consumes it (add_subdirectory, which
# FetchContent also uses), plus LinkProbe, a mod that check.sh inspects. Run by CI's build-linux job.
cmake_minimum_required(VERSION 3.22)
project(UE4SSLinuxModCheck CXX)

add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../.." ue4ss)

add_library(LinkProbe SHARED main.cpp)
target_link_libraries(LinkProbe PUBLIC UE4SS)
