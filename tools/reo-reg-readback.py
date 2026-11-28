"""r621: read the REO ring registers back, but only once the firmware is up.

r620 read them inside ath12k_dp_rx_pdev_reo_setup(), which runs right after the
WMI service-ready event and *before* ath12k_wmi_cmd_init(). Every read came back
0xffffffff and all three boots after that change failed to bring en0 up, while
the same build booted fine the round before. A read of a UMAC aperture that the
firmware has not brought up yet looks like the likeliest way to wedge the
endpoint, so the readback moves to ath12k_dp_rx_pdev_alloc(), after
ath12k_wmi_wait_for_unified_ready(), where the radio is already running and the
register window is known to answer for TCL and WBM.

One extra field, win: the raw content of the PCIe register window selector at
0x310c, so a bad window selection is visible instead of being guessed at.
"""
import sys
import pathlib


MARK = "NWDIAG reo reg"

LATE_READBACK = (
    # Inside ath12k_dp_rx_pdev_alloc() `dp` is the pdev dp, not the base dp, so
    # reach the REO rings through ath12k_ab_to_dp().
    "\t{\n"
    "\t\tstruct ath12k_dp *dbg_dp = ath12k_ab_to_dp(ab);\n"
    "\t\tstruct dp_srng *dbg_r = &dbg_dp->reo_dst_ring[0];\n"
    "\t\tstruct hal_srng *dbg_s = &ab->hal.srng_list[dbg_r->ring_id];\n"
    "\t\tu32 dbg_win = ath12k_hif_read32(ab, 0x310c);\n"
    "\t\tu32 dbg_rb = ath12k_hif_read32(ab,\n"
    "\t\t\tdbg_s->hwreg_base[HAL_SRNG_REG_GRP_R0]);\n"
    "\t\tu32 dbg_msb = ath12k_hif_read32(ab,\n"
    "\t\t\tdbg_s->hwreg_base[HAL_SRNG_REG_GRP_R0] + 4);\n"
    "\n"
    "\t\tath12k_info(ab, \"" + MARK + " late win 0x%x hw 0x%x wrote 0x%llx base 0x%x msb 0x%x\\n\",\n"
    "\t\t\t    dbg_win, dbg_s->hwreg_base[HAL_SRNG_REG_GRP_R0],\n"
    "\t\t\t    (unsigned long long)dbg_r->paddr, dbg_rb, dbg_msb);\n"
    "\t}\n"
)

ENABLE_READBACK = (
    "\tath12k_info(ab, \"" + MARK + " enable 0x%x misc 0x%x ix2 0x%x ix3 0x%x\\n\",\n"
    "\t\t    ath12k_hif_read32(ab, reo_base + HAL_REO1_GEN_ENABLE),\n"
    "\t\t    ath12k_hif_read32(ab, reo_base + HAL_REO1_MISC_CTRL_ADDR(hal)),\n"
    "\t\t    ath12k_hif_read32(ab, reo_base + HAL_REO1_DEST_RING_CTRL_IX_2),\n"
    "\t\t    ath12k_hif_read32(ab, reo_base + HAL_REO1_DEST_RING_CTRL_IX_3));\n"
)

# reo-mem-dump.py runs earlier in the chain and leaves this call at the end of
# ath12k_dp_rx_pdev_alloc's declaration block.
DUMP_ANCHOR = "\tnwdiag_reo_mem_dump(ab);\n"

HAL_RX_ANCHOR = (
    "\tath12k_hif_write32(ab, reo_base + HAL_REO1_DEST_RING_CTRL_IX_3,\n"
    "\t\t\t   ring_hash_map);\n"
    "}\n"
)


def body_of(text: str, signature: str) -> str:
    start = text.index(signature)
    end = text.index("\n}\n", start)
    return text[start:end]


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    base = root
    if not (base / "dp_rx.c").exists():
        base = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    if not (base / "dp_rx.c").exists():
        raise SystemExit(f"reo-reg-readback: no ath12k sources under {root}")

    dp_rx = base / "dp_rx.c"
    hal_rx = base / "wifi7/hal_rx.c"
    dp_text = dp_rx.read_bytes().decode()
    hal_text = hal_rx.read_bytes().decode()

    if MARK in dp_text:
        print("reo-reg-readback: already patched")
        return

    # Both files are checked in memory and written together, so a failing anchor
    # never leaves half a patch behind.
    if '#include "hif.h"' not in dp_text:
        inc_anchor = '#include "dp_rx.h"\n'
        if dp_text.count(inc_anchor) != 1:
            raise SystemExit("reo-reg-readback: include anchor count != 1")
        dp_text = dp_text.replace(inc_anchor, inc_anchor + '#include "hif.h"\n')

    if dp_text.count(DUMP_ANCHOR) != 1:
        raise SystemExit(
            f"reo-reg-readback: dump anchor count={dp_text.count(DUMP_ANCHOR)}")
    dp_text = dp_text.replace(DUMP_ANCHOR, DUMP_ANCHOR + LATE_READBACK)

    if hal_text.count(HAL_RX_ANCHOR) != 1:
        raise SystemExit(
            f"reo-reg-readback: hal_rx anchor count={hal_text.count(HAL_RX_ANCHOR)}")
    hal_text = hal_text.replace(
        HAL_RX_ANCHOR,
        "\tath12k_hif_write32(ab, reo_base + HAL_REO1_DEST_RING_CTRL_IX_3,\n"
        "\t\t\t   ring_hash_map);\n" + ENABLE_READBACK + "}\n")

    # Guard the reason this round exists: nothing may read REO registers from
    # the early setup path again.
    early = body_of(dp_text, "int ath12k_dp_rx_pdev_reo_setup(struct ath12k_base *ab)")
    if MARK in early:
        raise SystemExit("reo-reg-readback: probe landed in the early setup path")
    late = body_of(dp_text, "int ath12k_dp_rx_pdev_alloc(")
    if MARK not in late:
        raise SystemExit("reo-reg-readback: probe missing from the late path")

    dp_rx.write_bytes(dp_text.encode())
    hal_rx.write_bytes(hal_text.encode())
    print("reo-reg-readback: patched ath12k/dp_rx.c late + ath12k/wifi7/hal_rx.c")


if __name__ == "__main__":
    main()
