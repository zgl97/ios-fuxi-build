#!/bin/bash
# Shared hosted/local Mac entry point. Never invokes a boot-image packer.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
usage() {
    cat <<'HELP'
Usage: tools/ci/build.sh TARGET [--dry-run]
Targets: kernel-metal4, kernel-wlan6g, kernel-metal4e, kernel-ios27, kernel-ath12k, metal, nativemetal, mesa, tools
       tools/ci/build.sh --plan KERNEL METAL MESA TOOLS [NATIVEMETAL] [ATH12K]
KERNEL: none|metal4|wlan6g|metal4e|ios27|all; remaining plan arguments: true|false
metal4e is the opt-in arm64e build of the metal4 kernel (FUXI_ARM64E=1, patch 0115); all does not include it.
ios27 is the iOS 27 kernel (xnu-12377 + xnu27/series, tools/ci/build-xnu27.sh): it
needs Xcode 26 or newer with ld-classic, ignores FUXI_IIG_MODE; all does not include it.
ath12k compiles the 125 objects of the Ath12kWifi Linux Wi-Fi stack for arm64
and audits them against this kernel's exports. It links nothing: the objects are
evidence for the port's next milestone, not a kext.
Outputs: tools/out/ci/TARGET/ (explicit allowlist, logs, SHA256SUMS)
Caches: deps/ci-downloads/; isolated build directories: deps/ci-work-TARGET/
Requires a Mac with preinstalled Xcode 15.4 (kernel) or a selected full Xcode
(userspace), Homebrew (prefix only; nothing is brew-installed), and rustup (metal). Kernel setup may install its KDK.
--dry-run validates arguments and prints the plan on Linux without side effects.
FUXI_IIG_MODE (kernel): xcode (default; Xcode 15 iig), capture (Xcode 15 iig, also
emit iig-pregen/), replay (any Xcode; replay FUXI_IIG_CACHE or xnu/iig-pregen/).
HELP
}
die() { echo "fuxi-ci: $*" >&2; exit 2; }
[[ $# -gt 0 ]] || { usage >&2; exit 2; }
case "$1" in
    -h|--help) [[ $# == 1 ]] || die 'unexpected help arguments'; usage; exit 0 ;;
    --plan)
        [[ $# == 5 || $# == 6 ]] || die '--plan needs KERNEL METAL MESA TOOLS [NATIVEMETAL]'
        case "$2" in none|metal4|wlan6g|metal4e|ios27|all) ;; *) die 'invalid kernel selection' ;; esac
        for flag in "$3" "$4" "$5" "${6:-false}" "${FUXI_ATH12K:-false}"; do
            [[ $flag == true || $flag == false ]] || die 'plan flags must be true or false'
        done
        python3 - "$2" "$3" "$4" "$5" "${6:-false}" "${FUXI_ATH12K:-false}" <<'PY'
import json, sys
kernel, metal, mesa, tools, nativemetal, ath12k = sys.argv[1:]
targets = ["kernel-" + v for v in ("metal4", "wlan6g") if kernel in (v, "all")]
targets += ["kernel-" + kernel] if kernel in ("metal4e", "ios27") else []
targets += [t for t, f in zip(("metal", "mesa", "tools", "nativemetal"), (metal, mesa, tools, nativemetal)) if f == "true"]
# kernel-ath12k compiles the Linux stack, which no kernel profile depends on:
# it is selected on its own so an object-level regression cannot be mistaken
# for a kernel one.
if ath12k == "true":
    targets += ["kernel-ath12k"]
if not targets:
    raise SystemExit("Select at least one target")
print(json.dumps({"target": targets}))
PY
        exit ;;
esac
target=$1
shift
case "$target" in kernel-metal4|kernel-wlan6g|kernel-metal4e|kernel-ios27|kernel-ath12k|metal|nativemetal|mesa|tools) ;; *) die "unknown target: $target" ;; esac
dry=false
if [[ $# == 1 && $1 == --dry-run ]]; then dry=true; shift; fi
[[ $# == 0 ]] || die 'unexpected arguments'
# shellcheck source=tools/ci/pins.env
source "$root/tools/ci/pins.env"
iig_mode=${FUXI_IIG_MODE:-xcode}
case "$iig_mode" in xcode|capture|replay) ;; *) die "unknown FUXI_IIG_MODE: $iig_mode" ;; esac
if $dry; then
    printf 'target=%s\nartifact=fuxi-%s\noutput=tools/out/ci/%s\n' "$target" "$target" "$target"
    if [[ $target == kernel-ios27 ]]; then
        # shellcheck source=xnu27/sources.env
        source "$root/xnu27/sources.env"
        printf 'pins: %s=%s trustcache=%s=%s availability=%s libplatform=%s libdispatch=%s %s=%s\n' "$XNU27_TAG" "$XNU27_SHA" \
            "$XNU27_TRUSTCACHE_TAG" "$XNU27_TRUSTCACHE_SHA" "$AVAILABILITYVERSIONS_SHA" "$LIBPLATFORM_SHA" "$LIBDISPATCH_SHA" \
            "$LIBPTHREAD_TAG" "$LIBPTHREAD_SHA"
        printf 'kernel: arm64e FUXI (BCM2837 layer) DEVELOPMENT, %s patches, Xcode >= 26 + ld-classic, iig from Xcode\n' \
            "$(grep -cv '^#' "$root/xnu27/series")"
        echo 'outputs: mach.development.fuxi(.unstripped) fuxi27.adt fuxi27-cpus7.adt cmdline.txt cmdline-cpus7.txt kernel-arch.txt'
        echo 'libpthread: kernel part force-loaded into the DEVELOPMENT, chained and RELEASE links (libpthread-report.json, libpthread-linked.txt)'
        echo 'No downloads, builds, signing or packing in dry-run mode.'
        exit 0
    fi
    [[ $target != kernel-* || $target == kernel-ath12k ]] || printf 'iig=%s cache=%s\n' "$iig_mode" "${FUXI_IIG_CACHE:-xnu/iig-pregen}"
    printf 'pins: dxb=%s mesa=%s metal2vulkan=%s rust=%s\n' "$DXB_SHA" "$MESA_SHA" "$M2V_SHA" "$RUST_VERSION"
    if [[ $target == kernel-metal4e ]]; then
        echo 'kernel: arm64e (FUXI_ARM64E=1, patch 0115), metal4 cmdline + cmdline-cpus1.txt, needs ld-classic'
    fi
    if [[ $target == kernel-ath12k ]]; then
        printf 'ath12k: %s=%s linux=%s supplicant=%s\n' "$ATH12K_SHA" "$ATH12K_URL" "$LINUX_SHA256" "$SUPPLICANT_SHA256"
        echo 'outputs: ath12k-objects.txt ath12k-count.txt ath12k-{undefined,defined,external,libc,needs-kernel,globals}.txt objects/ ath12k-logs/'
        echo '         ath12k-gap-*.txt and ath12k-collisions.txt only when FUXI_ATH12K_KERNEL names a kernel binary'
        echo 'Nothing is linked into the kernel and no boot image is packed.'
    fi
    echo 'No downloads, builds, signing or packing in dry-run mode.'
    exit 0
fi
[[ $(uname -s) == Darwin ]] || die 'builds require macOS; use --dry-run on Linux'
cd "$root"
out=$root/tools/out/ci/$target
work=$root/deps/ci-work-$target
downloads=$root/deps/ci-downloads
[[ ! -e $out && ! -e $work ]] || die 'output/work directory exists; move it aside for a fresh build'
mkdir -p "$out/logs" "$work" "$downloads"

# Even a failed build emits only its log + checksum manifest, never a build tree.
manifest() {
    python3 - "$out" <<'PY'
import hashlib
from pathlib import Path
import sys
out = Path(sys.argv[1])
files = sorted(p for p in out.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
if any(p.is_symlink() for p in out.rglob('*')):
    raise SystemExit('Artifact must not contain symlinks')
(out/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(out).as_posix()+'\n' for p in files))
PY
}
finish() { status=$?; trap - EXIT; manifest; exit "$status"; }
trap finish EXIT

fetch() {
    local url=$1 dest=$2
    if [[ ! -s $dest ]]; then
        curl --fail --location --retry 3 --connect-timeout 30 "$url" -o "$dest.part"
        mv "$dest.part" "$dest"
    fi
}

# Cache a source-only shallow git snapshot as a tarball, including its revision
# metadata. No SDKs, KDKs, build products or boot inputs enter these caches.
source_checkout() {
    local name=$1 url=$2 pin=$3 dest=$4
    local archive=$downloads/$name-$pin-source-v1.tar.gz
    if [[ -f $archive ]]; then
        mkdir -p "$dest"
        tar -xzf "$archive" -C "$dest"
    else
        git init -q "$dest"
        git -C "$dest" remote add origin "$url"
        if [[ $name == metal2vulkan ]]; then
            # Full clones fetch precompiled Apple AIR fixtures. Partial clone
            # with sparse checkout avoids downloading those blobs at all.
            git -C "$dest" config remote.origin.promisor true
            git -C "$dest" config remote.origin.partialclonefilter blob:none
            git -C "$dest" sparse-checkout init --no-cone
            git -C "$dest" sparse-checkout set --no-cone '/*' \
                '!**/fixtures/' '!**/golden/' '!**/corpus/' \
                '!**/*.air' '!**/*.metallib' '!**/*.mtlb' '!**/*.spv' '!**/*.bin'
            git -C "$dest" -c protocol.version=2 fetch --filter=blob:none --no-tags --depth 1 origin "$pin"
        else
            git -C "$dest" fetch --no-tags --depth 1 origin "$pin"
        fi
        git -C "$dest" checkout --detach FETCH_HEAD
        tar -czf "$archive.part" -C "$dest" .
        mv "$archive.part" "$archive"
    fi
    [[ $(git -C "$dest" rev-parse HEAD) == "$pin" ]] || die "$name cache revision mismatch"
    git -C "$dest" diff --exit-code
    git -C "$dest" diff --cached --exit-code
}

# Verified source/binary download into the cache, then unpacked into $work.
fetch_verified() {
    local url=$1 name=$2 sum=$3
    fetch "$url" "$downloads/$name"
    echo "$sum  $downloads/$name" | shasum -a 256 -c -
}

install_gum() {
    # Hosted macOS runners are arm64 or x64; the pinned binary must match the host.
    local arch=arm64 sha=$GUM_SHA256
    if [[ $(uname -m) != arm64 ]]; then arch=x86_64 sha=$GUM_SHA256_X86_64; fi
    fetch_verified "https://github.com/charmbracelet/gum/releases/download/v$GUM_VERSION/gum_${GUM_VERSION}_Darwin_${arch}.tar.gz" \
        "gum-$GUM_VERSION-darwin-$arch.tar.gz" "$sha"
    mkdir -p "$work/gum"
    tar -xzf "$downloads/gum-$GUM_VERSION-darwin-$arch.tar.gz" -C "$work/gum"
    # The pinned builder rebuilds PATH from Xcode, system dirs and the Homebrew
    # prefix only, so builder-visible tools go into that prefix.
    install -m 0755 "$(find "$work/gum" -type f -name gum | head -1)" "$(brew --prefix)/bin/gum"
    gum --version
}

install_bash() {
    fetch_verified "https://ftp.gnu.org/gnu/bash/bash-$BASH_VERSION_PIN.tar.gz" \
        "bash-$BASH_VERSION_PIN.tar.gz" "$BASH_SHA256"
    tar -xzf "$downloads/bash-$BASH_VERSION_PIN.tar.gz" -C "$work"
    (cd "$work/bash-$BASH_VERSION_PIN" && ./configure --prefix="$native" --without-bash-malloc >/dev/null && make -j6 >/dev/null && make install >/dev/null)
    "$native/bin/bash" --version | head -1
}

install_glslang() {
    fetch_verified "https://github.com/KhronosGroup/glslang/archive/refs/tags/$GLSLANG_VERSION.tar.gz" \
        "glslang-$GLSLANG_VERSION.tar.gz" "$GLSLANG_SHA256"
    tar -xzf "$downloads/glslang-$GLSLANG_VERSION.tar.gz" -C "$work"
    cmake -S "$work/glslang-$GLSLANG_VERSION" -B "$work/glslang-build" -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$native" \
        -DENABLE_OPT=OFF -DGLSLANG_TESTS=OFF -DBUILD_EXTERNAL=OFF
    ninja -C "$work/glslang-build" install
    glslangValidator --version | head -1
}

setup() {
    # macOS 14 is a Homebrew tier-3 platform without bottles, so never run brew
    # install/upgrade: use the runner's tools and build missing ones from pins.
    native=$work/native
    mkdir -p "$native/bin"
    export PATH="$native/bin:$PATH"
    local cmd
    for cmd in cmake ninja; do command -v "$cmd" >/dev/null || die "runner lacks $cmd"; done
    case "$target" in
        kernel-ios27) command -v unifdef >/dev/null || die 'runner lacks unifdef' ;;
        # The Linux stack is compiled straight with xcrun's clang: it needs no
        # pinned bash, no gum and no jq, and it never runs the XNU builder.
        kernel-ath12k) ;;
        kernel-*) command -v jq >/dev/null || die 'runner lacks jq'
                  install_gum; install_bash ;;
        mesa) command -v pkg-config >/dev/null || die 'runner lacks pkg-config'
              install_glslang ;;
        tools) install_glslang ;;
    esac
    if [[ $target == kernel-ios27 ]]; then
        [[ -n ${DEVELOPER_DIR:-} ]] || die 'kernel-ios27 needs DEVELOPER_DIR (Xcode 26 or newer)'
    elif [[ $target == kernel-* && $target != kernel-ath12k ]]; then
        export DEVELOPER_DIR=${DEVELOPER_DIR:-/Applications/Xcode_15.4.app/Contents/Developer}
    else
        export DEVELOPER_DIR=${DEVELOPER_DIR:-$(xcode-select -p)}
    fi
    [[ -d $DEVELOPER_DIR ]] || die 'required full Xcode is missing; never download Xcode in CI'
    xcodebuild -version
    xcrun --sdk iphoneos --show-sdk-path
    if [[ $target == kernel-ios27 ]]; then
        local major
        major=$(xcodebuild -version | sed -n '1s/^Xcode \([0-9]*\).*/\1/p')
        [[ ${major:-0} -ge 26 ]] || die 'kernel-ios27 needs Xcode 26 or newer (xnu-12377 iig and SDK)'
        xcrun --find iig
    elif [[ $target == kernel-* && $target != kernel-ath12k ]]; then
        echo "IIG mode: $iig_mode"
        if [[ $iig_mode == replay ]]; then
            # Newer iig emits an IORPC ABI XNU 8796 lacks: never run it, replay
            # hash-checked outputs of Xcode 15 iig from the same .iig inputs.
            iig_cache=$(cd "${FUXI_IIG_CACHE:-$root/xnu/iig-pregen}" && pwd) || die 'replay needs FUXI_IIG_CACHE or xnu/iig-pregen'
            [[ -f $iig_cache/manifest.json ]] || die "no IIG manifest in $iig_cache"
            export IIG=$root/tools/xnu-iig-cache.py XNU_IIG_CACHE=$iig_cache
            [[ -x $IIG ]] || die 'IIG replay script is not executable'
            echo "IIG replay cache: $iig_cache"
            (cd "$iig_cache" && shasum -a 256 manifest.json)
        else
            [[ $(xcodebuild -version | head -1) == 'Xcode 15.'* ]] || die 'XNU requires Xcode 15 iig (or FUXI_IIG_MODE=replay)'
            xcrun --find iig
        fi
        if [[ $target == kernel-metal4e ]]; then
            # 0115 links with ld-classic -threaded_starts_section (arm_init signs the chains).
            xcrun --sdk macosx --find ld-classic || die 'arm64e kernel needs ld-classic in the selected Xcode'
        fi
        # The pinned builder tests xcodes even with Xcode preinstalled. Its
        # clean PATH includes this Homebrew prefix; no authentication needed.
        if ! command -v xcodes >/dev/null; then
            printf '#!/bin/sh\nexit 0\n' > "$(brew --prefix)/bin/xcodes"
            chmod +x "$(brew --prefix)/bin/xcodes"
        fi
    fi
}

