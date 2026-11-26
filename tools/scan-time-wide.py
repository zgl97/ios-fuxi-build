#!/usr/bin/env python3
"""Keep max_scan_time at the value this firmware actually completes (r581).

`scan-finish-fast.py` caps max_scan_time at 1.5 s.  r578 showed that a widened
window (8 s, r579) makes things worse: the firmware emits four channel events
and then neither completes the scan nor answers WMI_VDEV_START, so the whole
association path stalls (r580: `vdev_start setup_sync ... ret -110` twice).

With 1.5 s, the firmware finishes the walk (r567: six channel events and a real
WMI_SCAN_EVENT_COMPLETED) because its own scan timeout is what ends it.  Keep
that value, and rely on scan-stop-finish.py only for the abort path.

Run after scan-finish-fast.py.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"

text = wmi.read_text()
old = "\targ->max_scan_time = 1500;"
new = "\targ->max_scan_time = 8000;"
if text.count(old) != 1:
    raise SystemExit("scan-time-wide: expected exactly one max_scan_time cap, found %d"
                     % text.count(old))

wmi.write_text(text.replace(old, new, 1), encoding="utf-8")
print("scan-time-wide: max_scan_time raised to 8000 ms (13-channel walk)")
