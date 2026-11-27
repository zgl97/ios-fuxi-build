#!/usr/bin/env python3
"""r607: push the AUTH frame through the DP/TCL path instead of WMI_MGMT_TX.

Why: since r599 every AUTH frame leaves the host as WMI_MGMT_TX_SEND_CMDID
(ret 0) and the firmware never answers -- no WMI_MGMT_TX_COMPLETION_EVENTID
(0x7006), no transmit on air, and mac80211 times the authentication out after
3 s.  We have already ruled out frame content (r600 hex dump), alignment
(r601), vdev/peer state (r602), channel (r605), the TLV header (r604) and
cached DMA reads (r603), so instead of waiting on the firmware we simply hand
the frame to the data path, which is known good (peer 2 is already mapped in
the AST by sta_add).  TCL encap is HAL_TCL_ENCAP_TYPE_NATIVE_WIFI for 802.11
frames, so the AUTH frame travels as-is.

Runs after mgmt-tx-align.py; idempotent via the NWDIAG mgmt dp auth marker.
"""

import sys
import pathlib

MARK = "NWDIAG mgmt dp auth ret"

ANCHOR = "\t\tret = ath12k_mac_mgmt_tx(ar, skb, is_prb_rsp);\n"

PROBE = (
    "\t\tif (ieee80211_is_auth(hdr->frame_control)) {\n"
    "\t\t\tstruct ath12k_pdev_dp *mg_pdev;\n"
    "\n"
    "\t\t\tmg_pdev = ath12k_dp_to_pdev_dp(ar->ab->dp, ar->pdev_idx);\n"
    "\t\t\tif (mg_pdev) {\n"
    "\t\t\t\tint dret;\n"
    "\n"
    "\t\t\t\tdret = ath12k_wifi7_dp_tx(mg_pdev, arvif, skb,\n"
    "\t\t\t\t\t\t\t  false, 0, false);\n"
    "\t\t\t\tath12k_info(ar->ab, \"" + MARK + " %d\\n\", dret);\n"
    "\t\t\t\tif (!dret)\n"
    "\t\t\t\t\treturn;\n"
    "\t\t\t} else {\n"
    "\t\t\t\tath12k_info(ar->ab, \"NWDIAG mgmt dp auth no pdev\\n\");\n"
    "\t\t\t}\n"
    "\t\t}\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-tx-via-dp.py <Ath12kWifi-root>")

    path = pathlib.Path(sys.argv[1]) / "deps/linux/drivers/net/wireless/ath/ath12k/wifi7/hw.c"
    src = path.read_text()

    if MARK in src:
        print("mgmt-tx-via-dp: already patched")
        return

    if src.count(ANCHOR) != 1:
        raise SystemExit("mgmt-tx-via-dp: mgmt tx anchor not unique")

    src = src.replace(ANCHOR, PROBE + ANCHOR)
    path.write_text(src)
    print("mgmt-tx-via-dp: patched ath12k/wifi7/hw.c (auth via dp/tcl)")


if __name__ == "__main__":
    main()
