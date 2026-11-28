#!/usr/bin/env python3
"""r616: what the first replenish saw, printed where the console is quiet.

r615 settled two things with bits instead of log lines: steps 0x1f, so
ath12k_dp_rx_bufs_replenish() *did* run (r613's "never ran" was the lossy
console lying), and bufs_max is 512 -- the ring has room for 512 buffers. Yet
every refill probe line was swallowed, so we still do not know the one number
that matters: how many of those 512 slots the first pass actually filled, i.e.
what ath12k_hal_srng_src_num_free() returned on a freshly built ring.

Theory worth testing: if src_num_free() came back 0 (cached ring pointers not
initialised the way the shim expects), then num_remain == 0, replenish posts
nothing, the ring stays empty for the rest of the boot -- and that alone would
explain a receive path that never produces a single MSDU while the firmware
still hands management frames over the WMI side channel.

So: stash the numbers in file-scope statics inside replenish, print them from
ath12k_dp_rx_pdev_alloc (which logs fine), and add one steady-state sample of
the refill ring's hp/tp/hwtp from the RX poll loop so we can also see whether
the firmware ever consumes a buffer.
"""

import sys
import pathlib

MARK = "NWDIAG refill summary"
MARK_NOW = "NWDIAG refill now"

DECL = (
    "\n/* r616: what the first refill pass saw, for a later quiet-time print */\n"
    "static u32 dbg_rf_calls, dbg_rf_ring, dbg_rf_nf, dbg_rf_req, dbg_rf_rem;\n"
    "static u32 dbg_rf_hp, dbg_rf_tp;\n"
    "static unsigned long long dbg_rf_lo, dbg_rf_hi;\n"
    "/* r618: the device writes each REO dst ring's HP into the wrp window; report\n"
    " * the address it would write to, not the shadow value.\n"
    " */\n"
    "#define NWDIAG_REO_HP(i) \\\n"
    "  ((unsigned long long)(ab->hal.wrp.paddr + \\\n"
    "   ((unsigned long)ab->hal.srng_list[ath12k_ab_to_dp(ab)->reo_dst_ring[i].ring_id] \\\n"
    "    .u.dst_ring.hp_addr - (unsigned long)ab->hal.wrp.vaddr)))\n"
)

CAPTURE = (
    "\t{\n"
    "\t\tu32 dbg_tp0 = *srng->u.src_ring.tp_addr;\n"
    "\n"
    "\t\tdbg_rf_calls++;\n"
    "\t\tif (dbg_rf_calls <= 4) {\n"
    "\t\t\tdbg_rf_ring = rx_ring->refill_buf_ring.ring_id;\n"
    "\t\t\tdbg_rf_nf = num_free;\n"
    "\t\t\tdbg_rf_req = req_entries;\n"
    "\t\t\tdbg_rf_hp = srng->u.src_ring.hp;\n"
    "\t\t\tdbg_rf_tp = dbg_tp0;\n"
    "\t\t}\n"
    "\t}\n"
)

CAPTURE_REM = (
    "\tdbg_rf_rem = num_remain;\n"
)

# r617's per-buffer prints were eaten by the lossy console, so keep the span in
# statics and let the late summary carry it instead.
BUF_SPAN = (
    "\t\t{\n"
    "\t\t\tunsigned long long dbg_pa = (unsigned long long)paddr;\n"
    "\t\t\tunsigned long long dbg_end = dbg_pa + skb->len + skb_tailroom(skb);\n"
    "\n"
    "\t\t\tif (!dbg_rf_lo || dbg_pa < dbg_rf_lo)\n"
    "\t\t\t\tdbg_rf_lo = dbg_pa;\n"
    "\t\t\tif (dbg_end > dbg_rf_hi)\n"
    "\t\t\t\tdbg_rf_hi = dbg_end;\n"
    "\t\t}\n"
)

