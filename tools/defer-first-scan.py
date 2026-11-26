#!/usr/bin/env python3
"""Delay the board-path scan until after wpa_supplicant has initialized.

The board-path scan consumes vdev 0.  When it runs during netdev registration
it holds the interface locks for seconds; wpa's INFO request then times out,
wpa cancels the scan and the firmware wedges.  Starting it later - after wpa
has opened the interface and run its first scan cycle - lets wpa initialize
cleanly while still giving its reuse path a populated BSS cache.

r566: the device wpa_supplicant still uses the board-scan reuse path, so the
kernel must supply the scan results.  Wait long enough for wpa to settle, then
run the same single 2.4 GHz passive scan the board path always used.

r571: back to a fixed delay.  The r570 gate polled nw_cfg80211_client_ready(),
but that predicate never became true (a GET_BSS counter that also counted
failing sessions, and an INFO timestamp that was never assigned), so the loop
always ran out and the scan fired a full minute after boot - straight into
wpa's authentication attempt, which then blocked on a vdev that was still
scanning.  r567 reached HTT PEER_MAP with this fixed delay, so use it again.
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
  /* Let wpa_supplicant initialize and start its scan cycle before the board
   * path takes the interface locks.  Running this scan earlier makes wpa's
   * INFO request time out and wedges the firmware. */
  lx_delay_ms(12000);
  {
    extern int nw_cfg80211_scan_all(void);
    int scanned = nw_cfg80211_scan_all();
    if (scanned) lx_log(4, "wifi: delayed first scan request failed: %d", scanned);
    else lx_log(3, "wifi: delayed first scan requested");
  }
"""
if old not in s:
    raise SystemExit("defer-first-scan block not found")
s = s.replace(old, new, 1)
path.write_text(s, encoding="utf-8")
print("defer-first-scan: patched src/nw_mainline.c (delayed board scan)")
