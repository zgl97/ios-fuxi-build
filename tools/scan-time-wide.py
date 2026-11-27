#!/usr/bin/env python3
"""Keep the scan cap at the value the firmware actually finishes with (r588).

Evidence trail for this knob:
  r567  1500 ms: the firmware ended the walk itself (WMI_SCAN_EVENT_COMPLETED)
        and WMI_VDEV_START was answered.  The walk was active but the firmware
        seeds `dwell_time_active_2g` with zero, so no beacon was heard and the
        supplicant saw zero BSS.
  r579  8000 ms: the driver's own timeout is `max_scan_time + 200 ms`, so the
        driver always won, aborted locally and left the scan engine busy; every
        later WMI command, `WMI_VDEV_START` included, timed out.
  r586 15000 ms: same, the firmware still had not reported COMPLETED at 15.2 s.
  r587  3000 ms: same again at 3.2 s - the firmware only ever completes the
        walk when the cap is the 1500 ms it was given in the beginning.
  r588  1500 ms: back to the cap the firmware completes with, now that
        scan-dwell.py gives 2.4 GHz a non-zero dwell, so the short walk is
        enough to hear the beacons.  Completion must come from the firmware,
        not from our abort path, or `WMI_VDEV_START` is never answered.

Run after scan-finish-fast.py.
"""
import pathlib
import sys

TARGET = 1500

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"

text = wmi.read_text()
new = f"\targ->max_scan_time = {TARGET};"
if new in text:
    print(f"scan-time-wide: max_scan_time already {TARGET} ms")
    sys.exit(0)

for cap in (3000, 8000, 15000):
    old = f"\targ->max_scan_time = {cap};"
    if old in text:
        if text.count(old) != 1:
            raise SystemExit(
                "scan-time-wide: expected one max_scan_time cap, found %d"
                % text.count(old)
            )
        wmi.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"scan-time-wide: max_scan_time {cap} -> {TARGET} ms (firmware ends the walk)")
        sys.exit(0)

if "\targ->max_scan_time = 1500;" in text:
    print("scan-time-wide: max_scan_time already 1500 ms")
    sys.exit(0)

raise SystemExit("scan-time-wide: no max_scan_time cap found")
