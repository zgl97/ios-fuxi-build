#!/usr/bin/env python3
"""Disable HTC TX credit flow on the WMI endpoints.

The target never credits the WMI endpoint in the L1 path, so the first WMI
command after init blocks on an empty credit pool.  The firmware accepts WMI
commands without the host-side credit accounting; keep the endpoint flowing.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "deps/linux/drivers/net/wireless/ath/ath12k/htc.c"
text = path.read_text()
needle = "\tep->tx_credits = tx_alloc;\n"
insert = (
    needle
    + "\n"
    + "\tath12k_info(ab, \"htc: service %u tx_alloc %u eid %u\\n\",\n"
    + "\t\t    conn_req->service_id, tx_alloc, assigned_eid);\n"
    + "\tif (conn_req->service_id == ATH12K_HTC_SVC_ID_WMI_CONTROL ||\n"
    + "\t    conn_req->service_id == ATH12K_HTC_SVC_ID_WMI_CONTROL_MAC1 ||\n"
    + "\t    conn_req->service_id == ATH12K_HTC_SVC_ID_WMI_CONTROL_MAC2)\n"
    + "\t\tep->tx_credit_flow_enabled = false;\n"
    + "\n"
)
if insert in text:
    print("htc-no-wmi-credits: already applied")
    raise SystemExit(0)
count = text.count(needle)
if count != 1:
    raise SystemExit(f"htc-no-wmi-credits: expected 1 insertion point, found {count}")
path.write_text(text.replace(needle, insert, 1))
print("htc-no-wmi-credits: applied")
