#!/usr/bin/env python3
"""r625: arm the REO destination rings again, after the firmware is up.

r624's bank dump shows the ring-bank window is alive -- deadc0de poison in the
holes, real values in the implemented registers -- and ring 0 still holds the
base we wrote while rings 1..7 hold zero.  All eight were armed before
ath12k_wmi_cmd_init(), and the pre-init reads that r620/r623 tried came back
0xffffffff: this REO block only answers once the firmware owns it.

So arm them again from ath12k_dp_rx_pdev_alloc(), which runs after unified
ready, and read each base register straight back.  If the registers now hold
what we wrote, the rings are live and the question moves to whether the chip
writes descriptors.  If they still read zero, arming is not the problem and the
REO block wants something else before it accepts a ring.
"""

import sys
from pathlib import Path

MARK = "NWDIAG reo rearm"

ANCHOR = "\tnwdiag_reo_mem_dump(ab);\n"

BLOCK_LINES = [
    "\t{",
    "\t\tstruct ath12k_dp *dbg_dp = ath12k_ab_to_dp(ab);",
    "\t\tint dbg_i;",
    "",
    "\t\tfor (dbg_i = 0; dbg_i < DP_REO_DST_RING_MAX; dbg_i++) {",
    "\t\t\tstruct hal_srng *dbg_s =",
    "\t\t\t\t&ab->hal.srng_list[dbg_dp->reo_dst_ring[dbg_i].ring_id];",
    "\t\t\tu32 dbg_rb = dbg_s->hwreg_base[HAL_SRNG_REG_GRP_R0];",
    "",
    "\t\t\tab->hal.ops->srng_dst_hw_init(ab, dbg_s);",
    "\t\t\tath12k_info(ab, \"NWDIAG reo rearm %d id %u base 0x%x msb 0x%x\\n\",",
    "\t\t\t\t    dbg_i, dbg_s->ring_id,",
    "\t\t\t\t    ath12k_hif_read32(ab, dbg_rb),",
    "\t\t\t\t    ath12k_hif_read32(ab, dbg_rb + 4));",
    "\t\t}",
    "\t}",
]


def block():
    return "\n".join(BLOCK_LINES) + "\n"


def ath12k_dir(dest):
    dest = Path(dest)
    for cand in (
        dest,
        dest / "ath12k",
        dest / "deps/linux/drivers/net/wireless/ath/ath12k",
        dest / "drivers/net/wireless/ath/ath12k",
    ):
        if (cand / "dp_rx.c").is_file():
            return cand
    raise SystemExit(f"reo-ring-rearm: no ath12k source under {dest}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: reo-ring-rearm.py <tree>")
    dp_rx = ath12k_dir(sys.argv[1]) / "dp_rx.c"
    src = dp_rx.read_text()

    if MARK in src:
        print(f"reo-ring-rearm: already applied to {dp_rx}")
        return

    count = src.count(ANCHOR)
    if count != 1:
        raise SystemExit(f"reo-ring-rearm: anchor count={count}, refusing to patch")

    src = src.replace(ANCHOR, block() + ANCHOR)
    dp_rx.write_text(src)
    print(f"reo-ring-rearm: patched {dp_rx} (REO rings re-armed after firmware init)")


if __name__ == "__main__":
    main()
