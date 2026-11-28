#!/usr/bin/env python3
"""r617: what addresses does the host hand to RXDMA?

r616 killed the refill hypothesis: replenish() posts 511 buffers (num_free 511 >
bufs_max*3/4), firmware's tail then advances by 286 and stops there forever,
while the REO destination rings stay at zero and not one MSDU reaches the host.
So buffers do get consumed.  Two things can still be wrong at that point:

  * the address inside each buffer descriptor is not an address the device can
    write to (the port advertises narrow DMA/IOVA windows -- "L1 DMA window
    published IOVA ... pages 4" -- and 511 buffers of DP_RX_BUFFER_SIZE cannot
    all live in a 4-page window);
  * the REO ring itself is set up with a stale/zero base or HP address, in which
    case firmware has nowhere to report completion.

This probe answers both from the host side: dump the first four buffer addresses
as they go into the ring, then a min/max summary of everything posted, and let
reo-ring-state print each ring's base and hardware HP address.
"""

import sys
import pathlib

MARK = "NWDIAG rx buf"

ANCHOR = (
    "\t\tath12k_hal_rx_buf_addr_info_set(dp->hal, desc, paddr, cookie,\n"
    "\t\t\t\t\t\tmgr);\n"
)

BLOCK = (
    "\t\t{\n"
    "\t\t\tstatic u32 dbg_buf;\n"
    "\t\t\tstatic unsigned long long dbg_lo, dbg_hi;\n"
    "\t\t\tunsigned long long dbg_pa = (unsigned long long)paddr;\n"
    "\n"
    "\t\t\tdbg_buf++;\n"
    "\t\t\tif (!dbg_lo || dbg_pa < dbg_lo)\n"
    "\t\t\t\tdbg_lo = dbg_pa;\n"
    "\t\t\tif (dbg_pa + skb->len + skb_tailroom(skb) > dbg_hi)\n"
    "\t\t\t\tdbg_hi = dbg_pa + skb->len + skb_tailroom(skb);\n"
    "\t\t\tif (dbg_buf <= 4)\n"
    '\t\t\t\tath12k_info(ab, "NWDIAG rx buf n %u paddr 0x%llx len %u cookie 0x%x\\n",\n'
    "\t\t\t\t\t    dbg_buf, dbg_pa, skb->len + skb_tailroom(skb),\n"
    "\t\t\t\t\t    cookie);\n"
    "\t\t\telse if (!(dbg_buf & 255))\n"
    '\t\t\t\tath12k_info(ab, "NWDIAG rx buf span n %u lo 0x%llx hi 0x%llx\\n",\n'
    "\t\t\t\t\t    dbg_buf, dbg_lo, dbg_hi);\n"
    "\t\t}\n"
)

NEW = BLOCK + ANCHOR


def ath12k_dir(dest):
    p = pathlib.Path(dest)
    if (p / "dp_rx.c").exists():
        return p
    cand = p / "deps/linux/drivers/net/wireless/ath/ath12k"
    if (cand / "dp_rx.c").exists():
        return cand
    raise SystemExit(f"rx-buf-addr-log: no ath12k sources under {dest}")


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else "."
    path = ath12k_dir(dest) / "dp_rx.c"

    data = path.read_bytes()
    if MARK.encode() in data:
        print("rx-buf-addr-log: already patched")
        return

    anchor = ANCHOR.encode()
    count = data.count(anchor)
    if count != 1:
        raise SystemExit(f"rx-buf-addr-log: anchor not unique (count={count})")

    path.write_bytes(data.replace(anchor, NEW.encode()))
    print("rx-buf-addr-log: patched ath12k/dp_rx.c (buffer addresses)")


if __name__ == "__main__":
    main()
