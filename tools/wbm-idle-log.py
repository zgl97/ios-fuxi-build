#!/usr/bin/env python3
"""Ask the WBM idle link ring whether REO ever asked it for a descriptor.

REO cannot hand an MSDU to the host without a link descriptor, and those come
from the HAL_WBM_IDLE_LINK ring that the host fills at init.  If the hardware
tail of that ring never advances, REO never asked for one and the receive
block is dead upstream of the descriptor memory; if it advances, the
descriptors are being consumed and the fault is further back in RXDMA.

Two lines, both short enough for the 180 byte console truncation:
  NWDIAG wbm idle      one snapshot right after the rings are re-armed
  NWDIAG wbm idle poll  sampled from the receive poller
"""
import sys

MARK = "NWDIAG wbm idle"

DP_RX_ANCHOR = "\tnwdiag_reo_mem_dump(ab);\n"

SNAP = """\t/* r629: does REO ever pull a link descriptor out of the idle ring? */
\t{
\t\tstruct hal_srng *dbg_wl =
\t\t\t&ab->hal.srng_list[HAL_SRNG_RING_ID_WBM_IDLE_LINK];

\t\tath12k_info(ab, "NWDIAG wbm idle n %u hp %u tp %u hwtp %u\\n",
\t\t\t    dbg_wl->num_entries, dbg_wl->u.src_ring.hp,
\t\t\t    dbg_wl->u.src_ring.cached_tp,
\t\t\t    *dbg_wl->u.src_ring.tp_addr);
\t}
"""

WIFI7_ANCHOR = """\t{
\t\tstatic u32 dbg_rs;
\t\tu32 dbg_rrid = dp->rx_refill_buf_ring.refill_buf_ring.ring_id;
\t\tstruct hal_srng *dbg_rsrng = &hal->srng_list[dbg_rrid];

\t\tdbg_rs++;
\t\tif (dbg_rs == 200 || dbg_rs == 1000 || !(dbg_rs & 2047))
\t\t\tath12k_info(ab, "NWDIAG refill now n %u ring %u hp %u tp %u nf %d\\n",
\t\t\t\t    dbg_rs, dbg_rrid, dbg_rsrng->u.src_ring.hp,
\t\t\t\t    *dbg_rsrng->u.src_ring.tp_addr,
\t\t\t\t    ath12k_hal_srng_src_num_free(ab, dbg_rsrng, true));
\t}
"""

POLL = """\t{
\t\tstatic u32 dbg_wb;
\t\tstruct hal_srng *dbg_wl =
\t\t\t&hal->srng_list[HAL_SRNG_RING_ID_WBM_IDLE_LINK];

\t\tdbg_wb++;
\t\tif (dbg_wb == 200 || !(dbg_wb & 2047))
\t\t\tath12k_info(ab, "NWDIAG wbm idle poll n %u hp %u tp %u hwtp %u\\n",
\t\t\t\t    dbg_wb, dbg_wl->u.src_ring.hp,
\t\t\t\t    dbg_wl->u.src_ring.cached_tp,
\t\t\t\t    *dbg_wl->u.src_ring.tp_addr);
\t}
"""


def ath12k_dir(dest):
    from pathlib import Path

    root = Path(dest)
    for cand in (root, root / "ath12k",
                 root / "deps/linux/drivers/net/wireless/ath/ath12k",
                 root / "drivers/net/wireless/ath/ath12k"):
        if (cand / "dp_rx.c").is_file():
            return cand
    raise SystemExit("wbm-idle-log: cannot find ath12k under %s" % dest)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: wbm-idle-log.py <kernel tree>")
    d = ath12k_dir(sys.argv[1])
    edits = [(d / "dp_rx.c", DP_RX_ANCHOR, SNAP),
             (d / "wifi7/dp_rx.c", WIFI7_ANCHOR, POLL)]

    plan = []
    for path, anchor, block in edits:
        src = path.read_bytes().decode()
        if MARK in src:
            print("skip %s (already patched)" % path.name)
            continue
        if src.count(anchor) != 1:
            raise SystemExit("wbm-idle-log: %s anchor count=%d, refusing"
                             % (path.name, src.count(anchor)))
        plan.append((path, src.replace(anchor, anchor + block)))

    for path, text in plan:
        path.write_bytes(text.encode())
        print("ok wbm-idle-log: patched %s" % path.name)


if __name__ == "__main__":
    main()
