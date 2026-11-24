#!/bin/bash
# Stage the Ath12kWifi sources and the Linux/supplicant subset they compile
# against, then run the stack's own tools/prepare.sh to generate its include
# trees. Nothing here builds; see tools/fuxi-ath12k-build.sh for that.
#
# Usage: tools/fuxi-ath12k-stage.sh DEST CACHE_DIR
#
# DEST becomes a complete Ath12kWifi work tree (sources + deps/ + out/).
# CACHE_DIR holds the pinned tarballs so a re-run does not download again.
#
# The pinned Linux tree is the kernel.org tarball, not a git checkout: the
# tarball is one 157 MB download with a published SHA256, while fetching the
# same commit from git.kernel.org means a packfile of the same order and a
# server that resets the TLS handshake often enough to fail CI reruns. Only the
# paths source-lock.json names are extracted, which is what the stack needs.
set -euo pipefail

[[ $# == 2 ]] || { echo 'usage: fuxi-ath12k-stage.sh DEST CACHE_DIR' >&2; exit 2; }
DEST=$1
CACHE=$2
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$CACHE"

# shellcheck source=tools/ci/pins.env
source "$root/tools/ci/pins.env"
[[ -n ${ATH12K_SHA:-} ]] || { echo 'pins.env has no ATH12K_SHA' >&2; exit 2; }

stage() { printf '\n=== %s ===\n' "$*"; }

stage "ath12kWifi $ATH12K_SHA"
if [[ ! -d $DEST/.git ]]; then
    git init -q "$DEST"
    git -C "$DEST" remote add origin "$ATH12K_URL"
fi
git -C "$DEST" fetch --no-tags --depth 1 origin "$ATH12K_SHA"
git -C "$DEST" checkout -q --detach FETCH_HEAD
[[ $(git -C "$DEST" rev-parse HEAD) == "$ATH12K_SHA" ]] || { echo 'ath12kWifi revision mismatch' >&2; exit 1; }
echo "files=$(git -C "$DEST" ls-files | wc -l | tr -d ' ')"

# The lock file in the checkout is the authority for what to extract: it is the
# same file tools/fetch-sources.py verifies against.
python3 - "$DEST" <<'PY'
import json, sys
from pathlib import Path
dest = Path(sys.argv[1])
lock = json.loads((dest / 'tools/source-lock.json').read_text())
linux = lock['linux']
(dest / 'deps').mkdir(exist_ok=True)
(dest / 'deps/linux-paths.txt').write_text('\n'.join(p.lstrip('/') for p in linux['paths']) + '\n')
(dest / 'deps/linux-required.txt').write_text('\n'.join(linux['required']) + '\n')
(dest / 'deps/supplicant-required.txt').write_text('\n'.join(lock['supplicant']['required']) + '\n')
print(f"linux paths={len(linux['paths'])} required={len(linux['required'])}")
print(f"supplicant required={len(lock['supplicant']['required'])}")
print(f"linux sha256={lock['linux'].get('archive_sha256', '(tarball, see pins.env)')}")
PY

stage 'linux tarball'
archive=$CACHE/$LINUX_ARCHIVE
if [[ ! -s $archive ]]; then
    curl --fail --location --retry 3 --connect-timeout 30 "$LINUX_URL" -o "$archive.part"
    mv "$archive.part" "$archive"
fi
echo "$LINUX_SHA256  $archive" | shasum -a 256 -c -
rm -rf "$DEST/deps/linux"
# The selection is done in python, not with tar -T: bsdtar strips the leading
# component before matching the list, so `--strip-components=1 -T paths` misses
# every entry on macOS while working under GNU tar. Only the paths the stack
# includes are extracted, so the tree is ~74 MB instead of 1.4 GB.
python3 "$root/tools/ci/extract-linux-subset.py" \
    "$archive" "$DEST/deps/linux" "$DEST/deps/linux-paths.txt"
echo "linux files=$(find "$DEST/deps/linux" -type f | wc -l | tr -d ' ')"

stage 'supplicant tarball'
supp=$CACHE/$SUPPLICANT_ARCHIVE
if [[ ! -s $supp ]]; then
    curl --fail --location --retry 3 --connect-timeout 30 "$SUPPLICANT_URL" -o "$supp.part"
    mv "$supp.part" "$supp"
fi
echo "$SUPPLICANT_SHA256  $supp" | shasum -a 256 -c -
tar -xzf "$supp" -C "$DEST/deps"
echo "supplicant files=$(find "$DEST/deps/$SUPPLICANT_DIR" -type f | wc -l | tr -d ' ')"

stage 'toolchain compatibility'
# Two patches the stack needs on this toolchain rather than on the pinned
# commit. The first is the source-lock bypass above. The second turns
# BUILD_BUG_ON into a statement: mac80211 uses it straight after case labels,
# which is a C23 extension, and clang 16 rejects it there where clang 21 only
# warns. Both are applied before prepare.sh, which symlinks the headers they
# change.
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/ath12k-skip-source-lock.patch"
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/ath12k-build-bug-on-statement.patch"
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/ath12k-skip-soc-reset.patch"
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/mhi-cache-current-ee.patch"
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/nw-wifi-debug-mask.patch"
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/ath12k-register-note.patch"
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/ath12k-qmi-note.patch"
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/nw-qrtr-seed-wlfw-server.patch"
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/nw-mode-timeout-diag.patch"
patch --batch --forward --fuzz=0 -p1 -d "$DEST" -i "$root/tools/patches/nw-test-scan.patch"
python3 "$root/tools/htc-no-wmi-credits.py" "$DEST"
python3 "$root/tools/wmi-op-rx-log.py" "$DEST"
python3 "$root/tools/scan-vdev-start.py" "$DEST"
python3 "$root/tools/reg-chan-list-fallback.py" "$DEST"
python3 "$root/tools/idle-ps-off.py" "$DEST"
python3 "$root/tools/chan-list-2g-only.py" "$DEST"
python3 "$root/tools/nw-scan-stuck-diag.py" "$DEST"
NW_SKIP_SOURCE_LOCK=1 python3 "$DEST/tools/fetch-sources.py" --check

stage 'prepare.sh'
(cd "$DEST" && NW_SKIP_SOURCE_LOCK=1 bash tools/prepare.sh)
echo "pinned=$(find "$DEST/out/pinned" -type l | wc -l | tr -d ' ') ath12k=$(find "$DEST/out/ath12k-src" -type l | wc -l | tr -d ' ') mac80211=$(find "$DEST/out/mac80211" -type l | wc -l | tr -d ' ') cfg80211=$(find "$DEST/out/cfg80211" -type l | wc -l | tr -d ' ')"
