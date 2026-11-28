#!/usr/bin/env python3
"""r623: read the REO dst ring registers before and after the firmware init cmd.

The eight REO destination rings are armed in ath12k_dp_rx_pdev_reo_setup(),
which runs *before* ath12k_wmi_cmd_init().  If the firmware reconfigures the
REO block during its own init, the base registers we wrote are gone by the time
the first frame should be delivered -- and that is exactly what the r622 dump
suggests: ring 0 still holds its base, rings 1..7 read back as zero.

So read the same eight registers at two points: right before the init cmd goes
out, and right after the unified ready event comes back.  Two runs of the same
read, one firmware init apart, tell us whether the firmware clears them.
"""

import sys
from pathlib import Path

MARK = "NWDIAG reo arm"

# 核心编号：ath12k_wmi_cmd_init() 调用前，与 unified ready 之后。
PRE_ANCHOR = "\tret = ath12k_wmi_cmd_init(ab);\n"
POST_ANCHOR = (
    '\tret = ath12k_wmi_wait_for_unified_ready(ab);\n'
    '\tif (ret) {\n'
    '\t\tath12k_err(ab, "failed to receive wmi unified ready event: %d\\n",\n'
    "\t\t\t   ret);\n"
    "\t\tgoto err_reo_cleanup;\n"
    "\t}\n"
)

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
    "\t\t\tath12k_info(ab, \"NWDIAG reo arm @TAG@ %d id %u reg 0x%x base 0x%x msb 0x%x\\n\",",
    "\t\t\t\t    dbg_i, dbg_s->ring_id, dbg_rb,",
    "\t\t\t\t    ath12k_hif_read32(ab, dbg_rb),",
    "\t\t\t\t    ath12k_hif_read32(ab, dbg_rb + 4));",
    "\t\t}",
    "\t}",
]


def block(tag):
    return "\n".join(line.replace("@TAG@", tag) for line in BLOCK_LINES) + "\n"


def ath12k_dir(dest):
    dest = Path(dest)
    candidates = [
        dest,
        dest / "ath12k",
        dest / "deps/linux/drivers/net/wireless/ath/ath12k",
        dest / "drivers/net/wireless/ath/ath12k",
    ]
    for cand in candidates:
        if (cand / "core.c").is_file() and (cand / "dp_rx.c").is_file():
            return cand
    raise SystemExit(f"reo-arm-trace: no ath12k source under {dest}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: reo-arm-trace.py <tree>")
    root = ath12k_dir(sys.argv[1])
    core = root / "core.c"
    src = core.read_text()

    if MARK in src:
        print(f"reo-arm-trace: already applied to {core}")
        return

    for name, anchor in (("pre", PRE_ANCHOR), ("post", POST_ANCHOR)):
        count = src.count(anchor)
        if count != 1:
            raise SystemExit(
                f"reo-arm-trace: {name} anchor count={count}, refusing to patch"
            )

    src = src.replace(PRE_ANCHOR, block("pre") + PRE_ANCHOR)
    src = src.replace(POST_ANCHOR, POST_ANCHOR + block("post"))

    core.write_text(src)
    print(f"reo-arm-trace: patched {core} (REO arm trace)")


if __name__ == "__main__":
    main()
