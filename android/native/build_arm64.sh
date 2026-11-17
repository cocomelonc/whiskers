#!/usr/bin/env bash
# Third-party llama.cpp build; outputs only to the caller's Whiskers directory.
set -euo pipefail
if [ "$#" -ne 3 ]; then
  printf 'Usage: bash build_arm64.sh LLAMA_CPP_SOURCE ANDROID_NDK OUTPUT_DIRECTORY\n' >&2
  exit 2
fi
whiskers_llama="$1"
whiskers_ndk="$2"
whiskers_out="$3"
whiskers_src="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ "$(git -C "$whiskers_llama" rev-parse HEAD)" != 6f1e6543704bc5b080c7a98e7b6f707b31f6ed51 ]; then
  printf 'Use the recorded llama.cpp revision.\n' >&2
  exit 2
fi
cmake -S "$whiskers_src" -B "$whiskers_out/build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE="$whiskers_ndk/build/cmake/android.toolchain.cmake" -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_static -DLLAMA_DIR="$whiskers_llama"
cmake --build "$whiskers_out/build" -j 8
"$whiskers_ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" --strip-unneeded "$whiskers_out/build/libwhiskers_llm.so" -o "$whiskers_out/libwhiskers_llm.so"
sha256sum "$whiskers_out/libwhiskers_llm.so"
