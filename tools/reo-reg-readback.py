"""r620: did our REO ring register writes actually land, and is REO enabled?

r619 shows all eight REO dst rings hold nothing but zeros, so the question moves
back one layer: the host programs each ring's base address, size, ring id and HP
address into the chip's register window in ath12k_wifi7_hal_srng_dst_hw_init().
Read those registers back. A base that reads back equal to what we wrote means
the window works and the ring simply was never enabled; a zero or stale base
means our write32 path is not reaching the chip, which is a port problem we can
fix here.

Also read back the REO global enable and destination ring control registers that
ath12k_wifi7_hal_reo_hw_setup() writes, because a disabled REO explains empty
rings just as well.
"""
import sys
import pathlib


MARK = "NWDIAG reo reg"

READBACK = (
    "\n\tfor (i = 0; i < DP_REO_DST_RING_MAX; i++) {\n"
    "\t\tstruct dp_srng *dbg_r = &dp->reo_dst_ring[i];\n"
    "\t\tstruct hal_srng *dbg_s = &ab->hal.srng_list[dbg_r->ring_id];\n"
    "\t\tu32 dbg_rb = ath12k_hif_read32(ab,\n"
    "\t\t\tdbg_s->hwreg_base[HAL_SRNG_REG_GRP_R0]);\n"
    "\t\tu32 dbg_msb = ath12k_hif_read32(ab,\n"
    "\t\t\tdbg_s->hwreg_base[HAL_SRNG_REG_GRP_R0] + 4);\n"
    "\t\tu32 dbg_hp = READ_ONCE(*dbg_s->u.dst_ring.hp_addr);\n"
    "\n"
    "\t\tath12k_info(ab, \"" + MARK + " id %u wrote 0x%llx read 0x%x msb 0x%x hp %u\\n\",\n"
    "\t\t\t    dbg_r->ring_id, (unsigned long long)dbg_r->paddr,\n"
    "\t\t\t    dbg_rb, dbg_msb, dbg_hp);\n"
    "\t}\n"
)

ENABLE_READBACK = (
    "\tath12k_info(ab, \"" + MARK + " enable 0x%x misc 0x%x ix2 0x%x ix3 0x%x\\n\",\n"
    "\t\t    ath12k_hif_read32(ab, reo_base + HAL_REO1_GEN_ENABLE),\n"
    "\t\t    ath12k_hif_read32(ab, reo_base + HAL_REO1_MISC_CTRL_ADDR(hal)),\n"
    "\t\t    ath12k_hif_read32(ab, reo_base + HAL_REO1_DEST_RING_CTRL_IX_2),\n"
    "\t\t    ath12k_hif_read32(ab, reo_base + HAL_REO1_DEST_RING_CTRL_IX_3));\n"
)

DP_RX_ANCHOR = (
    "\t\tif (ret) {\n"
    "\t\t\tath12k_warn(ab, \"failed to setup reo_dst_ring\\n\");\n"
    "\t\t\tgoto err_reo_cleanup;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "\treturn 0;\n"
)

HAL_RX_ANCHOR = (
    "\tath12k_hif_write32(ab, reo_base + HAL_REO1_DEST_RING_CTRL_IX_3,\n"
    "\t\t\t   ring_hash_map);\n"
    "}\n"
)


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    base = root
    if not (base / "dp_rx.c").exists():
        base = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    if not (base / "dp_rx.c").exists():
        raise SystemExit(f"reo-reg-readback: no ath12k sources under {root}")

    dp_rx = base / "dp_rx.c"
    hal_rx = base / "wifi7/hal_rx.c"
    dp_data = dp_rx.read_bytes()
    hal_data = hal_rx.read_bytes()

    if MARK.encode() in dp_data:
        print("reo-reg-readback: already patched")
        return

    inc_anchor = b'#include "dp_rx.h"\n'
    if b'#include "hif.h"\n' not in dp_data:
        if dp_data.count(inc_anchor) != 1:
            raise SystemExit("reo-reg-readback: include anchor count != 1")
        dp_data = dp_data.replace(inc_anchor, inc_anchor + b'#include "hif.h"\n')

    n = dp_data.count(DP_RX_ANCHOR.encode())
    if n != 1:
        raise SystemExit(f"reo-reg-readback: dp_rx anchor count={n}")
    dp_data = dp_data.replace(DP_RX_ANCHOR.encode(),
                              DP_RX_ANCHOR.encode().replace(
                                  b"\n\treturn 0;\n",
                                  b"\n" + READBACK.encode() + b"\n\treturn 0;\n"))

    n = hal_data.count(HAL_RX_ANCHOR.encode())
    if n != 1:
        raise SystemExit(f"reo-reg-readback: hal_rx anchor count={n}")
    hal_data = hal_data.replace(
        HAL_RX_ANCHOR.encode(),
        b"\tath12k_hif_write32(ab, reo_base + HAL_REO1_DEST_RING_CTRL_IX_3,\n"
        b"\t\t\t   ring_hash_map);\n" + ENABLE_READBACK.encode() + b"}\n")

    dp_rx.write_bytes(dp_data)
    hal_rx.write_bytes(hal_data)
    print("reo-reg-readback: patched ath12k/dp_rx.c + ath12k/wifi7/hal_rx.c")


if __name__ == "__main__":
    main()
