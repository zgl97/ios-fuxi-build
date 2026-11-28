#!/usr/bin/env python3
"""Watch the peer come up on the receive side.

REO only delivers a peer's frames to a ring if the peer was told which ring to
use, and that happens exactly once, in ath12k_dp_peer_setup(): one default
routing command (reo_dest = mac_id + 1) followed by a reorder queue per TID.
Nothing else names that mapping, and the receive probes show no frame ever
reaches a destination ring, so print each of those commands and its result.

  NWDIAG peer route  the default routing command and its reo_dest
  NWDIAG peer tid    the per-TID reorder queue loop's last result
  NWDIAG peer frag   the defrag context result
If routing fails the peer simply has no destination and every RX ring stays
empty no matter how often the host re-arms it.
"""
import sys
from pathlib import Path

MARK = "NWDIAG peer route"

ANCHORS = [
    # after printing we know which ring the peer was routed to and whether the
    # command was accepted
    ("\t\t\t\t\tDP_RX_HASH_ENABLE | (reo_dest << 1));\n",
     """\tath12k_info(ab, "NWDIAG peer route reo %u ret %d\\n", reo_dest, ret);
"""),
    # before the defrag call, so the value printed is the reorder queue loop's
    # result and not the defrag one
    ("\tret = ath12k_dp_rx_peer_frag_setup(ar, addr, vdev_id);\n",
     """\tath12k_info(ab, "NWDIAG peer tid last ret %d\\n", ret);
"""),
]


def ath12k_dir(dest):
    root = Path(dest)
    for cand in (root, root / "ath12k",
                 root / "deps/linux/drivers/net/wireless/ath/ath12k",
                 root / "drivers/net/wireless/ath/ath12k"):
        if (cand / "dp.c").is_file():
            return cand
    raise SystemExit("peer-reo-log: cannot find ath12k under %s" % dest)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: peer-reo-log.py <kernel tree>")
    path = ath12k_dir(sys.argv[1]) / "dp.c"
    src = path.read_bytes().decode()
    if MARK in src:
        print("skip dp.c (already patched)")
        return
    eol = "\r\n" if "\r\n" in src else "\n"
    out = src
    for i, (anchor, block) in enumerate(ANCHORS):
        a = anchor.replace("\n", eol)
        b = block.replace("\n", eol)
        if out.count(a) != 1:
            raise SystemExit("peer-reo-log: anchor count=%d, refusing"
                             % out.count(a))
        # the second probe must run before the defrag call that overwrites ret
        out = out.replace(a, (b + a) if i else (a + b))
    path.write_bytes(out.encode())
    print("ok peer-reo-log: patched dp.c")


if __name__ == "__main__":
    main()
