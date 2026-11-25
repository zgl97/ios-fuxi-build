#!/usr/bin/env python3
"""Advertise a non-zero scan SSID budget to cfg80211/wpa_supplicant."""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "src/nw_mainline.c"
s = path.read_text(encoding="utf-8")

old = """static void ready(struct net_device *dev) {
  dev->input = input; dev->input_flush = input_flush;
"""
new = """static void ready(struct net_device *dev) {
  dev->input = input; dev->input_flush = input_flush;
  dev->ieee80211_ptr->wiphy->max_scan_ssids = 4;
  dev->ieee80211_ptr->wiphy->max_scan_ie_len = 2048;
"""
if old not in s:
    raise SystemExit("ready prologue not found")
s = s.replace(old, new, 1)
path.write_text(s, encoding="utf-8")
print("max-scan-ssids: patched src/nw_mainline.c")
