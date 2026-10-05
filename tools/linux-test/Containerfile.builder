# Toolchain for a Linux UE4SS build that loads inside the RuneScape: Dragonwilds server image. Same
# base as that image (Fedora 44, glibc 2.43): a library built against a newer glibc, such as the
# host's, can need symbols the game's image does not have and then fails to load.
# gcc-c++ and libstdc++-static: clang uses GCC's libstdc++ and libgcc, and UE4SS links both
# statically (UE4SS/CMakeLists.txt, -static-libstdc++ -static-libgcc).
FROM quay.io/fedora/fedora-minimal:44-x86_64
RUN microdnf -y install --setopt=install_weak_deps=0 --nodocs \
      clang lld cmake ninja-build gcc-c++ libstdc++-static rust cargo git-core python3 \
 && microdnf clean all
