#!/usr/bin/env python3
"""Do not rewrite the scan channel list while a scan is in progress.

A late regulatory update issues a second WMI_SCAN_CHAN_LIST after
WMI_SCAN_START, which cancels the running scan on this firmware.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "deps/linux/drivers/net/wireless/ath/ath12k/reg.c"
s = path.read_text(encoding="utf-8")

old = """\tif (ar->ah->state == ATH12K_HW_STATE_RESTARTING)
\t\treturn 0;

\tbands = hw->wiphy->bands;
"""
new = """\tif (ar->ah->state == ATH12K_HW_STATE_RESTARTING)
\t\treturn 0;

\tif (ar->scan.state != ATH12K_SCAN_IDLE) {
\t\tath12k_info(ar->ab,
\t\t\t    "NWDIAG skip scan chan list in state %s\\n",
\t\t\t    ath12k_scan_state_str(ar->scan.state));
\t\treturn 0;
\t}

\tbands = hw->wiphy->bands;
"""
if old not in s:
    raise SystemExit("reg_update_chan_list head not found")
s = s.replace(old, new, 1)
path.write_text(s, encoding="utf-8")
print("reg-scan-chan-list-guard: patched reg.c")
