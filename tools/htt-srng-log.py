#!/usr/bin/env python3
"""Print every ring the driver hands to the firmware via HTT srng setup.

The refill ring is the only thing that makes RXDMA fetch buffers, and the
firmware learns about it from this command alone. So print, for each ring,
its type, id, MAC id, entry count and the addresses the firmware is told:
if the ring base or the HP/TP addresses differ from what the host actually
programmed, the firmware fetches buffers from nowhere and the receive path
stays silent while every host side check keeps passing.

The caller (ath12k_dp_rx_htt_setup) only proceeds when this returns 0, and the
pdev alloc probes prove it did, so the return value is not the question: the
addresses are.
"""
import sys
from pathlib import Path

MARK = "NWDIAG htt srng"
ANCHOR_LF = "\ttp_addr = ath12k_hal_srng_get_tp_addr(ab, srng);\n"

BLOCK = """\t/* r630: what exactly are we telling the firmware about this ring? */
\tath12k_info(ab, "NWDIAG htt srng type %d id %u mac %d n %u base 0x%llx\\n",
\t\t    (int)ring_type, ring_id, mac_id, params.num_entries,
\t\t    (unsigned long long)params.ring_base_paddr);
\tath12k_info(ab, "NWDIAG htt srng addr hp 0x%llx tp 0x%llx\\n",
\t\t    (unsigned long long)hp_addr,
\t\t    (unsigned long long)tp_addr);
"""


def ath12k_dir(dest):
    root = Path(dest)
    for cand in (root, root / "ath12k",
                 root / "deps/linux/drivers/net/wireless/ath/ath12k",
                 root / "drivers/net/wireless/ath/ath12k"):
        if (cand / "dp_htt.c").is_file():
            return cand
    raise SystemExit("htt-srng-log: cannot find ath12k under %s" % dest)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: htt-srng-log.py <kernel tree>")
    path = ath12k_dir(sys.argv[1]) / "dp_htt.c"
    src = path.read_bytes().decode()
    if MARK in src:
        print("skip dp_htt.c (already patched)")
        return
    eol = "\r\n" if "\r\n" in src else "\n"
    anchor = ANCHOR_LF.replace("\n", eol)
    if src.count(anchor) != 1:
        raise SystemExit("htt-srng-log: anchor count=%d, refusing"
                         % src.count(anchor))
    block = BLOCK.replace("\n", eol)
    path.write_bytes(src.replace(anchor, anchor + block).encode())
    print("ok htt-srng-log: patched dp_htt.c")


if __name__ == "__main__":
    main()
