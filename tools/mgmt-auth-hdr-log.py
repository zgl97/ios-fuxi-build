#!/usr/bin/env python3
"""r610: print the 802.11 header of the AUTH frame just before it enters TCL.

r609 proves the TCL path works: the firmware consumed the entry and released it
with HAL_WBM_TQM_REL_REASON_FRAME_ACKED, yet the AP never answered and the host
never saw an auth response.  Two things can still be wrong before the frame
leaves the host, and both live in the header:

  * addr1 must be the AP (a4:39:b3:ca:42:7c) or the frame is addressed to
    nobody on air;
  * addr2 must be the MAC the firmware has registered for this vdev -- the AP
    sends its response to addr2, and the firmware's RX address filter drops
    anything that is not addressed to its own vdev MAC.  wpa_supplicant reports
    `Own MAC address: 02:11:22:33:44:55`, so the two values can be compared
    directly once this line is in the log.

seq_ctrl is printed as well: a retransmission counter that stays at 0 across
the three attempts points at a frame that the firmware builds rather than one
mac80211 hands down intact.

Runs after mgmt-tx-via-dp.py; idempotent via NWDIAG mgmt frame.
"""

import sys
import pathlib

MARK = "NWDIAG mgmt frame"

ANCHOR = "\t\t\t\tdret = ath12k_wifi7_dp_tx(mg_pdev, arvif, skb,\n"

PROBE = (
    "\t\t\t\tath12k_info(ar->ab,\n"
    "\t\t\t\t\t    \"" + MARK + " fc 0x%04x a1 %pM a2 %pM a3 %pM seq 0x%04x len %u\\n\",\n"
    "\t\t\t\t\t    le16_to_cpu(hdr->frame_control), hdr->addr1,\n"
    "\t\t\t\t\t    hdr->addr2, hdr->addr3,\n"
    "\t\t\t\t\t    le16_to_cpu(hdr->seq_ctrl), skb->len);\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-auth-hdr-log.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/wifi7/hw.c")
    src = path.read_text()

    if MARK in src:
        print("mgmt-auth-hdr-log: already patched")
        return

    if src.count(ANCHOR) != 1:
        raise SystemExit("mgmt-auth-hdr-log: dp tx anchor not unique")

    src = src.replace(ANCHOR, PROBE + ANCHOR)
    path.write_text(src)
    print("mgmt-auth-hdr-log: patched ath12k/wifi7/hw.c (auth header)")


if __name__ == "__main__":
    main()
