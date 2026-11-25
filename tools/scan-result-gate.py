#!/usr/bin/env python3
"""r567: do not hand BSS results to wpa while the first scan is still running.

The board-side scan is started from the kernel shortly after the interface
comes up.  wpa_supplicant's driver shim asks for results 100 ms after its
scan request and then immediately tries to authenticate.  If that happens
while ath12k still considers the scan running, the station vdev cannot be
started and the firmware ignores the request.

Record when a scan is submitted and return NW_CONTROL_NOT_FOUND for GET_BSS
during the first three seconds.  wpa then retries five seconds later, by
which time the (1.5 s capped) scan has finished and the cached BSS list is
complete.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
ctl = root / "src/nw_cfg80211_control.c"


def replace(path, old, new, label):
    text = path.read_text()
    if old not in text:
        raise SystemExit("%s: pattern not found" % label)
    if text.count(old) != 1:
        raise SystemExit("%s: pattern not unique (%d)" % (label, text.count(old)))
    path.write_text(text.replace(old, new, 1))
    print("patched %s: %s" % (path.name, label))


replace(ctl,
        "static bool radio_enabled = true;",
        "static bool radio_enabled = true;\n"
        "static unsigned long nw_scan_started_jiffies;",
        "scan start timestamp declaration")

replace(ctl,
        "  rdev->scan_req = request;\n"
        "  int result = cfg80211_scan(rdev);",
        "  rdev->scan_req = request;\n"
        "  nw_scan_started_jiffies = jiffies;\n"
        "  int result = cfg80211_scan(rdev);",
        "record scan start")

replace(ctl,
        "  uint32_t index;\n"
        "  if (nw_control_get_u32(request, NW_CONTROL_ATTR_BSS_INDEX, &index)) return NW_CONTROL_INVALID;",
        "  uint32_t index;\n"
        "  if (nw_scan_started_jiffies &&\n"
        "      time_before(jiffies, nw_scan_started_jiffies + msecs_to_jiffies(3000)))\n"
        "    return NW_CONTROL_NOT_FOUND;\n"
        "  if (nw_control_get_u32(request, NW_CONTROL_ATTR_BSS_INDEX, &index)) return NW_CONTROL_INVALID;",
        "gate get_bss until scan settled")
