#!/usr/bin/env python3
"""r610: stop the tx completion entry print from drowning the console.

r609 wanted to know whether the wbm release handler runs at all, so it printed
head/tail on every pass.  The answer came back loud: 14,382 lines of

    NWDIAG dp tx ring 0 head 1 tail 0

which is the *empty* ring -- the loop condition in the handler is
ATH12K_TX_COMPL_NEXT(ab, head) != tail, so head 1 / tail 0 means "nothing to
drain", not "one entry stuck".  A print on every pass therefore carries no
information after the first one and only perturbs timing on the tx hot path.

Print only when head or tail actually moves (every transition is kept), plus a
coarse heartbeat so a completely silent ring is still distinguishable from a
handler that stopped being called.

Runs after dp-tx-compl-log.py; idempotent via NWDIAG dp tx ring (throttled).
"""

import sys
import pathlib

MARK = "NWDIAG dp tx ring (throttled)"

OLD = (
    "\tath12k_info(ab, \"NWDIAG dp tx ring %d head %u tail %u\\n\",\n"
    "\t\t    ring_id, tx_ring->tx_status_head, tx_ring->tx_status_tail);\n"
)

NEW = (
    "\t{\n"
    "\t\tstatic u32 dbg_h[8], dbg_t[8], dbg_n[8];\n"
    "\t\tu32 dbg_i = (u32)ring_id & 7;\n"
    "\t\tbool dbg_chg = tx_ring->tx_status_head != dbg_h[dbg_i] ||\n"
    "\t\t\t       tx_ring->tx_status_tail != dbg_t[dbg_i];\n"
    "\n"
    "\t\tdbg_h[dbg_i] = tx_ring->tx_status_head;\n"
    "\t\tdbg_t[dbg_i] = tx_ring->tx_status_tail;\n"
    "\t\tif (dbg_chg || !(dbg_n[dbg_i] & 0x3ff))\n"
    "\t\t\tath12k_info(ab,\n"
    "\t\t\t\t    \"" + MARK + " %d head %u tail %u n %u\\n\",\n"
    "\t\t\t\t    ring_id, tx_ring->tx_status_head,\n"
    "\t\t\t\t    tx_ring->tx_status_tail, dbg_n[dbg_i]);\n"
    "\t\tdbg_n[dbg_i]++;\n"
    "\t}\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: dp-tx-ring-throttle.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/wifi7/dp_tx.c")
    src = path.read_text()

    if MARK in src:
        print("dp-tx-ring-throttle: already patched")
        return

    if src.count(OLD) != 1:
        raise SystemExit("dp-tx-ring-throttle: ring print not unique")

    src = src.replace(OLD, NEW)
    path.write_text(src)
    print("dp-tx-ring-throttle: patched ath12k/wifi7/dp_tx.c (throttled ring)")


if __name__ == "__main__":
    main()
