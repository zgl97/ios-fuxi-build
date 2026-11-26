#!/usr/bin/env python3
"""Never let a skipped scan-cancel wedge the scan state machine (r579).

`scan-stop-ok.py` makes ath12k_scan_stop() return 0 without sending the WMI
cancel, because this firmware never answers it.  r578 then hung silently:
ath12k_scan_abort() had already set ar->scan.state = ATH12K_SCAN_ABORTING, and
because the stop "succeeded" the tail of ath12k_scan_stop() skipped
__ath12k_mac_scan_finish(), so mac80211 was never told the scan ended, no
SCAN_ABORTED/SCAN_RESULTS ever reached wpa_supplicant, and the radio sat in
ABORTING forever.

Finish the scan locally in that branch.  __ath12k_mac_scan_finish() cancels the
timeout, completes ar->scan.completed and queues scan.vdev_clean_wk (whose
interface removal is already replaced by a log line in scan-finish-fast.py, so
en0 survives).  Run after scan-stop-ok.py.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
mac = root / "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"

text = mac.read_text()
old = ('\tath12k_info(ar->ab, "NWDIAG scan_stop ok pdev %u\\n",\n'
       '\t\t   arg.pdev_id);\n'
       '\tret = 0;\n')
new = ('\tath12k_info(ar->ab, "NWDIAG scan_stop ok pdev %u\\n",\n'
       '\t\t   arg.pdev_id);\n'
       '\tret = 0;\n'
       '\tath12k_info(ar->ab, "NWDIAG scan_finish local state %d\\n",\n'
       '\t\t   ar->scan.state);\n'
       '\tath12k_mac_scan_finish(ar);\n')

if text.count(old) != 1:
    raise SystemExit("scan-stop-finish: scan_stop ok block not unique (%d)"
                     % text.count(old))

mac.write_text(text.replace(old, new, 1), encoding="utf-8")
print("patched mac.c: scan_stop finishes the scan locally")
