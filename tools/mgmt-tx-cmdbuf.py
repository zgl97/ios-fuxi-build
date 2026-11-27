#!/usr/bin/env python3
"""r603: print the whole WMI_MGMT_TX_SEND command the firmware receives.

r602 shows whether the vdev is started when the frame goes out, but the command
itself has only ever been inspected field by field in separate probes.  If the
firmware silently rejects the command, everything upstream of it looks healthy
and the completion event never comes - which is exactly the state we are in
since r599: `wmi mgmt send ret 0`, live stats events, no
WMI_MGMT_TX_COMPLETION_EVENTID.

This dumps the first 64 bytes of the command buffer as it goes on the wire:
tlv header (tag/len), vdev_id, desc_id, chanfreq, paddr lo/hi, frame_len,
buf_len, tx_params_valid, then the frame TLV header and the first frame bytes.
Reading it tells whether the descriptor the firmware sees matches what the
per-field probes claim, and whether any field sits at the wrong offset.

The dump reads 32-bit words instead of formatting bytes, because the shim's
stdio surface is thin and a sprintf here would cost a build round to discover.
"""
import sys
from pathlib import Path

ANCHOR = "\tret = ath12k_wmi_cmd_send(wmi, skb, WMI_MGMT_TX_SEND_CMDID);\n"
PROBE = (
    "\t{\n"
    "\t\tconst u32 *cw = (const u32 *)skb->data;\n"
    "\t\tint cwn = len / 4;\n"
    "\n"
    "\t\tath12k_info(ab, \"NWDIAG mgmt cmdbuf len %d w 0 %08x %08x %08x %08x %08x %08x %08x %08x\\n\",\n"
    "\t\t\t    len, cwn > 0 ? cw[0] : 0, cwn > 1 ? cw[1] : 0,\n"
    "\t\t\t    cwn > 2 ? cw[2] : 0, cwn > 3 ? cw[3] : 0,\n"
    "\t\t\t    cwn > 4 ? cw[4] : 0, cwn > 5 ? cw[5] : 0,\n"
    "\t\t\t    cwn > 6 ? cw[6] : 0, cwn > 7 ? cw[7] : 0);\n"
    "\t\tath12k_info(ab, \"NWDIAG mgmt cmdbuf w 1 %08x %08x %08x %08x %08x %08x %08x %08x\\n\",\n"
    "\t\t\t    cwn > 8 ? cw[8] : 0, cwn > 9 ? cw[9] : 0,\n"
    "\t\t\t    cwn > 10 ? cw[10] : 0, cwn > 11 ? cw[11] : 0,\n"
    "\t\t\t    cwn > 12 ? cw[12] : 0, cwn > 13 ? cw[13] : 0,\n"
    "\t\t\t    cwn > 14 ? cw[14] : 0, cwn > 15 ? cw[15] : 0);\n"
    "\t}\n"
)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-tx-cmdbuf.py <ath12kwifi-root>")
    root = Path(sys.argv[1])
    wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
    src = wmi.read_text()
    if "NWDIAG mgmt cmdbuf" in src:
        print("mgmt-tx-cmdbuf: ath12k/wmi.c already instrumented")
        return
    if src.count(ANCHOR) != 1:
        raise SystemExit(f"mgmt-tx-cmdbuf: anchor count {src.count(ANCHOR)} (expected 1)")
    wmi.write_text(src.replace(ANCHOR, PROBE + ANCHOR, 1))
    print("mgmt-tx-cmdbuf: patched ath12k/wmi.c command dump")


if __name__ == "__main__":
    main()
