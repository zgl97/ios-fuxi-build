#!/usr/bin/env python3
"""r602: is the vdev started when the mgmt frame goes out, and on what channel.

r601 gave the frame a 4-byte aligned address and printed it: paddr ...a4 with a
well-formed header (b000 0000 a439 ... = auth, dur 0, addr1 = a4:39...).  The
firmware stayed alive and still never sent WMI_MGMT_TX_COMPLETION_EVENTID, so
neither alignment nor the frame bytes explain the silence.

What is left is the state the command is judged against.  A station vdev that
the driver believes is started but the firmware does not, or a command with no
channel to transmit on, are both dropped without an error -- and our early-start
path (r590 pre-starts the vdev, r598 skips later starts once fw_started is set)
is exactly the kind of bookkeeping that can drift out of step with the firmware.

So this round prints the state that the transmit decision depends on, and gives
the command the channel of the vdev's channel context instead of zero.  The
frequency change is a real change: if the round produces a completion, the
firmware wanted a channel; if it does not, the printed state says which of the
flags is wrong.
"""
import sys
from pathlib import Path

STATE_ANCHOR = "\tbuf_len = min_t(int, frame->len, WMI_MGMT_SEND_DOWNLD_LEN);\n"
STATE_PROBE = (
    "\tath12k_info(ab,\n"
    "\t\t    \"NWDIAG mgmt state vdev %u started %u fw_started %u chanctx %u cf %u scan %d alloc %#llx\\n\",\n"
    "\t\t    (unsigned int)arvif->vdev_id,\n"
    "\t\t    (unsigned int)arvif->is_started,\n"
    "\t\t    (unsigned int)arvif->fw_started,\n"
    "\t\t    arvif->chanctx.def.chan ? 1U : 0U,\n"
    "\t\t    arvif->chanctx.def.chan ? arvif->chanctx.def.chan->center_freq : 0U,\n"
    "\t\t    (int)ar->scan.state,\n"
    "\t\t    (unsigned long long)ar->allocated_vdev_map);\n"
)

FREQ_ANCHOR = "\tcmd->chanfreq = cpu_to_le32(ath12k_wmi_mgmt_get_freq(ar, info));\n"
FREQ_REPLACEMENT = (
    "\tcmd->chanfreq = cpu_to_le32(arvif->chanctx.def.chan ?\n"
    "\t\t\t\t    arvif->chanctx.def.chan->center_freq :\n"
    "\t\t\t\t    ath12k_wmi_mgmt_get_freq(ar, info));\n"
)


def insert(path: Path, anchor: str, probe: str, marker: str, label: str) -> None:
    src = path.read_text()
    if marker in src:
        print(f"mgmt-tx-state-diag: {label} already instrumented")
        return
    if src.count(anchor) != 1:
        raise SystemExit(f"mgmt-tx-state-diag: {label} anchor count {src.count(anchor)} (expected 1)")
    path.write_text(src.replace(anchor, probe + anchor, 1))
    print(f"mgmt-tx-state-diag: patched {label}")


def replace(path: Path, old: str, new: str, marker: str, label: str) -> None:
    src = path.read_text()
    if marker in src:
        print(f"mgmt-tx-state-diag: {label} already patched")
        return
    if src.count(old) != 1:
        raise SystemExit(f"mgmt-tx-state-diag: {label} anchor count {src.count(old)} (expected 1)")
    path.write_text(src.replace(old, new, 1))
    print(f"mgmt-tx-state-diag: patched {label}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-tx-state-diag.py <ath12kwifi-root>")
    root = Path(sys.argv[1])
    wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
    insert(wmi, STATE_ANCHOR, STATE_PROBE, "NWDIAG mgmt state vdev", "ath12k/wmi.c state")
    replace(wmi, FREQ_ANCHOR, FREQ_REPLACEMENT, "chanfreq = cpu_to_le32(arvif->chanctx", "ath12k/wmi.c chanfreq")


if __name__ == "__main__":
    main()
