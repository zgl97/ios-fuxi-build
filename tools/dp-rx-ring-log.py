#!/usr/bin/env python3
"""r612 (1/4): count how often the WiFi7 RX ring is polled at all.

r611 watched `ath12k_dp_rx_deliver_msdu()`, which r612's sibling probe now
widens, but a zero there has two very different meanings: "the firmware never
handed us a frame" and "nobody ever polled the RX ring".  This prints the poll
counter at the entry of `ath12k_wifi7_dp_rx_process()`, the one function the
NAPI handler calls when the REO destination ring raises an interrupt
(wifi7/dp.c:55), so the next log separates those two cases by itself.

Idempotent via the NWDIAG rx poll marker.
"""

import sys
import pathlib

MARK = "NWDIAG rx poll"

ANCHOR = "\tbool done = false;\n\tu64 desc_va;\n"

PROBE = (
    "\t{\n"
    "\t\tstatic u32 dbg_poll;\n"
    "\n"
    "\t\tdbg_poll++;\n"
    "\t\tif (dbg_poll <= 5 || !(dbg_poll & 511))\n"
    "\t\t\tath12k_info(ab, \"" + MARK + " n %u ring %d budget %d\\n\",\n"
    "\t\t\t\t    dbg_poll, ring_id, budget);\n"
    "\t}\n"
)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: dp-rx-ring-log.py <Ath12kWifi-root>")

    path = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k/wifi7/dp_rx.c")
    src = path.read_text()

    if MARK in src:
        print("dp-rx-ring-log: already patched")
        return

    if src.count(ANCHOR) != 1:
        raise SystemExit("dp-rx-ring-log: process anchor not unique "
                         f"(count={src.count(ANCHOR)})")

    path.write_text(src.replace(ANCHOR, ANCHOR + PROBE))
    print("dp-rx-ring-log: patched ath12k/wifi7/dp_rx.c (poll counter)")


if __name__ == "__main__":
    main()
