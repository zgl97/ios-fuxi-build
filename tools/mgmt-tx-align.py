#!/usr/bin/env python3
"""r601: give the auth frame a 4-byte aligned DMA address.

r600 proved the firmware is alive while ignoring the authentication frame: it
answered the WMI_REQUEST_PDEV_STAT ping four times right after the frame was
queued, and it never sent WMI_MGMT_TX_COMPLETION_EVENTID.  So the frame was not
transmitted, and the command was not rejected in a way that yields an error.

The one anomalous field in that round is the frame's DMA address.  All three
attempts landed at paddr ...a3, three bytes past a 4-byte boundary, because the
mgmt skb's data pointer sits three bytes into its buffer.  A management frame
the endpoint reads at a misaligned address is the kind of thing a firmware drops
without a word, and ruling it out is cheap: relocate the frame inside its own
buffer so the mapped address is aligned, then map that address.

The second probe prints the frame's first bytes and its three addresses, so the
next round can tell "misaligned frame" apart from "well-formed frame that the
firmware still ignores".
"""
import sys
from pathlib import Path

ALIGN_ANCHOR = "\tpaddr = dma_map_single(ab->dev, skb->data, skb->len, DMA_TO_DEVICE);\n"
ALIGN_PROBE = (
    "\tif ((unsigned long)skb->data & 3) {\n"
    "\t\tunsigned int shift = 4 - ((unsigned long)skb->data & 3U);\n"
    "\n"
    "\t\tif (skb_tailroom(skb) >= shift) {\n"
    "\t\t\tmemmove(skb->data + shift, skb->data, skb->len);\n"
    "\t\t\tskb_reserve(skb, shift);\n"
    "\t\t}\n"
    "\t}\n"
    "\tath12k_info(ab, \"NWDIAG mac mgmt align %#lx mod %u\\n\",\n"
    "\t\t    (unsigned long)skb->data, (unsigned int)((unsigned long)skb->data & 3U));\n"
)

HEX_ANCHOR = "\t\t    (unsigned long long)ATH12K_SKB_CB(frame)->paddr);\n"
HEX_PROBE = (
    "\tath12k_info(ab,\n"
    "\t\t    \"NWDIAG wmi mgmt hex %02x%02x %02x%02x %02x%02x a1 %pM a2 %pM a3 %pM\\n\",\n"
    "\t\t    frame->data[0], frame->data[1], frame->data[2], frame->data[3],\n"
    "\t\t    frame->data[4], frame->data[5],\n"
    "\t\t    hdr->addr1, hdr->addr2, hdr->addr3);\n"
)


def patch(path: Path, anchor: str, probe: str, marker: str, label: str, before: bool) -> None:
    src = path.read_text()
    if marker in src:
        print(f"mgmt-tx-align: {label} already instrumented")
        return
    if src.count(anchor) != 1:
        raise SystemExit(f"mgmt-tx-align: {label} anchor count {src.count(anchor)} (expected 1)")
    new = probe + anchor if before else anchor + probe
    path.write_text(src.replace(anchor, new, 1))
    print(f"mgmt-tx-align: patched {label}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-tx-align.py <ath12kwifi-root>")
    root = Path(sys.argv[1])
    ath = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    patch(ath / "mac.c", ALIGN_ANCHOR, ALIGN_PROBE, "NWDIAG mac mgmt align",
          "ath12k/mac.c align", before=True)
    patch(ath / "wmi.c", HEX_ANCHOR, HEX_PROBE, "NWDIAG wmi mgmt hex",
          "ath12k/wmi.c hex", before=False)


if __name__ == "__main__":
    main()
