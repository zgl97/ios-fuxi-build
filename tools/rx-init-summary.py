#!/usr/bin/env python3
"""r615: record init steps as bits, then print the bits later.

r614 put a print at every step of the DP RX init chain, and the boot log showed
the entry line missing while a print from thirty lines further down made it out.
Disassembly of _ath12k_dp_rx_alloc in that same kernel tells the truth: the very
first instructions load the "alloc enter" string and call _ath12k_info, so the
line was emitted and lost in the serial console, not skipped.

Lesson: on this port a *missing* line is weak evidence -- only a present line
proves anything. So stop inferring from absence. Each step now sets a bit in a
file-scope word, and one summary line is printed from a place we know runs late
(ath12k_dp_rx_pdev_alloc, which logs fine) plus from replenish itself if it ever
gets there. The bitmask says exactly how far the chain got, regardless of which
individual lines the console swallowed.
"""

import sys
import pathlib

MARK = "NWDIAG rx init summary"

DECL = (
    "\n/* r615: init-chain bits, so one late print survives a lossy console */\n"
    "static u32 dbg_rx_steps;\n"
    "#define DBG_RX_STEP(bit) do { dbg_rx_steps |= (1u << (bit)); } while (0)\n"
)

STEPS = []


def step(anchor, bit):
    STEPS.append((anchor, f"\tDBG_RX_STEP({bit});\n"))


step("int ath12k_dp_rx_alloc(struct ath12k_base *ab)\n{\n"
     "\tstruct ath12k_dp *dp = ath12k_ab_to_dp(ab);\n", 0)

step("\tret = ath12k_dp_rxdma_buf_setup(ab);\n", 1)

step("static int ath12k_dp_rxdma_buf_setup(struct ath12k_base *ab)\n{\n"
     "\tstruct ath12k_dp *dp = ath12k_ab_to_dp(ab);\n"
     "\tstruct dp_rxdma_mon_ring *mon_ring;\n"
     "\tint ret, i;\n", 2)

step("\trx_ring->bufs_max = rx_ring->refill_buf_ring.size /\n"
     "\t\t\tath12k_hal_srng_get_entrysize(ab, HAL_RXDMA_BUF);\n", 3)

step("int ath12k_dp_rx_bufs_replenish(struct ath12k_dp *dp,\n"
     "\t\t\t\tstruct dp_rxdma_ring *rx_ring,\n"
     "\t\t\t\tstruct list_head *used_list,\n"
     "\t\t\t\tint req_entries)\n{\n"
     "\tstruct ath12k_base *ab = dp->ab;\n", 4)

SUMMARY_IN_PDEV = (
    '\tath12k_info(ab, "' + MARK + ' steps 0x%x refill_ring %u bufs_max %d"\n'
    '\t\t    " rx_pdev 1 rxdma1 %d\\n", dbg_rx_steps,\n'
    '\t\t    ath12k_ab_to_dp(ab)->rx_refill_buf_ring.refill_buf_ring.ring_id,\n'
    '\t\t    ath12k_ab_to_dp(ab)->rx_refill_buf_ring.bufs_max,\n'
    '\t\t    ab->hw_params->rxdma1_enable);\n')

SUMMARY_IN_REPL = (
    '\tath12k_info(ab, "' + MARK + ' steps 0x%x ring %u req %d"\n'
    '\t\t    " replenish 1\\n", dbg_rx_steps,\n'
    '\t\t    rx_ring->refill_buf_ring.ring_id, req_entries);\n')


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    base = root
    if not (base / "dp_rx.c").exists():
        base = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    if not (base / "dp_rx.c").exists():
        raise SystemExit(f"rx-init-summary: no ath12k sources under {root}")

    path = base / "dp_rx.c"
    data = path.read_bytes()
    if MARK.encode() in data:
        print("rx-init-summary: already patched")
        return

    # 1. the bit word, right after the last #include
    inc_anchor = b'#include "dp_rx.h"\n'
    if data.count(inc_anchor) != 1:
        raise SystemExit("rx-init-summary: include anchor not unique")
    data = data.replace(inc_anchor, inc_anchor + DECL.encode())

    # 2. one bit per step
    for anchor, block in STEPS:
        a = anchor.encode()
        if data.count(a) != 1:
            raise SystemExit("rx-init-summary: step anchor not unique: "
                             + anchor.splitlines()[0][:60])
        data = data.replace(a, a + block.encode())

    # 3. the summary, from the two places that matter
    pdev_anchor = (
        "int ath12k_dp_rx_pdev_alloc(struct ath12k_base *ab, int mac_id)\n{\n"
        "\tstruct ath12k *ar = ab->pdevs[mac_id].ar;\n"
        "\tstruct ath12k_pdev_dp *dp = &ar->dp;\n"
        "\tu32 ring_id;\n"
        "\tint i;\n"
        "\tint ret;\n").encode()
    if data.count(pdev_anchor) != 1:
        raise SystemExit("rx-init-summary: pdev alloc anchor not unique")
    data = data.replace(pdev_anchor, pdev_anchor + SUMMARY_IN_PDEV.encode())

    repl_anchor = (
        "int ath12k_dp_rx_bufs_replenish(struct ath12k_dp *dp,\n"
        "\t\t\t\tstruct dp_rxdma_ring *rx_ring,\n"
        "\t\t\t\tstruct list_head *used_list,\n"
        "\t\t\t\tint req_entries)\n{\n"
        "\tstruct ath12k_base *ab = dp->ab;\n").encode()
    if data.count(repl_anchor) != 1:
        raise SystemExit("rx-init-summary: replenish anchor not unique")
    data = data.replace(repl_anchor, repl_anchor + SUMMARY_IN_REPL.encode())

    path.write_bytes(data)
    print("rx-init-summary: patched ath12k/dp_rx.c (6 bits + 2 summaries)")


if __name__ == "__main__":
    main()
