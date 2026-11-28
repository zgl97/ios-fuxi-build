"""r619: dump each REO dst ring's memory and the HP the device would read.

r618 proved the address space is sane (the device writes hwtp into the rdp
window). The open question is whether RXDMA ever wrote descriptors into the REO
dst rings: host reads its own copy of that memory, so a non-zero word there
means the device DMA'd something, and the stalled `tp` is then a pointer
visibility bug on our side. All zero means the ring was never enabled.

Printed at the late, quiet point (pdev_alloc) because the console drops lines
under load.
"""
import sys
import pathlib


MARK = "NWDIAG reo mem"

DECL = (
    "\n/* r619: the four REO dst rings, sampled once the ring setup has settled */\n"
    "static void nwdiag_reo_mem_dump(struct ath12k_base *ab)\n"
    "{\n"
    "\tstruct ath12k_dp *dbg_dp = ath12k_ab_to_dp(ab);\n"
    "\tint dbg_i;\n"
    "\n"
    "\tfor (dbg_i = 0; dbg_i < DP_REO_DST_RING_MAX; dbg_i++) {\n"
    "\t\tstruct dp_srng *dbg_r = &dbg_dp->reo_dst_ring[dbg_i];\n"
    "\t\tstruct hal_srng *dbg_s = &ab->hal.srng_list[dbg_r->ring_id];\n"
    "\t\tu32 *dbg_w = dbg_s->ring_base_vaddr;\n"
    "\n"
    "\t\tif (!dbg_w)\n"
    "\t\t\tcontinue;\n"
    "\t\tath12k_info(ab, \"NWDIAG reo mem ring %d id %u base 0x%llx n %u es %u\"\n"
    "\t\t\t    \" tp %u chp %u hw 0x%x 0x%x\\n\", dbg_i, dbg_r->ring_id,\n"
    "\t\t\t    (unsigned long long)dbg_s->ring_base_paddr, dbg_s->num_entries,\n"
    "\t\t\t    dbg_s->entry_size, dbg_s->u.dst_ring.tp,\n"
    "\t\t\t    dbg_s->u.dst_ring.cached_hp,\n"
    "\t\t\t    dbg_w[0], dbg_w[1]);\n"
    "\t\tath12k_info(ab, \"NWDIAG reo mem ring %d d2 %x %x %x %x d6 %x %x %x %x\\n\",\n"
    "\t\t\t    dbg_i, dbg_w[2], dbg_w[3], dbg_w[4], dbg_w[5],\n"
    "\t\t\t    dbg_w[6], dbg_w[7], dbg_w[8], dbg_w[9]);\n"
    "\t}\n"
    "}\n"
)

CALL_ANCHOR = (
    "int ath12k_dp_rx_pdev_alloc(struct ath12k_base *ab, int mac_id)\n{\n"
    "\tstruct ath12k *ar = ab->pdevs[mac_id].ar;\n"
    "\tstruct ath12k_pdev_dp *dp = &ar->dp;\n"
    "\tu32 ring_id;\n"
    "\tint i;\n"
    "\tint ret;\n"
)

CALL = (
    "\tnwdiag_reo_mem_dump(ab);\n"
)


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    base = root
    if not (base / "dp_rx.c").exists():
        base = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    if not (base / "dp_rx.c").exists():
        raise SystemExit(f"reo-mem-dump: no ath12k sources under {root}")

    path = base / "dp_rx.c"
    data = path.read_bytes()
    if MARK.encode() in data:
        print("reo-mem-dump: already patched")
        return

    decl_anchor = b'#include "dp_rx.h"\n'
    call_anchor = CALL_ANCHOR.encode()
    for anchor in (decl_anchor, call_anchor):
        n = data.count(anchor)
        if n != 1:
            raise SystemExit(f"reo-mem-dump: anchor count={n} in dp_rx.c: "
                             f"{anchor.splitlines()[0][:60]!r}")

    data = data.replace(decl_anchor, decl_anchor + DECL.encode())
    data = data.replace(call_anchor, call_anchor + CALL.encode())
    path.write_bytes(data)
    print("reo-mem-dump: patched ath12k/dp_rx.c (REO ring memory dump)")


if __name__ == "__main__":
    main()
