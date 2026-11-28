#!/usr/bin/env python3
"""r612 (4/4): print the HTT RX ring TLV filter the driver actually installs.

`ath12k_dp_tx_htt_rx_filter_setup()` is the single place where the host tells
the firmware which frame types may be written into an RX ring
(struct htt_rx_ring_tlv_filter: rx_filter plus pkt_filter_flags0..3).  AUTH
lives in flags1 (dp_htt.h:628) and beacons in flags0 (:602), so printing the
values per ring answers, without guessing, whether the ring we receive on was
ever told to accept authentication frames -- and whether a monitor destination
ring (which does include everything) exists at all for our pdev.

dp_htt.c holds two functions with an identical local declaration block, so the
insertion point is found by anchoring on the function signature and taking the
first `int ret;` after it; that keeps the probe after the declarations.

Idempotent via the NWDIAG htt filter marker.
"""

import sys
import pathlib

MARK = "NWDIAG htt filter"

SIG = ("int ath12k_dp_tx_htt_rx_filter_setup(struct ath12k_base *ab, "
       "u32 ring_id,")

LAST_DECL = "\tint ret;\n"

PROBE = (
    "\n"
    "\t{\n"
    "\t\tstatic u32 dbg_hf;\n"
    "\n"
    "\t\tdbg_hf++;\n"
    "\t\tif (dbg_hf <= 24)\n"
    "\t\t\tath12k_info(ab, \"" + MARK + " n %u ring %u mac %d type %d"
    " rx_filter 0x%x f0 0x%x f1 0x%x f2 0x%x f3 0x%x\\n\",\n"
    "\t\t\t\t    dbg_hf, ring_id, mac_id, (int)ring_type,\n"
    "\t\t\t\t    tlv_filter->rx_filter,\n"
    "\t\t\t\t    tlv_filter->pkt_filter_flags0,\n"
    "\t\t\t\t    tlv_filter->pkt_filter_flags1,\n"
    "\t\t\t\t    tlv_filter->pkt_filter_flags2,\n"
    "\t\t\t\t    tlv_filter->pkt_filter_flags3);\n"
    "\t}\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: htt-filter-log.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/dp_htt.c")
    src = path.read_text()

    if MARK in src:
        print("htt-filter-log: already patched")
        return

    start = src.find(SIG)
    if start < 0 or src.find(SIG, start + 1) >= 0:
        raise SystemExit("htt-filter-log: filter-setup signature not unique")

    end = src.find(LAST_DECL, start)
    if end < 0:
        raise SystemExit("htt-filter-log: no 'int ret;' after the signature")
    end += len(LAST_DECL)

    path.write_text(src[:end] + PROBE + src[end:])
    print("htt-filter-log: patched ath12k/dp_htt.c (rx tlv filter values)")


if __name__ == "__main__":
    main()
