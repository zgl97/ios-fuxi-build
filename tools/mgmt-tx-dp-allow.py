#!/usr/bin/env python3
"""r608: let a management frame into the dp/tcl path.

r607 handed the AUTH frame to ath12k_wifi7_dp_tx() and got -EOPNOTSUPP, which
is the guard at the top of that function:

    if (!(info->flags & IEEE80211_TX_CTL_HW_80211_ENCAP) &&
        !ieee80211_is_data(hdr->frame_control))
        return -EOPNOTSUPP;

Management frames were never meant to go this way (they use WMI_MGMT_TX), but
that path is dead on this firmware: the command is accepted (ret 0) and then
neither transmitted nor completed.  The TCL path is known good -- the AP peer
is already in the AST (dppeer map vdev 0 peer 2 ast 5) -- and NATIVE_WIFI
encap passes a raw 802.11 frame through unchanged, so allow mgmt frames here
and let the r607 caller decide when to use it.

Runs after mgmt-tx-via-dp.py; idempotent via NWDIAG mgmt dp allow.
"""

import sys
import pathlib

MARK = "NWDIAG mgmt dp allow"

OLD = (
    "\tif (!(info->flags & IEEE80211_TX_CTL_HW_80211_ENCAP) &&\n"
    "\t    !ieee80211_is_data(hdr->frame_control))\n"
    "\t\treturn -EOPNOTSUPP;\n"
)

NEW = (
    "\tif (!(info->flags & IEEE80211_TX_CTL_HW_80211_ENCAP) &&\n"
    "\t    !ieee80211_is_data(hdr->frame_control) &&\n"
    "\t    !ieee80211_is_mgmt(hdr->frame_control))\n"
    "\t\treturn -EOPNOTSUPP;\n"
    "\n"
    "\tif (ieee80211_is_mgmt(hdr->frame_control))\n"
    "\t\tath12k_info(ab, \"" + MARK + " vdev %u len %u fc 0x%04x\\n\",\n"
    "\t\t\t    arvif->vdev_id, skb->len, hdr->frame_control);\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-tx-dp-allow.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/wifi7/dp_tx.c")
    src = path.read_text()

    if MARK in src:
        print("mgmt-tx-dp-allow: already patched")
        return

    if src.count(OLD) != 1:
        raise SystemExit("mgmt-tx-dp-allow: encap guard not unique")

    src = src.replace(OLD, NEW)
    path.write_text(src)
    print("mgmt-tx-dp-allow: patched ath12k/wifi7/dp_tx.c (mgmt allowed)")


if __name__ == "__main__":
    main()
