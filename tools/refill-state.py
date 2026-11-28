#!/usr/bin/env python3
"""r613: does the firmware ever consume RX buffers?

The RXDMA_BUF refill ring is the other half of the picture.  The driver posts
buffer addresses (its head pointer, hp); the firmware consumes them and writes
its own tail pointer into device memory (*tp_addr).  If that tail never advances
the firmware never read a single buffer, so nothing can arrive no matter what
filter is installed -- which would explain "ring polled, zero MSDUs" without any
filtering at all.

Print on every replenish call (throttled to the first 5 and then every 256):
ring id, bufs_max, how many slots were free, the driver hp, and the firmware tp
read straight out of the shared hal_srng memory.
"""

import sys
import pathlib

MARK = "NWDIAG refill"

ANCHOR = "\tnum_free = ath12k_hal_srng_src_num_free(ab, srng, true);\n"

BLOCK = (
    "\t{\n"
    "\t\tstatic u32 dbg_rf;\n"
    "\t\tu32 dbg_hw_tp = *srng->u.src_ring.tp_addr;\n"
    "\n"
    "\t\tdbg_rf++;\n"
    "\t\tif (dbg_rf <= 5 || !(dbg_rf & 255))\n"
    "\t\t\tath12k_info(ab,\n"
    '\t\t\t\t    "NWDIAG refill n %u ring %u max %u num_free %d hp %u hwtp %u req %d\\n",\n'
    "\t\t\t\t    dbg_rf, rx_ring->refill_buf_ring.ring_id,\n"
    "\t\t\t\t    rx_ring->bufs_max, num_free,\n"
    "\t\t\t\t    srng->u.src_ring.hp, dbg_hw_tp, req_entries);\n"
    "\t}\n"
)


def ath12k_dir(dest):
    """Accept either the tree root or the ath12k directory itself."""
    p = pathlib.Path(dest)
    if (p / "dp_rx.c").exists():
        return p
    cand = p / "deps/linux/drivers/net/wireless/ath/ath12k"
    if (cand / "dp_rx.c").exists():
        return cand
    raise SystemExit(f"refill-state: no ath12k sources under {dest}")


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else "."
    path = ath12k_dir(dest) / "dp_rx.c"

    data = path.read_bytes()
    if MARK.encode() in data:
        print("refill-state: already patched")
        return

    anchor = ANCHOR.encode()
    count = data.count(anchor)
    if count != 1:
        raise SystemExit(f"refill-state: anchor not unique (count={count})")

    path.write_bytes(data.replace(anchor, anchor + BLOCK.encode()))
    print("refill-state: patched ath12k/dp_rx.c (refill ring state)")


if __name__ == "__main__":
    main()
