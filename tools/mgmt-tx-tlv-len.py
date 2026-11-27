#!/usr/bin/env python3
"""r604: make the WMI_MGMT_TX_SEND header length match the frame TLV offset.

r603 dumps the command as the firmware receives it.  Decoding the two words with
this tree's own masks (WMI_TLV_LEN is the low half, WMI_TLV_TAG the high half):

  w0 = 01a60020  tag 0x01a6 = WMI_TAG_MGMT_TX_SEND_CMD, len 0x0020 = 32
  w1..w7         vdev 0, desc 0/1/2, chanfreq 0, paddr, frame_len 30, buf_len 30
  w8 = 00000000  tx_params_valid
  w9 = 00110020  tag 0x0011 = WMI_TAG_ARRAY_BYTE, len 32 = the frame TLV

The frame TLV therefore sits at offset 36 while the command header claims the
command is 32 bytes long.  A firmware that trusts the header looks for the next
TLV at 32 - where our tx_params_valid word lives - finds tag 0 len 0, so the
frame array is empty.  An empty mgmt frame is dropped without an error, which is
exactly the silence since r599: command accepted, firmware alive, no completion.

sizeof(struct wmi_mgmt_send_cmd) should be 36 (nine __le32 fields, __packed), so
the 32 means the compiled view of this struct disagrees with the source the
patch chain edits - the ath12k sources are translated into fxa_* files before
they are compiled, and a dropped or reordered field there is invisible here.

Rather than guess which side is wrong, set the header from the address the frame
TLV actually has, and print sizeof/offsetof so the next round can confirm the
compiled layout.
"""
import sys
from pathlib import Path

TLV_ANCHOR = "\tframe_tlv = (struct wmi_tlv *)(skb->data + sizeof(*cmd));\n"
TLV_FIX = (
    "\tframe_tlv = (struct wmi_tlv *)(skb->data + sizeof(*cmd));\n"
    "\t{\n"
    "\t\tu32 tlv_off = (u32)((unsigned char *)frame_tlv - (unsigned char *)cmd);\n"
    "\n"
    "\t\tcmd->tlv_header = ath12k_wmi_tlv_cmd_hdr(WMI_TAG_MGMT_TX_SEND_CMD, tlv_off);\n"
    "\t\tath12k_info(ab,\n"
    "\t\t\t    \"NWDIAG mgmt tlvfix off %u sizeof %u fl %u bl %u tpv %u\\n\",\n"
    "\t\t\t    tlv_off, (u32)sizeof(*cmd),\n"
    "\t\t\t    (u32)offsetof(struct wmi_mgmt_send_cmd, frame_len),\n"
    "\t\t\t    (u32)offsetof(struct wmi_mgmt_send_cmd, buf_len),\n"
    "\t\t\t    (u32)offsetof(struct wmi_mgmt_send_cmd, tx_params_valid));\n"
    "\t}\n"
)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-tx-tlv-len.py <ath12kwifi-root>")
    root = Path(sys.argv[1])
    wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
    src = wmi.read_text()
    if "NWDIAG mgmt tlvfix" in src:
        print("mgmt-tx-tlv-len: ath12k/wmi.c already patched")
        return
    if src.count(TLV_ANCHOR) != 1:
        raise SystemExit(f"mgmt-tx-tlv-len: anchor count {src.count(TLV_ANCHOR)} (expected 1)")
    wmi.write_text(src.replace(TLV_ANCHOR, TLV_FIX, 1))
    print("mgmt-tx-tlv-len: patched ath12k/wmi.c command length")


if __name__ == "__main__":
    main()
