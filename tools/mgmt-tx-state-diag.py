#!/usr/bin/env python3
"""r602: is the vdev started when the mgmt frame goes out.

r601 gave the frame a 4-byte aligned address and printed it: paddr ...a4 with a
well-formed header (b000 0000 a439 ... = auth, dur 0, addr1 = a4:39...).  The
firmware stayed alive and still never sent WMI_MGMT_TX_COMPLETION_EVENTID, so
neither alignment nor the frame bytes explain the silence.

What is left is the state the command is judged against, and our early-start
path (r590 pre-starts the vdev, r598 skips later starts once fw_started is set)
is exactly where driver bookkeeping and firmware state can drift apart.  A
station vdev the driver believes is started but the firmware does not is
dropped without an error, which is what we see.

Only fields that exist on struct ath12k_link_vif / struct ath12k are read here
(the first attempt of this script also rewrote chanfreq from
arvif->chanctx and failed to compile: that copy of the channel context lives on
struct ath12k_vif, not on the link vif, and nothing in this tree keeps it
current).
"""
import sys
from pathlib import Path

STATE_ANCHOR = "\tbuf_len = min_t(int, frame->len, WMI_MGMT_SEND_DOWNLD_LEN);\n"
STATE_PROBE = (
    "\tath12k_info(ab,\n"
    "\t\t    \"NWDIAG mgmt state vdev %u created %u started %u fw_started %u up %u scan %d alloc %#llx\\n\",\n"
    "\t\t    (unsigned int)arvif->vdev_id,\n"
    "\t\t    (unsigned int)arvif->is_created,\n"
    "\t\t    (unsigned int)arvif->is_started,\n"
    "\t\t    (unsigned int)arvif->fw_started,\n"
    "\t\t    (unsigned int)arvif->is_up,\n"
    "\t\t    (int)ar->scan.state,\n"
    "\t\t    (unsigned long long)ar->allocated_vdev_map);\n"
)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-tx-state-diag.py <ath12kwifi-root>")
    root = Path(sys.argv[1])
    wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
    src = wmi.read_text()
    if "NWDIAG mgmt state vdev" in src:
        print("mgmt-tx-state-diag: ath12k/wmi.c already instrumented")
        return
    if src.count(STATE_ANCHOR) != 1:
        raise SystemExit(
            f"mgmt-tx-state-diag: anchor count {src.count(STATE_ANCHOR)} (expected 1)"
        )
    wmi.write_text(src.replace(STATE_ANCHOR, STATE_PROBE + STATE_ANCHOR, 1))
    print("mgmt-tx-state-diag: patched ath12k/wmi.c state")


if __name__ == "__main__":
    main()
