#!/usr/bin/env python3
"""Delay the first scan until the netdev has settled, then use the normal
board scan entry point instead of calling cfg80211's scan op directly."""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "src/nw_mainline.c"
s = path.read_text(encoding="utf-8")

old_proto = "#define UI_BSS_MAX 128"
new_proto = "int nw_wifi_scan(const u8 *ssid, size_t length);\n#define UI_BSS_MAX 128"
if old_proto in s and new_proto not in s:
    s = s.replace(old_proto, new_proto, 1)

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
   * first scan.  The normal board request path checks primary->registered
   * and takes the interface locks itself. */
  lx_delay_ms(3000);
  {
    int queued = nw_wifi_scan(NULL, 0);
    if (queued) lx_log(4, "wifi: first scan queue failed: %d", queued);
    else lx_log(3, "wifi: first scan queued");
  }
"""
if old not in s:
    raise SystemExit("ready scan block not found")
s = s.replace(old, new, 1)
path.write_text(s, encoding="utf-8")
print("delay-first-scan: patched src/nw_mainline.c")
