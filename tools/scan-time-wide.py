#!/usr/bin/env python3
"""Give the firmware the whole channel list's worth of time (r579).

`scan-finish-fast.py` capped max_scan_time at 1.5 s so the board scan would
return quickly.  r578 showed why that cap is now fatal: the STA scan reached
channel 3 (2422) after ~1.05 s, the 1.5 s cap plus the 200 ms margin in
ath12k_mac_hw_scan() fired ath12k_scan_timeout_work() first, and the scan was
aborted before the firmware ever emitted WMI_SCAN_EVENT_COMPLETED.

Widen it back to 8 s so the passive 13-channel walk finishes and the firmware
reports completion on its own.  Run after scan-finish-fast.py.
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
print("patched wmi.c: max_scan_time 1500 -> 8000")
