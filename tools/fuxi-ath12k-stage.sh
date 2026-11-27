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
# r564: the first scan makes the debug firmware emit a burst of ~106
# WMI_DIAG_EVENTID (0x1d011) messages of ~1.5 KB.  Printing every one of them
# on the slow console stalls the CE2 rx work long enough that the ring fills
# and the firmware goes silent (no vdev delete/start response, no PEER_MAP).
# Quiet the diag flood and grow the WMI receive ring so the burst fits.
python3 "$root/tools/wmi-log-quiet.py" "$DEST"
python3 "$root/tools/ce2-ring-512.py" "$DEST"
# r541: keep the scan vdev created but do not start it; standard mac80211
# scan only creates a scan vdev and lets WMI_START_SCAN drive the scan.
# python3 "$root/tools/scan-vdev-start.py" "$DEST"
python3 "$root/tools/max-scan-ssids.py" "$DEST"
python3 "$root/tools/bss-age-refresh.py" "$DEST"
python3 "$root/tools/delay-first-scan.py" "$DEST"
python3 "$root/tools/reg-chan-list-fallback.py" "$DEST"
python3 "$root/tools/idle-ps-off.py" "$DEST"
python3 "$root/tools/chan-list-2g-only.py" "$DEST"
python3 "$root/tools/nw-scan-stuck-diag.py" "$DEST"
# r561: instrument the STA-connect 3 s stall (ath12k_wait_for_peer_created waits
# 3*HZ for the HTT PEER_MAP that never arrives). Probes CE/HTC RX liveness
# (ce irq / ce wq / ce service / ce rx pipe / replenish retry), every HTT t2h
# type incl PEER_MAP, peer create send/wait, dp link peer map, and mac station_add
# / vdev_start (is_created/is_started, setup_sync ret). See HANDOFF §30.7.
python3 "$root/tools/peer-htt-diag.py" "$DEST"
# r563: break the STA start-order deadlock. r562 NWDIAG showed sta_add
# created=1 started=0, peer create TX ok, wait_event_timeout(PEER_MAP)=0 (no
# HTT PEER_MAP, no dppeer map): with vdev_start_delay=true the STA vdev is
# never started before peer_create, so the firmware never maps the peer. Flip
# wcn7850 hw2.0 vdev_start_delay to false so assign_vif_chanctx starts the vdev
# first. qcc2072's identical line is left untouched.
python3 "$root/tools/vdev-start-delay-off.py" "$DEST"
# r565/r566: the boot scan must not run while wpa_supplicant initializes: it
# holds the interface locks long enough that wpa's INFO request times out at
# 3 s, wpa cancels the scan and every later WMI command times out.  r565 tried
# skipping it entirely, but the device wpa binary still uses the board-scan
# reuse path and never scans on its own.  r566 delays the same single 2.4 GHz
# passive scan until wpa has initialized, then the board scan fills the BSS
# cache its reuse path reads.  r570 tried to gate on a "ready control client"
# instead of a fixed delay, but that predicate never turned true and the scan
# landed a minute late, on top of wpa's authentication attempt.  r571 goes back
# to the fixed 12 s delay that r567 used to reach HTT PEER_MAP.
python3 "$root/tools/defer-first-scan.py" "$DEST"
python3 "$root/tools/no-board-scan.py" "$DEST"
# r571: scan-after-client.py is unhooked - its gate is what broke r570.  If the
# gating idea comes back, fix nw_cfg80211_client_ready() first (assign
# last_info_jiffies on a successful INFO, and only count GET_BSS batches of a
# session that already exchanged INFO); otherwise the poll always expires.
# python3 "$root/tools/scan-after-client.py" "$DEST"
python3 "$root/tools/sheng-scan-real.py" "$DEST"
# r565: keep a late regulatory update from rewriting WMI_SCAN_CHAN_LIST while
# the first scan is starting (the second list command cancels it), and log all
# scan events so COMPLETED/DEQUEUED is visible.
python3 "$root/tools/reg-scan-chan-list-guard.py" "$DEST"
python3 "$root/tools/scan-event-log.py" "$DEST"
# r575: r574 booted, wpa's INFO timed out three times, and every "Remove
# interface en0 from radio" killed the scan that was already running on vdev 0:
# the firmware's WMI_SCAN_EVENT_COMPLETED then arrived for a vdev that no
# longer had an interface, ath12k_scan_event() could not resolve the radio and
# dropped the event, so the BSS cache stayed empty and wpa reported "No
# suitable network found" forever.  Attribute such events to the single active
# radio instead of dropping them.
python3 "$root/tools/scan-event-fallback.py" "$DEST"
# r567: this firmware never sends WMI_SCAN_EVENT_COMPLETED for the first scan,
# so cap max_scan_time at 8 s, skip the always-timing-out WMI scan-stop, and
# keep the scan vdev (it is the primary interface's link-0 vdev the station
# connection needs) instead of deleting it.  Also keep wpa from reading the
# BSS cache while the scan is still running: it would otherwise authenticate
# before the vdev is free and every vdev-start would time out.
python3 "$root/tools/scan-finish-fast.py" "$DEST"
python3 "$root/tools/scan-stop-ok.py" "$DEST"
python3 "$root/tools/scan-stop-finish.py" "$DEST"
python3 "$root/tools/scan-time-wide.py" "$DEST"
python3 "$root/tools/scan-result-gate.py" "$DEST"
# r570: log which kernel path aborts the one scan the firmware will complete.
python3 "$root/tools/scan-abort-diag.py" "$DEST"
# r568: follow an authentication frame from mac80211 into WMI/CE3.  The probes
# are rare (one line per frame), so they stay; the CE3/CE4 tx-completion cap
# widening that came with them is gone (r571) - it printed from the CE
# completion path and broke the r567 scan/auth timing.
python3 "$root/tools/mgmt-tx-diag.py" "$DEST"
# r574: r573 booted the byte-identical r567 payload and still timed out in
# ath12k_mac_vdev_setup_sync() (four times, -110 after exactly 5000 jiffies)
# while the firmware was mid-scan, then the WMI queue filled up (-105 on every
# PDEV_SET_PARAM).  Give WMI_VDEV_START 15 s and print the elapsed wait plus
# the response event, so one boot log separates "slow" from "wedged".
python3 "$root/tools/vdev-setup-timeout.py" "$DEST"
# r575: keep the interface alive when the board-path scan makes the first
# NW_CONTROL_INFO time out (that failure is what cascaded into r574), and stamp
# both vdev teardown paths so the console shows when vdev 0 disappears.
python3 "$root/tools/vdev-start-cmd-diag.py" "$DEST"
python3 "$root/tools/sheng-info-retry.py" "$DEST"
python3 "$root/tools/vdev-delete-diag.py" "$DEST"
NW_SKIP_SOURCE_LOCK=1 python3 "$DEST/tools/fetch-sources.py" --check

stage 'prepare.sh'
(cd "$DEST" && NW_SKIP_SOURCE_LOCK=1 bash tools/prepare.sh)
echo "pinned=$(find "$DEST/out/pinned" -type l | wc -l | tr -d ' ') ath12k=$(find "$DEST/out/ath12k-src" -type l | wc -l | tr -d ' ') mac80211=$(find "$DEST/out/mac80211" -type l | wc -l | tr -d ' ') cfg80211=$(find "$DEST/out/cfg80211" -type l | wc -l | tr -d ' ')"
