#!/usr/bin/env python3
"""Leave the first cfg80211 scan to wpa_supplicant.

The board-path scan consumes vdev 0 before wpa_supplicant starts.  While it
runs, the control workqueue is stuck in dev_close()/rtnl paths, so wpa's INFO
request times out, wpa cancels the scan and the firmware wedges.  Skipping the
boot scan lets wpa's own scan be the first (vdev 0) scan and run to completion.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "src/nw_mainline.c"
s = path.read_text(encoding="utf-8")

old = """  nw_wifi_stats.stage = 4;
  /* Let register_netdevice()/the cfg80211 notification settle before the
   * first scan.  The board path takes the interface locks itself. */
  lx_delay_ms(3000);
  {
    extern int nw_cfg80211_scan_all(void);
    int scanned = nw_cfg80211_scan_all();
    if (scanned) lx_log(4, "wifi: first scan request failed: %d", scanned);
    else lx_log(3, "wifi: first scan requested");
  }
"""
new = """  nw_wifi_stats.stage = 4;
  /* Let register_netdevice()/the cfg80211 notification settle.  The first
   * scan is left to wpa_supplicant so that it owns vdev 0. */
  lx_delay_ms(3000);
  lx_log(3, "wifi: first scan deferred to wpa_supplicant");
"""
if old not in s:
    raise SystemExit("delay-first-scan block not found")
s = s.replace(old, new, 1)
path.write_text(s, encoding="utf-8")
print("defer-first-scan: patched src/nw_mainline.c")
