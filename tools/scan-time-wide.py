#!/usr/bin/env python3
"""Give the firmware enough time to finish the walk on its own (r586).

History of this knob:
  r567  1500 ms: the firmware ended the scan itself (WMI_SCAN_EVENT_COMPLETED)
        and WMI_VDEV_START was answered, but a 1.5 s walk is too short to hear
        beacons, so the supplicant saw zero BSS.
  r579  8000 ms: the driver's own timeout is `max_scan_time + 200 ms`, so the
        driver always won the race, aborted locally at 8.2 s and left the
        firmware's scan engine running.  r580 and r585 then got `vdev_start
        setup_sync ... ret -110` and every later WMI command timed out.
  r586 15000 ms: with a 15 s cap the firmware at 8.4 s is the one that ends the
        scan, which is the only state in which WMI_VDEV_START has ever been
        answered.  scan-stop-finish.py stays as the abort path.

Run after scan-finish-fast.py.
"""
import pathlib
import sys

TARGET = 15000

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"

text = wmi.read_text()
old = "\targ->max_scan_time = 1500;"
new = f"\targ->max_scan_time = {TARGET};"
if new in text:
    print(f"scan-time-wide: max_scan_time already {TARGET} ms")
    sys.exit(0)
if text.count(old) != 1:
    raise SystemExit(
        "scan-time-wide: expected exactly one max_scan_time cap, found %d"
        % text.count(old)
    )

wmi.write_text(text.replace(old, new, 1), encoding="utf-8")
print(f"scan-time-wide: max_scan_time raised to {TARGET} ms (firmware finishes the walk)")
