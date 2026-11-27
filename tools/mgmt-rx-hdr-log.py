#!/usr/bin/env python3
"""r610: give the management rx line the header fields that identify a reply.

r586 prints pdev / channel / freq / status / len for each management frame the
firmware hands back, which is enough to count beacons and probe responses but
not enough to recognise an authentication response: every line from the scan
phase reads `len 292` or `len 367`, and the only frames that interest us now are
the ones with frame control 0x00b0.

Print the frame control and the three addresses next to the existing fields so
a reply to our AUTH frame is unmistakable, even when it is dropped later in the
path (an auth response carries our own MAC as addr1 and the AP as addr2).

Runs after mgmt-rx-log.py; idempotent via NWDIAG mgmt rx hdr.
"""

import sys
import pathlib

MARK = "NWDIAG mgmt rx hdr"

ANCHOR = (
    "\tath12k_info(ab, \"NWDIAG mgmt rx pdev %u chan %u freq %u status 0x%x len %u jiffies %lu\\n\",\n"
    "\t\t    rx_ev.pdev_id, rx_ev.channel, rx_ev.chan_freq, rx_ev.status,\n"
    "\t\t    skb->len, jiffies);\n"
)

PROBE = (
    "\tif (skb->len >= 24)\n"
    "\t\tath12k_info(ab,\n"
    "\t\t\t    \"" + MARK + " fc 0x%04x a1 %pM a2 %pM a3 %pM seq 0x%04x len %u\\n\",\n"
    "\t\t\t    le16_to_cpu(((struct ieee80211_hdr *)skb->data)->frame_control),\n"
    "\t\t\t    ((struct ieee80211_hdr *)skb->data)->addr1,\n"
    "\t\t\t    ((struct ieee80211_hdr *)skb->data)->addr2,\n"
    "\t\t\t    ((struct ieee80211_hdr *)skb->data)->addr3,\n"
    "\t\t\t    le16_to_cpu(((struct ieee80211_hdr *)skb->data)->seq_ctrl),\n"
    "\t\t\t    skb->len);\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-rx-hdr-log.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c")
    src = path.read_text()

    if MARK in src:
        print("mgmt-rx-hdr-log: already patched")
        return

    if src.count(ANCHOR) != 1:
        raise SystemExit("mgmt-rx-hdr-log: mgmt rx anchor not unique")

    src = src.replace(ANCHOR, ANCHOR + PROBE)
    path.write_text(src)
    print("mgmt-rx-hdr-log: patched ath12k/wmi.c (mgmt rx header)")


if __name__ == "__main__":
    main()
