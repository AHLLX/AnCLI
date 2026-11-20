#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT_DIR/src/module/bin"
WORK_DIR="${RUNNER_TEMP:-/tmp}/ancli-proot-build"

PROOT_URL="https://github.com/proot-me/proot/archive/refs/tags/v5.4.0.tar.gz"
PROOT_SHA256="29248aac2a7ce10c3bd5ee5602742ec33b2532310ff9cf73b79f3c133e5a5f68"

NDK_DIR="${ANDROID_NDK_HOME:-${ANDROID_NDK_LATEST_HOME:-${ANDROID_NDK_ROOT:-}}}"
if [ -z "$NDK_DIR" ] && [ -d "/usr/local/lib/android/sdk/ndk" ]; then
    NDK_DIR="$(find /usr/local/lib/android/sdk/ndk -maxdepth 1 -mindepth 1 -type d | sort -V | tail -n 1)"
fi
[ -n "$NDK_DIR" ] && [ -d "$NDK_DIR/toolchains/llvm/prebuilt/linux-x86_64/bin" ] || {
    echo "[X] Android NDK toolchain not found" >&2
    exit 1
}
TOOLCHAIN="$NDK_DIR/toolchains/llvm/prebuilt/linux-x86_64/bin"

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$OUT_DIR/arm64" "$OUT_DIR/x86_64"

curl -fsSL "$PROOT_URL" -o "$WORK_DIR/proot.tar.gz"
echo "$PROOT_SHA256  $WORK_DIR/proot.tar.gz" | sha256sum --check --status
tar -xzf "$WORK_DIR/proot.tar.gz" -C "$WORK_DIR"
PROOT_SRC="$WORK_DIR/proot-5.4.0"

# Fetch talloc 2.4.2 from upstream samba-team/samba GitHub tag (commit-pinned).
TALLOC_COMMIT="f28966c1638806a5af1fa4e451b668af638491ce"
git clone --depth 1 --branch talloc-2.4.2 --filter=blob:none --sparse \
    https://github.com/samba-team/samba.git "$WORK_DIR/samba"
git -C "$WORK_DIR/samba" sparse-checkout set lib/talloc lib/replace
[ "$(git -C "$WORK_DIR/samba" rev-parse HEAD)" = "$TALLOC_COMMIT" ] || {
    echo "[X] talloc commit mismatch" >&2
    exit 1
}
TALLOC_SRC="$WORK_DIR/samba/lib/talloc"
REPLACE_SRC="$WORK_DIR/samba/lib/replace"

build_arch() {
    local arch="$1"
    local triple="$2"
    local cc="$TOOLCHAIN/${triple}24-clang"
    local ar="$TOOLCHAIN/llvm-ar"
    local strip_bin="$TOOLCHAIN/llvm-strip"
    local stage="$WORK_DIR/build-$arch"
    mkdir -p "$stage/talloc" "$stage/proot"
    cp -a "$PROOT_SRC/src/." "$stage/proot/"

    cat > "$stage/talloc/config.h" <<'EOF'
#define __STDC_WANT_LIB_EXT1__ 1
#include <stdio.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <errno.h>
#include <unistd.h>
#include <sys/types.h>
#include <dlfcn.h>
/* NDK sys/types.h already defines useconds_t; prevent replace.h's fallback typedef. */
#define useconds_t __ancli_replace_useconds_t
#define TALLOC_BUILD_VERSION_MAJOR 2
#define TALLOC_BUILD_VERSION_MINOR 4
#define TALLOC_BUILD_VERSION_RELEASE 2
#define HAVE_CONSTRUCTOR_ATTRIBUTE 1
#define HAVE_VA_COPY 1
#define HAVE_STRNLEN 1
#define HAVE_BOOL 1
#define HAVE_INTPTR_T 1
#define HAVE_UINTPTR_T 1
#define HAVE_PTRDIFF_T 1
#define HAVE_USECONDS_T 1
#define HAVE_STDBOOL_H 1
#define HAVE_STDINT_H 1
#define HAVE_STDDEF_H 1
#define HAVE_STDARG_H 1
#define HAVE_STDIO_H 1
#define HAVE_STDLIB_H 1
#define HAVE_STRING_H 1
#define HAVE_LIMITS_H 1
#define HAVE_ERRNO_H 1
#define HAVE_UNISTD_H 1
#define HAVE_SYS_TYPES_H 1
#define HAVE_DLFCN_H 1
#define HAVE_STRLCPY 1
#define HAVE_STRLCAT 1
EOF
    "$cc" -O2 -D__STDC_WANT_LIB_EXT1__=1 -I"$TALLOC_SRC" -I"$REPLACE_SRC" -I"$stage/talloc" -include "$stage/talloc/config.h" \
        -c "$TALLOC_SRC/talloc.c" -o "$stage/talloc/talloc.o"
    "$ar" rcs "$stage/talloc/libtalloc.a" "$stage/talloc/talloc.o"

    make -C "$stage/proot" -j"$(nproc)" \
        V=1 \
        CC="$cc" \
        LD="$cc" \
        STRIP="$strip_bin" \
        OBJCOPY="$TOOLCHAIN/llvm-objcopy" \
        OBJDUMP="$TOOLCHAIN/llvm-objdump" \
        CFLAGS="-O2 -I$TALLOC_SRC -I$stage/proot -D_FILE_OFFSET_BITS=64 -D_GNU_SOURCE -Wno-error" \
        LDFLAGS="-L$stage/talloc -ltalloc -static" \
        proot

    "$strip_bin" "$stage/proot/proot" || true
    cp -f "$stage/proot/proot" "$OUT_DIR/$arch/proot"
    chmod 755 "$OUT_DIR/$arch/proot"
    if [ "$arch" = "x86_64" ]; then
        for ldr in loader/loader loader/loader-m32; do
            if [ -f "$stage/proot/$ldr" ]; then
                cp -f "$stage/proot/$ldr" "$OUT_DIR/x86_64/$(basename "$ldr")"
                chmod 755 "$OUT_DIR/x86_64/$(basename "$ldr")"
            fi
        done
    fi
}

build_arch arm64 aarch64-linux-android
build_arch x86_64 x86_64-linux-android
cp -f "$OUT_DIR/arm64/proot" "$OUT_DIR/proot"
chmod 755 "$OUT_DIR/proot"
ls -lh "$OUT_DIR/proot" "$OUT_DIR/arm64/proot" "$OUT_DIR/x86_64/proot"
