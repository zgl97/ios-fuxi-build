#!/usr/bin/env python3
"""r570: name the caller when the single firmware scan is aborted.

The first scan is the only scan this firmware completes, so an abort is fatal
for the connection attempt.  Log both kernel call sites that can abort it, with
the vdev and jiffies, so a remaining race is visible in the console log.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"
s = path.read_text(encoding="utf-8")


def replace(old, new, label):
    global s
    if old not in s:
        raise SystemExit(f"scan-abort-diag: anchor not found: {label}")
    s = s.replace(old, new, 1)


replace(
    "\t\tath12k_scan_abort(ar);\n\n\t\tcancel_delayed_work_sync(&ar->scan.timeout);",
    "\t\tath12k_info(ar->ab, \"NWDIAG scan abort cancel_hw_scan vdev %u jiffies %lu\\n\",\n"
    "\t\t\t    arvif->vdev_id, jiffies);\n"
    "\t\tath12k_scan_abort(ar);\n\n\t\tcancel_delayed_work_sync(&ar->scan.timeout);",
    "cancel_hw_scan",
)

replace(
    "\tif (ar->scan.arvif == arvif && ar->scan.state == ATH12K_SCAN_RUNNING) {\n"
    "\t\tath12k_scan_abort(ar);",
    "\tif (ar->scan.arvif == arvif && ar->scan.state == ATH12K_SCAN_RUNNING) {\n"
    "\t\tath12k_info(ar->ab, \"NWDIAG scan abort unassign vdev %u jiffies %lu\\n\",\n"
    "\t\t\t    arvif->vdev_id, jiffies);\n"
    "\t\tath12k_scan_abort(ar);",
    "unassign_vif_chanctx",
)

path.write_text(s, encoding="utf-8")
print("scan-abort-diag: patched deps/linux ath12k/mac.c")
