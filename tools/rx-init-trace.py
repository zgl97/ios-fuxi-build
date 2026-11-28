#!/usr/bin/env python3
"""r614: walk the DP RX init chain with a tracer bullet at every step.

r613 established the hard fact: ath12k_dp_rx_bufs_replenish() never ran, the
REO destination rings were never written (hwhp stuck at 0 on all four rings),
and nothing was ever reaped -- while the poll loop itself was alive (1176
samples).  No RX buffers posted means the hardware has nowhere to write, which
would explain the silent receive path end to end.

But the source says replenish sits unconditionally inside
ath12k_dp_rx_alloc() -> ath12k_dp_rxdma_buf_setup() -> ath12k_dp_rxdma_ring_buf_setup(),
and the same function also sets up the rx_mac_buf_ring whose ring ids (161/169)
we do see in the htt-filter probe.  Something in that chain is skipped, returns
early, or lives in a copy of the file the build did not use.  So stop guessing
and drop a bullet at every step of the chain:

    ath12k_dp_rx_alloc            enter, and just before the buffer setup call
    ath12k_dp_rxdma_buf_setup     enter
    ath12k_dp_rxdma_ring_buf_setup bufs_max + ring size
    ath12k_dp_rx_pdev_alloc       enter, with the hw_params that gate it
    ath12k_dp_rx_bufs_replenish   enter (before any early return)
"""

import sys
import pathlib

MARK = "NWDIAG rx init"

STEPS = []


def step(rel, anchor, block):
    STEPS.append((rel, anchor, block))


step("dp_rx.c",
     "int ath12k_dp_rx_alloc(struct ath12k_base *ab)\n{\n\tstruct ath12k_dp *dp = ath12k_ab_to_dp(ab);\n",
     '\tath12k_info(ab, "' + MARK + ' alloc enter\\n");\n')

step("dp_rx.c",
     "\tret = ath12k_dp_rxdma_buf_setup(ab);\n",
     '\tath12k_info(ab, "' + MARK + ' alloc srngs done refill ring %u\\n",\n'
     '\t\t    dp->rx_refill_buf_ring.refill_buf_ring.ring_id);\n')

step("dp_rx.c",
     "static int ath12k_dp_rxdma_buf_setup(struct ath12k_base *ab)\n{\n\tstruct ath12k_dp *dp = ath12k_ab_to_dp(ab);\n\tstruct dp_rxdma_mon_ring *mon_ring;\n\tint ret, i;\n",
     '\tath12k_info(ab, "' + MARK + ' rxdma buf setup enter rxdma1 %d\\n",\n'
     '\t\t    ab->hw_params->rxdma1_enable);\n')

step("dp_rx.c",
     "\trx_ring->bufs_max = rx_ring->refill_buf_ring.size /\n"
     "\t\t\tath12k_hal_srng_get_entrysize(ab, HAL_RXDMA_BUF);\n",
     '\tath12k_info(ab, "' + MARK + ' ring buf setup bufs_max %u ring size %u\\n",\n'
     '\t\t    rx_ring->bufs_max, rx_ring->refill_buf_ring.size);\n')

step("dp_rx.c",
     "int ath12k_dp_rx_pdev_alloc(struct ath12k_base *ab, int mac_id)\n{\n\tstruct ath12k *ar = ab->pdevs[mac_id].ar;\n\tstruct ath12k_pdev_dp *dp = &ar->dp;\n\tu32 ring_id;\n\tint i;\n\tint ret;\n",
     '\tath12k_info(ab, "' + MARK + ' pdev alloc enter mac %d rxdma1 %d dst_rings %u per_pdev %u\\n",\n'
     '\t\t    mac_id, ab->hw_params->rxdma1_enable,\n'
     '\t\t    ab->hw_params->num_rxdma_dst_ring,\n'
     '\t\t    ab->hw_params->num_rxdma_per_pdev);\n')

step("dp_rx.c",
     "int ath12k_dp_rx_bufs_replenish(struct ath12k_dp *dp,\n"
     "\t\t\t\tstruct dp_rxdma_ring *rx_ring,\n"
     "\t\t\t\tstruct list_head *used_list,\n"
     "\t\t\t\tint req_entries)\n{\n\tstruct ath12k_base *ab = dp->ab;\n",
     '\tath12k_info(ab, "' + MARK + ' replenish enter ring %u req %d\\n",\n'
     '\t\t    rx_ring->refill_buf_ring.ring_id, req_entries);\n')


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    base = root
    if not (base / "dp_rx.c").exists():
        base = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    if not (base / "dp_rx.c").exists():
        raise SystemExit(f"rx-init-trace: no ath12k sources under {root}")

    touched = 0
    for rel, anchor, block in STEPS:
        path = base / rel
        data = path.read_bytes()
        if block.encode() in data:
            continue
        a = anchor.encode()
        if data.count(a) != 1:
            raise SystemExit(f"rx-init-trace: anchor not unique in {rel} "
                             f"(count={data.count(a)}): {anchor[:60]!r}")
        path.write_bytes(data.replace(a, a + block.encode()))
        touched += 1

    if not touched:
        print("rx-init-trace: already patched")
        return
    print(f"rx-init-trace: patched {touched} step(s) in ath12k/dp_rx.c")


if __name__ == "__main__":
    main()
