#!/usr/bin/env python3
"""r628: arm every ring we own after the firmware init, then refill RX buffers.

Three rounds of probes say the same thing from three directions: the REO block
answers and the destination rings hold their programming, but not one descriptor
appears, the exception and release rings never move, and the refill ring stops
at exactly 286 buffers taken - before the first sample is even printed.  The
firmware init resets the receive block after the host has programmed it.

r625 fixed that for the REO destination rings alone.  Do it for every ring the
host owns: arm each one again after the firmware is up, skip the CE rings (they
carry the WMI/HTT traffic that keeps the link alive), then post a fresh set of
buffers to the refill ring and read the hardware tail back.  If the tail moves,
the receive path is alive again.
"""

from pathlib import Path
import sys

MARK = "NWDIAG rx restart"
ANCHOR = "\tnwdiag_reo_mem_dump(ab);\n"

BLOCK = (
    "\t/* r628: the firmware init resets the RX block, so arm every ring we\n"
    "\t * own again and hand the hardware a fresh set of buffers.\n"
    "\t */\n"
    "\t{\n"
    "\t\tstruct ath12k_dp *dbg_dp = ath12k_ab_to_dp(ab);\n"
    "\t\tstruct hal_srng *dbg_s;\n"
    "\t\tint dbg_i, dbg_n = 0;\n"
    "\n"
    "\t\tfor (dbg_i = 0; dbg_i < HAL_SRNG_RING_ID_MAX; dbg_i++) {\n"
    "\t\t\tdbg_s = &ab->hal.srng_list[dbg_i];\n"
    "\n"
    "\t\t\tif (!dbg_s->ring_base_vaddr)\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\tif (dbg_i >= HAL_SRNG_RING_ID_CE0_SRC &&\n"
    "\t\t\t    dbg_i <= HAL_SRNG_RING_ID_CE15_DST_STATUS)\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\tif (dbg_i >= HAL_SRNG_RING_ID_DMAC_CMN_ID_START)\n"
    "\t\t\t\tcontinue;\n"
    "\n"
    "\t\t\tif (dbg_s->ring_dir == HAL_SRNG_DIR_SRC)\n"
    "\t\t\t\tab->hal.ops->srng_src_hw_init(ab, dbg_s);\n"
    "\t\t\telse\n"
    "\t\t\t\tab->hal.ops->srng_dst_hw_init(ab, dbg_s);\n"
    "\n"
    "\t\t\tdbg_n++;\n"
    "\t\t\tath12k_info(ab, \"" + MARK + " id %d hw 0x%x val 0x%x\\n\",\n"
    "\t\t\t\t    dbg_i, dbg_s->hwreg_base[HAL_SRNG_REG_GRP_R0],\n"
    "\t\t\t\t    ath12k_hif_read32(ab, dbg_s->hwreg_base[HAL_SRNG_REG_GRP_R0]));\n"
    "\t\t}\n"
    "\n"
    "\t\tath12k_info(ab, \"" + MARK + " armed %u refill %u\\n\",\n"
    "\t\t\t    dbg_n, dbg_dp->rx_refill_buf_ring.refill_buf_ring.ring_id);\n"
    "\t\tath12k_dp_rxdma_ring_buf_setup(ab, &dbg_dp->rx_refill_buf_ring);\n"
    "\t\tath12k_info(ab, \"" + MARK + " refill hp %u tp %u\\n\",\n"
    "\t\t\t    ab->hal.srng_list[dbg_dp->rx_refill_buf_ring.refill_buf_ring.ring_id].u.src_ring.hp,\n"
    "\t\t\t    *ab->hal.srng_list[dbg_dp->rx_refill_buf_ring.refill_buf_ring.ring_id].u.src_ring.tp_addr);\n"
    "\t}\n"
)

HIF_INCLUDE = '#include "hif.h"\n'
INCLUDE_ANCHOR = '#include "dp_rx.h"\n'


def ath12k_dir(dest: Path) -> Path:
    for cand in (
        dest,
        dest / "ath12k",
        dest / "deps/linux/drivers/net/wireless/ath/ath12k",
        dest / "drivers/net/wireless/ath/ath12k",
    ):
        if (cand / "dp_rx.c").is_file():
            return cand
    raise SystemExit(f"rx-restart: no ath12k tree under {dest}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: rx-restart.py <tree|ath12k>")

    root = ath12k_dir(Path(sys.argv[1]))
    path = root / "dp_rx.c"
    src = path.read_bytes().decode()

    if MARK in src:
        print("rx-restart: already patched")
        return 0

    count = src.count(ANCHOR)
    if count != 1:
        raise SystemExit(f"rx-restart: dump anchor count={count}, refusing")

    if HIF_INCLUDE not in src:
        if src.count(INCLUDE_ANCHOR) != 1:
            raise SystemExit("rx-restart: include anchor not unique")
        src = src.replace(INCLUDE_ANCHOR, INCLUDE_ANCHOR + HIF_INCLUDE)

    src = src.replace(ANCHOR, BLOCK + ANCHOR)
    path.write_bytes(src.encode())
    print(f"rx-restart: patched {path} (every host ring armed again, buffers re-posted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
