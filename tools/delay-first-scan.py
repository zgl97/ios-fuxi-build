#!/usr/bin/env python3
"""Wait for the netdev to settle, then start one scan through the board path."""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "src/nw_mainline.c"
s = path.read_text(encoding="utf-8")

old = """  nw_wifi_stats.stage = 4;
  {
    extern int nw_cfg80211_scan_all(void);
    int scanned = nw_cfg80211_scan_all();
    if (scanned) lx_log(4, "wifi: first scan request failed: %d", scanned);
    else lx_log(3, "wifi: first scan requested");
  }
"""
new = """  nw_wifi_stats.stage = 4;
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
if old not in s:
    raise SystemExit("ready scan block not found")
s = s.replace(old, new, 1)
path.write_text(s, encoding="utf-8")
print("delay-first-scan: patched src/nw_mainline.c")
