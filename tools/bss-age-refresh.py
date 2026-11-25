#!/usr/bin/env python3
"""Refresh a BSS entry's timestamp before the control path looks it up.

The board path performs the only working scan (vdev 0) before wpa_supplicant
starts.  By the time the first authentication arrives those entries are older
than IEEE80211_SCAN_RESULT_EXPIRE (30 s with HZ=1000), so cfg80211_get_bss()
skips them as expired and authenticate() fails with -ENOENT
(NW_CONTROL_NOT_FOUND).  They are still the freshest data this firmware can
produce, so touch the matching entry instead of letting the lookup miss.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "src/nw_cfg80211_control.c"
s = path.read_text(encoding="utf-8")

old = """static struct cfg80211_bss *find_bss(struct cfg80211_registered_device *rdev, const struct nw_control_view *request) {
"""

new = """static void refresh_bss_age(struct cfg80211_registered_device *rdev, const u8 *bssid) {
  struct cfg80211_internal_bss *entry;
  spin_lock_bh(&rdev->bss_lock);
  list_for_each_entry(entry, &rdev->bss_list, list)
    if (ether_addr_equal(entry->pub.bssid, bssid)) entry->ts = jiffies;
  spin_unlock_bh(&rdev->bss_lock);
}
static struct cfg80211_bss *find_bss(struct cfg80211_registered_device *rdev, const struct nw_control_view *request) {
"""

if old not in s:
    raise SystemExit("find_bss prologue not found")
s = s.replace(old, new, 1)

old_call = """  return cfg80211_get_bss(&rdev->wiphy, channel, bssid, ssid, ssid_length, IEEE80211_BSS_TYPE_ESS, IEEE80211_PRIVACY_ANY);
"""
new_call = """  refresh_bss_age(rdev, bssid);
  return cfg80211_get_bss(&rdev->wiphy, channel, bssid, ssid, ssid_length, IEEE80211_BSS_TYPE_ESS, IEEE80211_PRIVACY_ANY);
"""
if old_call not in s:
    raise SystemExit("cfg80211_get_bss call not found")
s = s.replace(old_call, new_call, 1)

path.write_text(s, encoding="utf-8")
print("bss-age-refresh: patched src/nw_cfg80211_control.c")
