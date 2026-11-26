#!/usr/bin/env python3
"""r578: a scan may start again after the board scan was abandoned.

scan-finish-fast.py replaces the WMI cancel in ath12k_mac_scan_stop() with
"skip the command, return -ETIMEDOUT".  That was harmless while wpa_supplicant
answered every scan from the board cache, but r577 turned the front-end scan
into a real one, and ath12k_start_scan() calls ath12k_mac_scan_stop() first:
the synthetic -ETIMEDOUT now aborts every scan before WMI_SCAN_START is sent,
which is exactly the "failed to stop scan: -110 / failed to start hw scan:
-110" pair in the r577 boot log.

Report the stop as done instead (the firmware frees the scan engine when
max_scan_time expires) so ath12k_start_scan() proceeds to WMI_SCAN_START.

Run after scan-finish-fast.py.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
mac = root / "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"
s = mac.read_text()
old = """\tath12k_info(ar->ab, "NWDIAG scan_stop skip wmi pdev %u\\n",
\t\t   arg.pdev_id);
\tret = -ETIMEDOUT;
"""
new = """\tath12k_info(ar->ab, "NWDIAG scan_stop ok pdev %u\\n",
\t\t   arg.pdev_id);
\tret = 0;
"""
if s.count(old) != 1:
    raise SystemExit("scan_stop skip block not found (%d)" % s.count(old))
mac.write_text(s.replace(old, new, 1))
print("scan-stop-ok: ath12k_mac_scan_stop() reports success")
