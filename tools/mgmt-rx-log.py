#!/usr/bin/env python3
"""r586: count the management frames the firmware hands back during a scan.

r585 scanned all thirteen channels and the supplicant ended up with fourteen
BSS, but earlier boots scanned the same channels and produced none.  Whether
beacons and probe responses reach the host at all is the one thing the console
never said, so print one NWDIAG line per management frame with the pdev, the
channel and the frame length.
"""
import sys
from pathlib import Path

ANCHOR = (
    '\tath12k_dbg(ab, ATH12K_DBG_MGMT, "mgmt rx event status %08x\\n",\n'
    "\t\t   rx_ev.status);\n"
)
PROBE = (
    "\tath12k_info(ab, \"NWDIAG mgmt rx pdev %u chan %u freq %u status 0x%x len %u jiffies %lu\\n\",\n"
    "\t\t    rx_ev.pdev_id, rx_ev.channel, rx_ev.chan_freq, rx_ev.status,\n"
    "\t\t    skb->len, jiffies);\n"
)
MARKER = "NWDIAG mgmt rx pdev"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-rx-log.py <ath12kwifi-root>")
    path = (
        Path(sys.argv[1])
        / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
    )
    src = path.read_text()
    if MARKER in src:
        print("mgmt-rx-log: already instrumented")
        return
    if src.count(ANCHOR) != 1:
        raise SystemExit(f"mgmt-rx-log: anchor count {src.count(ANCHOR)} (expected 1)")
    path.write_text(src.replace(ANCHOR, ANCHOR + PROBE))
    print("mgmt-rx-log: patched ath12k/wmi.c")


if __name__ == "__main__":
    main()
