#!/usr/bin/env python3
"""r609: show what the firmware says about frames handed to the dp/tcl path.

r608 finally gets the AUTH frame into TCL (ath12k_wifi7_dp_tx() returns 0) but
the AP still answers nothing, so the next question is whether the firmware
consumed the TCL entry at all and, if it did, why the frame did not make it on
air.  Both answers live in the wbm release (tx completion) ring, which the
host drains in ath12k_wifi7_dp_tx_completion_handler():

  * a print at entry proves the handler runs and shows how many descriptors
    the ring holds (head/tail), so an empty ring is distinguishable from a
    handler that never fires;
  * a print per descriptor carries the parsed release source and tqm release
    reason plus peer id / try count / ack rssi, which is what a firmware-side
    drop reports.

Runs after mgmt-tx-dp-allow.py; idempotent via NWDIAG dp tx compl.
"""

import sys
import pathlib

MARK = "NWDIAG dp tx compl"

ENTRY_ANCHOR = "\tspin_lock_bh(&status_ring->lock);\n"

ENTRY_PROBE = (
    "\tath12k_info(ab, \"NWDIAG dp tx ring %d head %u tail %u\\n\",\n"
    "\t\t    ring_id, tx_ring->tx_status_head, tx_ring->tx_status_tail);\n"
)

PARSE_ANCHOR = "\t\tath12k_wifi7_dp_tx_status_parse(dp, tx_status, &ts);\n"

PARSE_PROBE = (
    "\t\tath12k_info(ab,\n"
    "\t\t\t    \"" + MARK + " src %u reason %u peer %u try %u rssi %d flags 0x%x\\n\",\n"
    "\t\t\t    (u32)ts.buf_rel_source, (u32)ts.status,\n"
    "\t\t\t    (u32)ts.peer_id, (u32)ts.try_cnt, (int)ts.ack_rssi,\n"
    "\t\t\t    ts.flags);\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: dp-tx-compl-log.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/wifi7/dp_tx.c")
    src = path.read_text()

    if MARK in src:
        print("dp-tx-compl-log: already patched")
        return

    if src.count(ENTRY_ANCHOR) != 1:
        raise SystemExit("dp-tx-compl-log: completion entry anchor not unique")
    if src.count(PARSE_ANCHOR) != 1:
        raise SystemExit("dp-tx-compl-log: status parse anchor not unique")

    src = src.replace(ENTRY_ANCHOR, ENTRY_PROBE + ENTRY_ANCHOR)
    src = src.replace(PARSE_ANCHOR, PARSE_ANCHOR + PARSE_PROBE)
    path.write_text(src)
    print("dp-tx-compl-log: patched ath12k/wifi7/dp_tx.c (tx completion)")


if __name__ == "__main__":
    main()