kernel() {
    local dxb=$work/dxb variant=${target#kernel-} arm64e=0
    [[ $target == kernel-metal4e ]] && arm64e=1
    python3 tools/test-fuxi-iig-cache.py
    python3 tools/test-fuxi-backlight.py
    python3 tools/test-fuxi-prof.py
    python3 tools/test-fuxi-regtrace.py
    python3 tools/test-fuxi-perf.py
    python3 tools/sync-fuxi-perf.py --check
    python3 tools/test-fuxi-audio.py
    python3 tools/sync-fuxi-audio.py --check
    python3 tools/test-fuxi-pac.py
    python3 tools/sync-fuxi-pac.py --check
    python3 tools/test-fuxi-arm64e.py
    if [[ $target == kernel-wlan6g ]]; then
        # WLAN source keeps the existing artifact/target and cmdlines. Exercise the
        # exact C carried by patch 0099 before spending a hosted kernel build.
        python3 tools/test-fuxi-wlan-m6.py
        python3 tools/test-fuxi-wlan-m6i.py
        python3 tools/test-fuxi-wlan-m6k.py
        python3 tools/test-fuxi-wlan-m6l.py
        python3 tools/test-fuxi-wlan-m6m.py
        python3 tools/test-fuxi-wlan-m6n.py
        python3 tools/test-fuxi-wlan-m6o.py
        python3 tools/test-fuxi-wlan-w10.py
        python3 tools/test-fuxi-wlan-w11.py
        python3 tools/test-fuxi-wlan-w12.py
        python3 tools/test-fuxi-wlan-w13.py
        python3 tools/test-fuxi-wlan-w14.py
        python3 tools/test-fuxi-wlan-w15.py
        python3 tools/test-fuxi-wlan-w15-audit.py
        python3 tools/test-fuxi-wlan-w16.py
        python3 tools/test-fuxi-wlan-w17.py
        python3 tools/test-fuxi-wlan-rails.py
        python3 tools/test-fuxi-wlan-m2a.py
        python3 tools/test-fuxi-wlan-m1.py
        python3 tools/test-fuxi-wlan-patch.py
        shasum -a 256 xnu/fuxi-wlan/fuxi_wlan.c xnu/patches/0099-fuxi-wlan.patch \
            xnu/fuxi-wlan/fuxi_wlan_board.c xnu/fuxi-wlan/nw_abi.h \
            shim/fuxi-probe/wlan.c > "$out/wlan-source-SHA256SUMS.txt"
        # The kernel now builds the Ath12kWifi stack in-tree (patch 0117), and dxb
        # runs 9999-stage-ath12k.sh before it applies any patch, so the pinned
        # sources have to be staged first. Same script the compile-only lane uses.
        bash tools/fuxi-ath12k-stage.sh "$work/ath12k" "$downloads"
        export FUXI_ATH12K_TREE=$work/ath12k
        shasum -a 256 xnu/patches/0117-fuxi-ath12k.patch \
            xnu/patches/9999-stage-ath12k.sh xnu/fuxi-ath12k/manifest.txt \
            >> "$out/wlan-source-SHA256SUMS.txt"
    fi
    source_checkout dxb https://github.com/blacktop/darwin-xnu-build.git "$DXB_SHA" "$dxb"
    python3 tools/ci/prepare-kernel.py "$dxb"
    # MACOS_VERSION selects xnu-8796.141.3 + KDK 13.5/22G74. The pinned
    # builder installs that KDK on this runner when absent; never cache/upload it.
    (
        cd "$dxb"
        export MACOS_VERSION=13.5 KERNEL_CONFIG=DEVELOPMENT ARCH_CONFIG=ARM64 MACHINE_CONFIG=FUXI
        # metal4e: MakeInc.def (patch 0115) switches FUXI to arm64e; unset everywhere else.
        if [[ $arm64e == 1 ]]; then export FUXI_ARM64E=1; else unset FUXI_ARM64E; fi
        "$native/bin/bash" build.sh
    )
    cp "$dxb/fakeroot/System/Library/Kernels/kernel.development.fuxi" "$out/"
    # MakeInc.kernel installs the raw linked kernel into SYMROOT *without* an
    # .unstripped suffix; give it the local packer's conventional output name.
    cp "$dxb/build/xnu.sym/kernel.development.fuxi" "$out/kernel.development.fuxi.unstripped"
    if [[ $iig_mode != xcode ]]; then
        # XNU-derived (APSL) generated source only: what every build directory
        # compiled, deduplicated and checked against this build's .iig inputs.
        python3 tools/xnu-iig-cache.py --prepare "$dxb/xnu" "$dxb/build" "$out/iig-pregen"
        python3 tools/xnu-iig-cache.py --verify "$dxb/xnu" "$out/iig-pregen"
        if [[ $iig_mode == replay ]]; then
            cmp "$XNU_IIG_CACHE/manifest.json" "$out/iig-pregen/manifest.json" || die 'replayed IIG outputs differ from the cache'
        fi
        (cd "$out/iig-pregen" && shasum -a 256 manifest.json)
    fi
    python3 tools/ci/kernel-inputs.py --variant "$variant" --out "$out"
    if [[ $arm64e == 1 ]]; then
        # The arm64e boot needs cpusubtype ARM64E and __TEXT,__thread_starts (arm_init signs those chains).
        python3 - "$out/kernel.development.fuxi" <<'PY' | tee "$out/kernel-arch.txt"
import sys
sys.path.insert(0, 'tools')
import mkpayload
m = mkpayload.parse_macho(open(sys.argv[1], 'rb').read())
print(f"cputype={m['cputype']:#x} cpusub={m['cpusub']} thread_starts={m['thread_starts']} chained_fixups={bool(m['fixups'])}")
if m['cpusub'] != mkpayload.CPU_SUBTYPE_ARM64E or not m['thread_starts']:
    raise SystemExit('metal4e kernel is not arm64e with __TEXT,__thread_starts')
PY
    fi
}

# iOS 27 kernel: Apple open-source checkouts at xnu27/sources.env, then
# tools/ci/build-xnu27.sh. No KDK, IPSW, kernelcache or Apple binary is fetched.
kernel_ios27() {
    python3 tools/test-fuxi-ios27.py
    python3 tools/test-fuxi-arm64e.py
    # shellcheck source=xnu27/sources.env
    source xnu27/sources.env
    local apple=https://github.com/apple-oss-distributions
    source_checkout xnu-12377 "$apple/xnu.git" "$XNU27_SHA" "$work/src/xnu"
    source_checkout AvailabilityVersions "$apple/AvailabilityVersions.git" "$AVAILABILITYVERSIONS_SHA" "$work/src/AvailabilityVersions"
    source_checkout libplatform "$apple/libplatform.git" "$LIBPLATFORM_SHA" "$work/src/libplatform"
    source_checkout libdispatch "$apple/libdispatch.git" "$LIBDISPATCH_SHA" "$work/src/libdispatch"
    source_checkout libpthread "$apple/libpthread.git" "$LIBPTHREAD_SHA" "$work/src/libpthread"
    # Only EXTERNAL_HEADERS/TrustCache of the older tag (dropped after it).
    local tc=$work/src/trustcache
    git init -q "$tc"
    git -C "$tc" remote add origin "$apple/xnu.git"
    git -C "$tc" sparse-checkout set --no-cone /EXTERNAL_HEADERS/TrustCache/
    git -C "$tc" fetch --no-tags --depth 1 --filter=blob:none origin "$XNU27_TRUSTCACHE_SHA"
    git -C "$tc" checkout -q --detach FETCH_HEAD
    [[ $(git -C "$tc" rev-parse HEAD) == "$XNU27_TRUSTCACHE_SHA" && -d $tc/EXTERNAL_HEADERS/TrustCache ]] || die 'TrustCache headers missing'
    shasum -a 256 xnu27/series xnu27/patches/*/*.patch xnu27/components/*/*.cpp xnu27/libpthread/series \
        xnu27/libpthread/*.patch > "$out/xnu27-SHA256SUMS.txt"
    bash tools/ci/build-xnu27.sh "$work/src" "$out"
}

mesa_source() {
    source_checkout mesa https://chromium.googlesource.com/external/gitlab.freedesktop.org/mesa/mesa "$MESA_SHA" "$work/mesa"
}

metal() {
    # CPU-only frontend/ownership/policy tests also cover opt-in async pipelines.
    # NativeMetal's independent adapter keeps its existing build route.
    if [[ $target == metal ]]; then python3 tools/test-fuxi-metal-frontend.py; fi
    mesa_source # Vulkan headers from the same pinned Mesa include/ tree.
    # Optional host Vulkan controls use a supplied local ICD and glslang, no downloads.
    if [[ $target == metal && -n ${FUXI_MTL_TEST_ICD:-} ]]; then
        python3 tools/test-fuxi-metal-merge.py --headers "$work/mesa/include" --icd "$FUXI_MTL_TEST_ICD" --glslang "${FUXI_MTL_TEST_GLSLANG:-glslangValidator}"
        python3 tools/test-fuxi-metal-shadows.py --headers "$work/mesa/include" --icd "$FUXI_MTL_TEST_ICD" --glslang "${FUXI_MTL_TEST_GLSLANG:-glslangValidator}"
    fi
    source_checkout metal2vulkan https://github.com/steelbrain/metal2vulkan.git "$M2V_SHA" "$work/metal2vulkan"
    command -v rustup >/dev/null || die 'install rustup before building metal'
    export FUXI_M2V_CACHE=$work/metal2vulkan FUXI_M2V_BRIDGE=$work/metal2vulkan-bridge
    # Use only the selected Xcode's own Metal compiler and SDK.
    # Xcode 15 splits the Metal toolchain into macos/ and ios/ trees; xcrun can
    # return the macOS one, which lacks the iOS runtime library the link needs.
    local rt
    export FUXI_METAL_TOOLS
    # A local Mac may preset this to a mounted Metal Toolchain component
    # (Xcode 26+ ships the Metal compiler as a separate component).
    if [[ -z ${FUXI_METAL_TOOLS:-} ]]; then
        rt=$(find "$DEVELOPER_DIR/Toolchains/XcodeDefault.xctoolchain/usr/metal" -name libmetal_rt_ios.a 2>/dev/null | head -1 || true)
        if [[ -n $rt ]]; then
            FUXI_METAL_TOOLS=${rt%%/lib/clang/*}/bin
        else
            FUXI_METAL_TOOLS=$(dirname "$(xcrun --sdk iphoneos --find metal)")
        fi
    fi
    echo "Metal tools: $FUXI_METAL_TOOLS"
    [[ -x $FUXI_METAL_TOOLS/metal ]] || die 'no Metal compiler next to the iOS runtime library'

    if [[ $target == nativemetal ]]; then
        source_checkout nativemetal https://github.com/UniXNU/NativeMetal.git "$NATIVEMETAL_SHA" "$work/nativemetal"
        # The fallback translator's checkout stays unpatched and independently replaceable.
        source_checkout metal2vulkan https://github.com/steelbrain/metal2vulkan.git "$M2V_SHA" "$work/native-metal2vulkan"
        export FUXI_NATIVEMETAL_SOURCE=$work/nativemetal FUXI_NATIVE_M2V_SOURCE=$work/native-metal2vulkan
        export FUXI_NATIVE_BUILD=$work/native-build
        bash tools/build-fuxi-nativemetal.sh "$work/mesa/include" "$work/outputs"
        cp -R "$work/outputs/FuxiNativeMetal.bundle" "$out/"
        cp "$work/outputs/libnative_metal_bridge.dylib" "$work/outputs/nativemetal-source.tar.gz" "$out/"
    else
        bash tools/build-fuxi-metal.sh "$work/mesa/include" "$work/outputs"
    fi
    # Explicit allowlist preserves the complete ad-hoc signed bundle; no AIR,
    # SDK libraries, intermediate build directories or external fixture blobs.
    # The CA diagnostic loads system CA resources; no Apple shaders are shipped.
    cp -R "$work/outputs/FuxiMetal.bundle" "$out/"
    local name
    for name in fuxi-metal-smoke fuxi-ca-smoke ca-smoke.conf metal-smoke.metallib libmetal2vulkan.dylib \
        metal2vulkan-source.tar.gz metal-smoke.conf metal-clients.conf; do
        cp "$work/outputs/$name" "$out/"
    done
}

mesa() {
    mesa_source
    mkdir -p "$work/patches" "$out/patches" "$out/tools"
    # Fetch only nine text patches, never the UniXNU tree (which may contain
    # unrelated firmware or precompiled inputs). Build recipe checks hashes.
    local name
    for name in freedreno-tools-nir.patch ir3-darwin-build-id.patch \
        ir3-portable-asm-roundtrip.patch offline-adreno-compiler-probe.patch turnip-darwin-kmd.patch \
        turnip-graphics-cache-feedback.patch turnip-empty-scissor.patch \
        turnip-builtin-shader-pack.patch turnip-host-import-images.patch; do
        fetch "https://raw.githubusercontent.com/UniXNU/UniXNU/$UNIXNU_SHA/patches/mesa/$name" \
            "$downloads/$UNIXNU_SHA-$name"
        cp "$downloads/$UNIXNU_SHA-$name" "$work/patches/$name"
    done
    fetch "https://ftp.gnu.org/gnu/bison/bison-$BISON_VERSION.tar.xz" "$downloads/bison-$BISON_VERSION.tar.xz"
    echo "$BISON_SHA256  $downloads/bison-$BISON_VERSION.tar.xz" | shasum -a 256 -c -
    tar -xf "$downloads/bison-$BISON_VERSION.tar.xz" -C "$work"
    (cd "$work/bison-$BISON_VERSION"; ./configure --prefix="$work/native"; make -j6; make install)
    python3 -m venv "$work/venv"
    "$work/venv/bin/pip" install meson==1.12.1 mako==1.4.3 MarkupSafe==3.0.4 pyyaml==6.0.3 packaging==26.3
    export PATH="$work/native/bin:$work/venv/bin:$PATH"
    python3 tools/build-fuxi-turnip.py --mesa "$work/mesa" --patches "$work/patches" \
        --build "$work/mesa-build" --out "$work/outputs"
    cp "$work/outputs/libvulkan_freedreno.dylib" "$out/"
    cp "$work/patches/"*.patch tools/patches/mesa-fuxi-*.patch "$out/patches/"
    cp tools/build-fuxi-turnip.py tools/build-fuxi-vk-shaders.py tools/audit-fuxi-vulkan.py "$out/tools/"
    # Also preserve upstream licence texts, but no prebuilt third-party files.
    cp -R "$work/mesa/licenses" "$out/mesa-licenses"
    cp "$work/mesa/docs/license.rst" "$out/mesa-licenses/"
}

user_tools() {
    mesa_source
    local sdk cc name
    sdk=$(xcrun --sdk iphoneos --show-sdk-path)
    cc=$(xcrun --sdk iphoneos --find clang++)
    python3 tools/build-fuxi-vk-shaders.py "$work/vk-smoke-spv.h"
    for name in probe smoke; do
        "$cc" -target arm64-apple-ios16.0 -isysroot "$sdk" -fstack-protector-strong \
            -Werror=unguarded-availability-new -std=c++17 -O2 -Wall -Wextra -Werror \
            -I"$work/mesa/include" -I"$work" "tools/fuxi-vk-$name.cpp" -o "$out/fuxi-vk-$name"
        codesign -f -s - "$out/fuxi-vk-$name"
        codesign --verify --strict "$out/fuxi-vk-$name"
    done
    bash tools/build-fuxi-gpu-abi-test.sh
    cp tools/out/fuxi-gpu-abi-test tools/vk-smoke.conf "$out/"
    codesign --verify --strict "$out/fuxi-gpu-abi-test"
    bash tools/build-fuxi-thermald.sh "$out"
    cp tools/fuxi-thermald/com.fuxi.thermald.plist tools/fuxi-thermald/LICENSE.HackiOS "$out/"
    python3 tools/audit-fuxi-vulkan.py "$out/fuxi-vk-probe" "$out/fuxi-vk-smoke"
    # Audit sidecars describe only our binaries and are useful with the logs.
}

kernel_ath12k() {
    # Compile-only lane for the Ath12kWifi Linux Wi-Fi stack. It proves the
    # object set a clean macOS toolchain produces, and audits what those objects
    # ask the kernel for. It links nothing into the kernel: that decision needs
    # the object evidence first, and the two failures that follow a bad object
    # set (a missing export, a duplicate definition) are cheaper to read here
    # than in a 26-minute kernel link.
    local tree=$work/ath12k objects=$work/ath12k-out
    bash tools/fuxi-ath12k-stage.sh "$tree" "$downloads"
    bash tools/fuxi-ath12k-build.sh "$tree" "$objects"

    # Objects and the two symbol lists are the artifact. The staged tree, with
    # its thousands of symlinks, stays in $work: manifest() refuses symlinks.
    cp -R "$objects/objects" "$out/objects"
    cp "$objects/ath12k-objects.txt" "$objects/ath12k-undefined.txt" \
       "$objects/ath12k-defined.txt" "$objects/ath12k-external.txt" \
       "$objects/ath12k-libc.txt" "$objects/ath12k-needs-kernel.txt" \
       "$objects/ath12k-globals.txt" "$out/"
    cp -R "$objects/logs" "$out/ath12k-logs"
    [[ ! -s $objects/ath12k-failures.txt ]] || cp "$objects/ath12k-failures.txt" "$out/"

    # The gap against this kernel's exports, and the names this stack defines
    # that the kernel also defines, are only meaningful against a kernel binary.
    # When the caller supplies one, record both; otherwise say so in the artifact
    # rather than implying an audit that never ran.
    #
    # Two gaps are reported, because the honest answer depends on a decision not
    # yet made: ath12k-external.txt is everything the objects need from outside,
    # and every one of those may come from the kernel; ath12k-needs-kernel.txt
    # drops the C library names the shim could still supply. A single number
    # would hide which of the two the port will actually rely on.
    if [[ -n ${FUXI_ATH12K_KERNEL:-} ]]; then
        python3 tools/ci/kernel-exports.py "$FUXI_ATH12K_KERNEL" > "$work/kernel-exports.txt"
        # comm compares byte-wise and demands that both inputs are sorted the
        # same way. kernel-exports.py sorts in C order while sort(1) under the
        # runner's locale puts `__stack_chk_fail` after `bzero`, so an unsorted
        # pair makes comm emit the whole list as "missing" — the first attempt
        # reported 14 of 14. Pin both sides to the same collation.
        LC_ALL=C sort -o "$work/kernel-exports.txt" "$work/kernel-exports.txt"
        local exports_count
        exports_count=$(wc -l < "$work/kernel-exports.txt" | tr -d ' ')
        {
            printf 'kernel=%s exports=%s\n' "$FUXI_ATH12K_KERNEL" "$exports_count"
            for pair in "external:$objects/ath12k-external.txt" "needs-kernel:$objects/ath12k-needs-kernel.txt"; do
                label=${pair%%:*}; list=${pair#*:}
                LC_ALL=C sort -o "$work/gap-input-$label.txt" "$list"
                LC_ALL=C comm -23 "$work/gap-input-$label.txt" "$work/kernel-exports.txt" \
                    > "$out/ath12k-gap-$label.txt"
                printf '%s=%s missing=%s\n' "$label" "$(wc -l < "$list" | tr -d ' ')" \
                    "$(wc -l < "$out/ath12k-gap-$label.txt" | tr -d ' ')"
            done
        } | tee "$out/ath12k-link-gap-summary.txt"
        # A name the stack defines and the kernel also exports is not a link
        # failure for a kext (the stack's own definition wins inside its own
        # image) but it is a real collision once these objects join the kernel,
        # so it is recorded rather than ignored.
        cut -f1 "$objects/ath12k-globals.txt" | LC_ALL=C sort -u > "$work/globals.txt"
        LC_ALL=C comm -12 "$work/globals.txt" "$work/kernel-exports.txt" > "$out/ath12k-collisions.txt"
        printf 'collisions=%s\n' "$(wc -l < "$out/ath12k-collisions.txt" | tr -d ' ')" \
            | tee -a "$out/ath12k-link-gap-summary.txt"
        # A name the objects need and the kernel does not export is the one
        # result this lane exists to catch, so it fails the build rather than
        # sitting in a file nobody opens. Both gaps are checked: the narrower one
        # is a subset, but the wider one is what a kext would actually resolve
        # against, and a missing C library name is just as fatal there.
        for label in external needs-kernel; do
            [[ ! -s $out/ath12k-gap-$label.txt ]] ||
                die "kernel lacks $(wc -l < "$out/ath12k-gap-$label.txt" | tr -d ' ') name(s) the objects need; see ath12k-gap-$label.txt"
        done
    else
        echo 'No FUXI_ATH12K_KERNEL: symbols recorded, the gaps and collisions against kernel exports were not computed.' \
            | tee "$out/ath12k-link-gap-summary.txt"
    fi

    # One object per line of sources.txt, and two objects that share a name would
    # mean one silently replaced the other. Count both sides.
    local expected counted
    expected=$(grep -cvE '^\s*(#|$)' "$tree/tools/sources.txt")
    counted=$(wc -l < "$out/ath12k-objects.txt" | tr -d ' ')
    printf 'sources=%s objects=%s\n' "$expected" "$counted" | tee "$out/ath12k-count.txt"
    [[ $expected == "$counted" ]] || die "sources.txt names $expected sources but $counted objects were recorded"
}

build() {
    setup
    printf 'Repository: %s\nTarget: %s\n' "$(git rev-parse HEAD)" "$target"
    cat tools/ci/pins.env
    case "$target" in
        kernel-ios27) kernel_ios27 ;;
        kernel-ath12k) kernel_ath12k ;;
        kernel-*) kernel ;;
        metal|nativemetal) metal ;;
        mesa) mesa ;;
        tools) user_tools ;;
    esac
}
build 2>&1 | tee "$out/logs/build.log"