SUMMARY = (
    '\tath12k_info(ab, "' + MARK + ' calls %u ring %u nf %u req %u rem %u"\n'
    '\t\t    " hp %u hwtp %u\\n", dbg_rf_calls, dbg_rf_ring, dbg_rf_nf,\n'
    '\t\t    dbg_rf_req, dbg_rf_rem, dbg_rf_hp, dbg_rf_tp);\n'
    '\tath12k_info(ab, "' + MARK + ' buf lo 0x%llx hi 0x%llx\\n",\n'
    '\t\t    dbg_rf_lo, dbg_rf_hi);\n'
    '\tath12k_info(ab, "' + MARK + ' dma rdp 0x%llx wrp 0x%llx\\n",\n'
    '\t\t    (unsigned long long)ab->hal.rdp.paddr,\n'
    '\t\t    (unsigned long long)ab->hal.wrp.paddr);\n'
    '\tath12k_info(ab, "' + MARK + ' reo hp 0x%llx 0x%llx 0x%llx 0x%llx\\n",\n'
    '\t\t    NWDIAG_REO_HP(0), NWDIAG_REO_HP(1), NWDIAG_REO_HP(2),\n'
    '\t\t    NWDIAG_REO_HP(3));\n'
)

NOW = (
    "\t{\n"
    "\t\tstatic u32 dbg_rs;\n"
    "\t\tu32 dbg_rrid = dp->rx_refill_buf_ring.refill_buf_ring.ring_id;\n"
    "\t\tstruct hal_srng *dbg_rsrng = &hal->srng_list[dbg_rrid];\n"
    "\n"
    "\t\tdbg_rs++;\n"
    "\t\tif (dbg_rs == 200 || dbg_rs == 1000 || !(dbg_rs & 2047))\n"
    "\t\t\tath12k_info(ab, \"" + MARK_NOW + " n %u ring %u hp %u tp %u nf %d\\n\",\n"
    "\t\t\t\t    dbg_rs, dbg_rrid, dbg_rsrng->u.src_ring.hp,\n"
    "\t\t\t\t    *dbg_rsrng->u.src_ring.tp_addr,\n"
    "\t\t\t\t    ath12k_hal_srng_src_num_free(ab, dbg_rsrng, true));\n"
    "\t}\n"
)


def apply_edits(plan):
    """Verify every anchor in every file first, then write. A half-patched tree
    is worse than an unpatched one: the stage chain would record a partial round
    and the next patch would be matched against shifted text."""
    staged = {}
    for path, edits in plan:
        data = path.read_bytes()
        for anchor, block, before in edits:
            a = anchor.encode()
            n = data.count(a)
            if n != 1:
                raise SystemExit(f"refill-summary: anchor count={n} in {path.name}: "
                                 f"{anchor.splitlines()[0][:60]!r}")
            data = data.replace(
                a, block.encode() + a if before else a + block.encode())
        staged[path] = data

    for path, data in staged.items():
        path.write_bytes(data)


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    base = root
    if not (base / "dp_rx.c").exists():
        base = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    if not (base / "dp_rx.c").exists():
        raise SystemExit(f"refill-summary: no ath12k sources under {root}")

    dp_rx = base / "dp_rx.c"
    wifi7 = base / "wifi7/dp_rx.c"
    if MARK.encode() in dp_rx.read_bytes():
        print("refill-summary: already patched")
        return

    pdev_sig = (
        "int ath12k_dp_rx_pdev_alloc(struct ath12k_base *ab, int mac_id)\n{\n"
        "\tstruct ath12k *ar = ab->pdevs[mac_id].ar;\n"
        "\tstruct ath12k_pdev_dp *dp = &ar->dp;\n"
        "\tu32 ring_id;\n"
        "\tint i;\n"
        "\tint ret;\n")

    apply_edits([
        (dp_rx, [
            ('#include "dp_rx.h"\n', DECL, False),
            ("\tnum_free = ath12k_hal_srng_src_num_free(ab, srng, true);\n",
             CAPTURE, False),
            ("\tif (!num_remain)\n\t\tgoto out;\n", CAPTURE_REM, True),
            ("\t\tath12k_hal_rx_buf_addr_info_set(dp->hal, desc, paddr, cookie,\n"
             "\t\t\t\t\t\tmgr);\n", BUF_SPAN, True),
            (pdev_sig, SUMMARY, False),
        ]),
        (wifi7, [
            ("\t{\n\t\tstatic u32 dbg_poll;\n\n\t\tdbg_poll++;\n"
             "\t\tif (dbg_poll <= 5 || !(dbg_poll & 511))\n"
             '\t\t\tath12k_info(ab, "NWDIAG rx poll n %u ring %d budget %d\\n",\n'
             "\t\t\t\t    dbg_poll, ring_id, budget);\n\t}\n",
             NOW, False),
        ]),
    ])

    print("refill-summary: patched ath12k/dp_rx.c + ath12k/wifi7/dp_rx.c")


if __name__ == "__main__":
    main()
