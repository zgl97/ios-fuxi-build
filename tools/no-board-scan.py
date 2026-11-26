#!/usr/bin/env python3
"""r578: drop the board-path scan now that wpa_supplicant scans for real.

Why the board scan existed: every front-end scan was answered from the BSS
cache, so the kernel had to fill that cache with one 13-channel passive scan
before wpa_supplicant looked at it.  That scan takes vdev 0 (the id the
interface itself needs), holds the interface locks, and - since the firmware
never completes it - leaves the scan engine wedged; the boot logs since r573
show it being torn down mid-scan with scan_state=ABORTING, which is why the
cache stayed empty and no scan ever returned a BSS.

r577 gives wpa_supplicant a real scan on its own interface vdev, so the board
scan is now pure interference: it is started 12 s after boot, immediately after
wpa's INFO succeeds, and blocks it.

Run after defer-first-scan.py (which installs the 12 s version of this block).
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "src/nw_mainline.c"
s = path.read_text(encoding="utf-8")

old = """  lx_delay_ms(12000);
  {
    extern int nw_cfg80211_scan_all(void);
    int scanned = nw_cfg80211_scan_all();
    if (scanned) lx_log(4, "wifi: delayed first scan request failed: %d", scanned);
    else lx_log(3, "wifi: delayed first scan requested");
  }
"""
new = """  /* r578: wpa_supplicant scans on its own interface vdev now; a board-path
   * scan only steals vdev 0 and wedges the firmware scan engine. */
  lx_log(3, "wifi: board scan skipped");
"""
if s.count(old) != 1:
    raise SystemExit("deferred board scan block not found (%d)" % s.count(old))
path.write_text(s.replace(old, new, 1), encoding="utf-8")
print("no-board-scan: nw_mainline.c no longer runs the board scan")
