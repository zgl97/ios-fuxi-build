#!/usr/bin/env python3
"""r612 (3/4): stop swallowing WMI_MGMT_RX_FW_CONSUMED_EVENTID.

Stock ath12k lists this event with the "frequent, hence silently ignore"
events (wmi.c), which is right for a healthy association: it means the firmware
consumed a management frame itself instead of handing it up.  In our round the
auth response never arrives, and this event is the firmware's only channel for
saying "I saw a management frame and dealt with it" -- so counting it turns a
silent drop into evidence, including whether it carries the frame (length).

The case shares its body with WMI_PDEV_GET_HALPHY_CAL_STATUS_EVENTID, so the
patch splits them: HALPHY_CAL keeps an empty body, this one gets a counter.

Idempotent via the NWDIAG fw consumed marker.
"""

import sys
import pathlib

MARK = "NWDIAG fw consumed"

ANCHOR = (
    "\t/* add Unsupported events (frequent) here */\n"
    "\tcase WMI_PDEV_GET_HALPHY_CAL_STATUS_EVENTID:\n"
    "\tcase WMI_MGMT_RX_FW_CONSUMED_EVENTID:\n"
    "\t\t/* debug might flood hence silently ignore (no-op) */\n"
    "\t\tbreak;\n"
)

REPLACE = (
    "\t/* add Unsupported events (frequent) here */\n"
    "\tcase WMI_PDEV_GET_HALPHY_CAL_STATUS_EVENTID:\n"
    "\t\tbreak;\n"
    "\tcase WMI_MGMT_RX_FW_CONSUMED_EVENTID: {\n"
    "\t\tstatic u32 dbg_fwc;\n"
    "\n"
    "\t\tdbg_fwc++;\n"
    "\t\tif (dbg_fwc <= 8 || !(dbg_fwc & 63))\n"
    "\t\t\tath12k_info(ab, \"" + MARK + " n %u len %u\\n\",\n"
    "\t\t\t\t    dbg_fwc, skb->len);\n"
    "\t\tbreak;\n"
    "\t}\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-consumed-log.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c")
    src = path.read_text()

    if MARK in src:
        print("mgmt-consumed-log: already patched")
        return

    if src.count(ANCHOR) != 1:
        raise SystemExit("mgmt-consumed-log: event-switch anchor not unique "
                         f"(count={src.count(ANCHOR)})")

    path.write_text(src.replace(ANCHOR, REPLACE))
    print("mgmt-consumed-log: patched ath12k/wmi.c (fw-consumed event)")


if __name__ == "__main__":
    main()
