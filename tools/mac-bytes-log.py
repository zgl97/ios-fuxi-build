#!/usr/bin/env python3
"""r611: print MAC addresses byte by byte instead of %pM.

r610 put the header probe in place, but this kernel's kprintf shim does not
implement %pM: the argument falls through to %p and the log shows a pointer
with a literal "M" glued on the end

    a1 0xfffffe30c821c0a7M a2 0xfffffe30c821c0adM ...

so the three addresses that matter (addr1 = AP, addr2 = our own vdev MAC,
addr3 = AP again for an auth frame) never made it into the log.  The offsets
were fine -- a2 is a1 + 6, a3 is a2 + 6 -- so the only missing piece is the
value itself.

This script rewrites both r610 probes to print each byte with %02x and keeps
the rest of the line unchanged, so the r611 log can answer:

  * addr1 == a4:39:b3:ca:42:7c (the AP we scan found) ?
  * addr2 == 02:11:22:33:44:55 (the MAC wpa_supplicant reports as "Own MAC") ?
  * does the mgmt rx path ever see fc 0x00b0 ?

Runs after mgmt-rx-hdr-log.py; idempotent via NWDIAG mac bytes.
"""

import sys
import pathlib

MARK = "NWDIAG mac bytes"

OLD_AUTH = (
    "\t\t\t\tath12k_info(ar->ab,\n"
    "\t\t\t\t\t    \"NWDIAG mgmt frame fc 0x%04x a1 %pM a2 %pM a3 %pM seq 0x%04x len %u\\n\",\n"
    "\t\t\t\t\t    le16_to_cpu(hdr->frame_control), hdr->addr1,\n"
    "\t\t\t\t\t    hdr->addr2, hdr->addr3,\n"
    "\t\t\t\t\t    le16_to_cpu(hdr->seq_ctrl), skb->len);\n"
)

NEW_AUTH = (
    "\t\t\t\tath12k_info(ar->ab,\n"
    "\t\t\t\t\t    \"" + MARK + " tx fc 0x%04x seq 0x%04x len %u"
    " a1 %02x:%02x:%02x:%02x:%02x:%02x"
    " a2 %02x:%02x:%02x:%02x:%02x:%02x"
    " a3 %02x:%02x:%02x:%02x:%02x:%02x\\n\",\n"
    "\t\t\t\t\t    le16_to_cpu(hdr->frame_control),\n"
    "\t\t\t\t\t    le16_to_cpu(hdr->seq_ctrl), skb->len,\n"
    "\t\t\t\t\t    hdr->addr1[0], hdr->addr1[1], hdr->addr1[2],\n"
    "\t\t\t\t\t    hdr->addr1[3], hdr->addr1[4], hdr->addr1[5],\n"
    "\t\t\t\t\t    hdr->addr2[0], hdr->addr2[1], hdr->addr2[2],\n"
    "\t\t\t\t\t    hdr->addr2[3], hdr->addr2[4], hdr->addr2[5],\n"
    "\t\t\t\t\t    hdr->addr3[0], hdr->addr3[1], hdr->addr3[2],\n"
    "\t\t\t\t\t    hdr->addr3[3], hdr->addr3[4], hdr->addr3[5]);\n"
)

OLD_RX = (
    "\tif (skb->len >= 24)\n"
    "\t\tath12k_info(ab,\n"
    "\t\t\t    \"NWDIAG mgmt rx hdr fc 0x%04x a1 %pM a2 %pM a3 %pM seq 0x%04x len %u\\n\",\n"
    "\t\t\t    le16_to_cpu(((struct ieee80211_hdr *)skb->data)->frame_control),\n"
    "\t\t\t    ((struct ieee80211_hdr *)skb->data)->addr1,\n"
    "\t\t\t    ((struct ieee80211_hdr *)skb->data)->addr2,\n"
    "\t\t\t    ((struct ieee80211_hdr *)skb->data)->addr3,\n"
    "\t\t\t    le16_to_cpu(((struct ieee80211_hdr *)skb->data)->seq_ctrl),\n"
    "\t\t\t    skb->len);\n"
)

NEW_RX = (
    "\tif (skb->len >= 24) {\n"
    "\t\tconst struct ieee80211_hdr *rh =\n"
    "\t\t\t(const struct ieee80211_hdr *)skb->data;\n"
    "\n"
    "\t\tath12k_info(ab,\n"
    "\t\t\t    \"" + MARK + " rx fc 0x%04x seq 0x%04x len %u"
    " a1 %02x:%02x:%02x:%02x:%02x:%02x"
    " a2 %02x:%02x:%02x:%02x:%02x:%02x"
    " a3 %02x:%02x:%02x:%02x:%02x:%02x\\n\",\n"
    "\t\t\t    le16_to_cpu(rh->frame_control),\n"
    "\t\t\t    le16_to_cpu(rh->seq_ctrl), skb->len,\n"
    "\t\t\t    rh->addr1[0], rh->addr1[1], rh->addr1[2],\n"
    "\t\t\t    rh->addr1[3], rh->addr1[4], rh->addr1[5],\n"
    "\t\t\t    rh->addr2[0], rh->addr2[1], rh->addr2[2],\n"
    "\t\t\t    rh->addr2[3], rh->addr2[4], rh->addr2[5],\n"
    "\t\t\t    rh->addr3[0], rh->addr3[1], rh->addr3[2],\n"
    "\t\t\t    rh->addr3[3], rh->addr3[4], rh->addr3[5]);\n"
    "\t}\n"
)


def patch(path, old, new, tag):
    src = path.read_text()
    if MARK in src:
        print(f"mac-bytes-log: {tag} already patched")
        return
    if src.count(old) != 1:
        raise SystemExit(f"mac-bytes-log: {tag} anchor not unique "
                         f"(count={src.count(old)})")
    path.write_text(src.replace(old, new))
    print(f"mac-bytes-log: patched {tag} (byte-wise MAC)")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: mac-bytes-log.py <Ath12kWifi-root>")

    root = (pathlib.Path(sys.argv[1]) /
            "deps/linux/drivers/net/wireless/ath/ath12k")

    patch(root / "wifi7/hw.c", OLD_AUTH, NEW_AUTH, "ath12k/wifi7/hw.c")
    patch(root / "wmi.c", OLD_RX, NEW_RX, "ath12k/wmi.c")


if __name__ == "__main__":
    main()
