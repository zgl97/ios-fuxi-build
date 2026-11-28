#!/usr/bin/env python3
"""r613: is the REO destination ring moving at all?

r612 answered the four questions but flipped the problem: the WiFi7 RX rings ARE
polled (150 polls, rings 0..3, budget 64) and yet ath12k_dp_rx_deliver_msdu()
never ran once -- no data-path MSDU reached the host in the whole boot, not even
a beacon (they all came up through WMI_MGMT_RX).  So the question is no longer
"who filtered the auth response" but "does the firmware ever write this ring".

Per poll print: entries reaped, the driver read pointer (tp), the cached and the
hardware write pointer (cached_hp / *hp_addr), and the two counters we already
keep (reo_rx and hal_reo_error).  tp advancing without reaping means we read
entries but rejected them; hp never moving means FW never wrote anything.

The probe has to sit BETWEEN the unlock and the !total_msdu_reaped branch,
because that branch jumps straight to exit: with zero MSDUs -- exactly the case
we are chasing.

Throttled: first 5 polls plus every 64th.
"""

import sys
import pathlib

MARK = "NWDIAG reo ring"

ANCHOR = (
    "\tspin_unlock_bh(&srng->lock);\n"
    "\n"
    "\tif (!total_msdu_reaped)\n"
)

BLOCK = (
    "\t{\n"
    "\t\tstatic u32 dbg_reo;\n"
    "\t\tu32 dbg_hw_hp = *srng->u.dst_ring.hp_addr;\n"
    "\n"
    "\t\tdbg_reo++;\n"
    "\t\tif (dbg_reo <= 5 || !(dbg_reo & 63))\n"
    "\t\t\tath12k_info(ab,\n"
    '\t\t\t\t    "NWDIAG reo ring n %u ring %d reaped %d tp %u chp %u hwhp %u reo_rx %u err %u\\n",\n'
    "\t\t\t\t    dbg_reo, ring_id, total_msdu_reaped,\n"
    "\t\t\t\t    srng->u.dst_ring.tp, srng->u.dst_ring.cached_hp,\n"
    "\t\t\t\t    dbg_hw_hp, dp->device_stats.reo_rx[ring_id][dp->device_id],\n"
    "\t\t\t\t    dp->device_stats.hal_reo_error[ring_id]);\n"
    "\t}\n"
)

NEW = (
    "\tspin_unlock_bh(&srng->lock);\n"
    "\n"
    + BLOCK
    + "\n"
    "\tif (!total_msdu_reaped)\n"
)


def ath12k_dir(dest):
    """Accept either the tree root or the ath12k directory itself."""
    p = pathlib.Path(dest)
    if (p / "wifi7" / "dp_rx.c").exists():
        return p
    cand = p / "deps/linux/drivers/net/wireless/ath/ath12k"
    if (cand / "wifi7" / "dp_rx.c").exists():
        return cand
    raise SystemExit(f"reo-ring-state: no ath12k sources under {dest}")


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else "."
    path = ath12k_dir(dest) / "wifi7" / "dp_rx.c"

    data = path.read_bytes()
    if MARK.encode() in data:
        print("reo-ring-state: already patched")
        return

    anchor = ANCHOR.encode()
    count = data.count(anchor)
    if count != 1:
        raise SystemExit(f"reo-ring-state: process anchor not unique (count={count})")

    path.write_bytes(data.replace(anchor, NEW.encode()))
    print("reo-ring-state: patched ath12k/wifi7/dp_rx.c (reo dst ring state)")


if __name__ == "__main__":
    main()
