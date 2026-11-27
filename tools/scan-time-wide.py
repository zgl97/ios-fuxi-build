#!/usr/bin/env python3
"""Keep the firmware on a leash it can actually finish (r587).

History of this knob:
  r567  1500 ms: the firmware ended the scan itself (WMI_SCAN_EVENT_COMPLETED)
        and WMI_VDEV_START was answered, but a 1.5 s walk is too short to hear
        beacons, so the supplicant saw zero BSS.
  r579  8000 ms: the driver's own timeout is `max_scan_time + 200 ms`, so the
        driver always won the race, aborted locally at 8.2 s and left the
        firmware's scan engine running.  r580 and r585 then got `vdev_start
        setup_sync ... ret -110` and every later WMI command timed out.
  r586 15000 ms: the firmware still had not reported COMPLETED when the driver
        gave up at 15.2 s, so the local abort kept winning.
  r587  3000 ms: thirteen channels at the firmware's own dwell budget (60 ms
        active / 150 ms passive) need about two seconds, so a 3 s cap leaves
        the firmware ~1 s of slack to end the walk on its own, while the
        driver's 3.2 s timeout stays behind it.  Paired with scan-dwell.py,
        which gives 2.4 GHz active scans a non-zero dwell so the beacon
        actually lands in the walk.

Run after scan-finish-fast.py.
"""
import pathlib
import sys

TARGET = 3000

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"

text = wmi.read_text()
new = f"\targ->max_scan_time = {TARGET};"
if new in text:
    print(f"scan-time-wide: max_scan_time already {TARGET} ms")
    sys.exit(0)

for cap in (1500, 8000, 15000):
    old = f"\targ->max_scan_time = {cap};"
    if old in text:
        if text.count(old) != 1:
            raise SystemExit(
                "scan-time-wide: expected one max_scan_time cap, found %d"
                % text.count(old)
            )
        wmi.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"scan-time-wide: max_scan_time {cap} -> {TARGET} ms (firmware can finish the walk)")
        sys.exit(0)

raise SystemExit("scan-time-wide: no max_scan_time cap found")
