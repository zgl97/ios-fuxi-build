#!/usr/bin/env python3
"""r611: watch the DP RX path for any management frame that reaches the host.

r610 instrumented exactly one delivery path -- the WMI mgmt rx event in
ath12k_mgmt_rx_event() -- and that path carried 11 beacons and no auth
response.  That is not conclusive, because ath12k has a second, independent
way to push a received MPDU up the stack: the DP RX ring feeds
ath12k_dp_rx_deliver_msdu(), which hands the skb straight to
ieee80211_rx_napi() without going through a WMI event at all.

So r611 adds a probe at that funnel and separates the two possible worlds:

  * a management frame shows up here  -> the firmware did deliver it and
    something above the DP path (mac80211 or the port's shim) drops it;
  * nothing shows up here either      -> the firmware itself discarded the
    auth response before it ever reached the host, and the next lever is the
    vdev RX filter / peer state, not the host.

Beacons are skipped on purpose (they arrive at ~10/s and would swamp the
console); a heartbeat every 1024 received msdus proves the probe is alive and
shows what class of frames is flowing.

Runs after mgmt-rx-hdr-log.py; idempotent via NWDIAG dp rx.
"""

import sys
import pathlib

MARK = "NWDIAG dp rx"

ANCHOR = (
    "\tpeer = ath12k_dp_peer_find_by_peerid(dp_pdev, rx_info->peer_id);\n"
    "\n"
    "\tpubsta = peer ? peer->sta : NULL;\n"
)

PROBE = (
    "\n"
    "\t{\n"
    "\t\tstatic u32 dbg_rxn, dbg_rxm;\n"
    "\t\tu16 dbg_fc = 0;\n"
    "\n"
    "\t\tdbg_rxn++;\n"
    "\t\tif (decap == DP_RX_DECAP_TYPE_NATIVE_WIFI && msdu->len >= 24) {\n"
    "\t\t\tconst struct ieee80211_hdr *dh =\n"
    "\t\t\t\t(const struct ieee80211_hdr *)msdu->data;\n"
    "\n"
    "\t\t\tdbg_fc = le16_to_cpu(dh->frame_control);\n"
    "\t\t\t/* type 0 = mgmt; subtype 8 = beacon, skip to keep the log sane */\n"
    "\t\t\tif (!(dbg_fc & 0x000c) && ((dbg_fc & 0x00f0) != 0x0080)) {\n"
    "\t\t\t\tdbg_rxm++;\n"
    "\t\t\t\tath12k_info(dp->ab,\n"
    "\t\t\t\t\t    \"" + MARK + " mgmt fc 0x%04x seq 0x%04x len %u"
    " peer 0x%x decap %u"
    " a1 %02x:%02x:%02x:%02x:%02x:%02x"
    " a2 %02x:%02x:%02x:%02x:%02x:%02x\\n\",\n"
    "\t\t\t\t\t    dbg_fc, le16_to_cpu(dh->seq_ctrl), msdu->len,\n"
    "\t\t\t\t\t    rx_info->peer_id, decap,\n"
    "\t\t\t\t\t    dh->addr1[0], dh->addr1[1], dh->addr1[2],\n"
    "\t\t\t\t\t    dh->addr1[3], dh->addr1[4], dh->addr1[5],\n"
    "\t\t\t\t\t    dh->addr2[0], dh->addr2[1], dh->addr2[2],\n"
    "\t\t\t\t\t    dh->addr2[3], dh->addr2[4], dh->addr2[5]);\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\n"
    "\t\tif (!(dbg_rxn & 1023))\n"
    "\t\t\tath12k_info(dp->ab,\n"
    "\t\t\t\t    \"" + MARK + " beat n %u mgmt %u mcbc %u"
    " lastfc 0x%04x decap %u len %u\\n\",\n"
    "\t\t\t\t    dbg_rxn, dbg_rxm, is_mcbc, dbg_fc, decap, msdu->len);\n"
    "\t}\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: dp-rx-fc-log.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/dp_rx.c")
    src = path.read_text()

    if MARK in src:
        print("dp-rx-fc-log: already patched")
        return

    if src.count(ANCHOR) != 1:
        raise SystemExit("dp-rx-fc-log: deliver_msdu anchor not unique "
                         f"(count={src.count(ANCHOR)})")

    path.write_text(src.replace(ANCHOR, ANCHOR + PROBE))
    print("dp-rx-fc-log: patched ath12k/dp_rx.c (deliver_msdu probe)")


if __name__ == "__main__":
    main()
