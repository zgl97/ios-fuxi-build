#!/usr/bin/env python3
"""r612 (2/4): widen the DP RX funnel probe from r611.

r611's block only fired for a non-beacon management frame whose decap type was
NATIVE_WIFI, and its heartbeat needed 1024 frames before it said anything -- in
a round with a handful of frames that is silent whether the funnel ran ten
times or zero times.  This replacement prints:

  * the first eight delivered MSDUs unconditionally, with decap type, frame
    control (when the decap really is native-wifi), length, peer id and the
    multicast flag -- i.e. "does the firmware use this door at all";
  * every later management frame regardless of decap type, because a translated
    mgmt frame would show up as ETHERNET2_DIX and r611 would have dropped it;
  * a heartbeat every 64 frames instead of 1024.

Runs after dp-rx-fc-log.py; idempotent via the NWDIAG rxf marker.
"""

import sys
import pathlib

MARK = "NWDIAG rxf"

ANCHOR = "\tbool is_eapol = rxcb->is_eapol;\n"

PROBE = (
    "\t{\n"
    "\t\tstatic u32 dbg_fn;\n"
    "\t\tu16 dbg_ffc = 0;\n"
    "\t\tbool dbg_fmgmt = false;\n"
    "\n"
    "\t\tdbg_fn++;\n"
    "\t\tif (decap == DP_RX_DECAP_TYPE_NATIVE_WIFI && msdu->len >= 24) {\n"
    "\t\t\tconst struct ieee80211_hdr *fh =\n"
    "\t\t\t\t(const struct ieee80211_hdr *)msdu->data;\n"
    "\n"
    "\t\t\tdbg_ffc = le16_to_cpu(fh->frame_control);\n"
    "\t\t\tdbg_fmgmt = !(dbg_ffc & 0x000c);\n"
    "\t\t}\n"
    "\n"
    "\t\tif (dbg_fn <= 8)\n"
    "\t\t\tath12k_info(dp->ab, \"" + MARK + " n %u decap %u fc 0x%04x"
    " len %u peer 0x%x mcbc %u\\n\",\n"
    "\t\t\t\t    dbg_fn, decap, dbg_ffc, msdu->len,\n"
    "\t\t\t\t    rx_info->peer_id, is_mcbc);\n"
    "\t\telse if (dbg_fmgmt)\n"
    "\t\t\tath12k_info(dp->ab, \"" + MARK + " mgmt n %u decap %u"
    " fc 0x%04x len %u peer 0x%x\\n\",\n"
    "\t\t\t\t    dbg_fn, decap, dbg_ffc, msdu->len,\n"
    "\t\t\t\t    rx_info->peer_id);\n"
    "\n"
    "\t\tif (!(dbg_fn & 63))\n"
    "\t\t\tath12k_info(dp->ab, \"" + MARK + " beat n %u lastfc 0x%04x"
    " decap %u len %u mcbc %u\\n\",\n"
    "\t\t\t\t    dbg_fn, dbg_ffc, decap, msdu->len, is_mcbc);\n"
    "\t}\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: dp-rx-funnel-log.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/dp_rx.c")
    src = path.read_text()

    if MARK in src:
        print("dp-rx-funnel-log: already patched")
        return

    if src.count(ANCHOR) != 1:
        raise SystemExit("dp-rx-funnel-log: deliver_msdu anchor not unique "
                         f"(count={src.count(ANCHOR)})")

    path.write_text(src.replace(ANCHOR, ANCHOR + PROBE))
    print("dp-rx-funnel-log: patched ath12k/dp_rx.c (widened funnel probe)")


if __name__ == "__main__":
    main()
