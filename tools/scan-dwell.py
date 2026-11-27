#!/usr/bin/env python3
"""Give 2.4 GHz active scans a real dwell (r587).

`ath12k_wmi_start_scan_init()` seeds the scan request with

        arg->dwell_time_active    = 50;
        arg->dwell_time_active_2g = 0;
        arg->dwell_time_passive   = 150;

A zero `dwell_time_active_2g` means the firmware spends no time on a 2.4 GHz
channel during an active scan: the walk reports every channel event within a
few hundred milliseconds and comes back with no management frames at all,
which is exactly what r582 and r584 showed (thirteen channel events, zero BSS,
while every passive scan on the same band filled the cache).

The iOS supplicant now sends a wildcard SSID, so the scan is active and the
dwell override is what decides whether that scan can hear anything.

Idempotent: the marker records the patched text.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"

text = wmi.read_text()
if "arg->dwell_time_active_2g = 60;" in text:
    print("scan-dwell: 2.4 GHz active dwell already 60 ms")
    sys.exit(0)

pairs = [
    ("\targ->dwell_time_active = 50;", "\targ->dwell_time_active = 60;"),
    ("\targ->dwell_time_active_2g = 0;", "\targ->dwell_time_active_2g = 60;"),
]

for old, new in pairs:
    if text.count(old) != 1:
        raise SystemExit(
            "scan-dwell: expected exactly one %r, found %d" % (old.strip(), text.count(old))
        )

for old, new in pairs:
    text = text.replace(old, new, 1)

wmi.write_text(text, encoding="utf-8")
print("scan-dwell: active dwell 50 -> 60 ms, 2.4 GHz active dwell 0 -> 60 ms")
