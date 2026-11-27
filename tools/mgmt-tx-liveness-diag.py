#!/usr/bin/env python3
"""r600: decide whether the auth frame killed the firmware or was dropped.

r599 got the authentication frame out through WMI_MGMT_TX_SEND_CMDID for the
first time (three attempts, all returning 0) and then nothing: no
WMI_MGMT_TX_COMPLETION_EVENTID, no further event of any kind, and the
authentication timed out at the mac80211 layer.

Two explanations fit that silence and they need different fixes, so this probe
separates them in a single boot:

  * the frame's DMA address is bogus and the firmware died reading it, or
  * the firmware is alive and quietly rejected the command (vdev/channel state).

So it does three things:
  1. prints the DMA address that goes into paddr_lo/paddr_hi, plus the buffer
     id and the frame's control/auth fields, so a bogus address (zero, or one
     outside the board's IOVA arena) is visible immediately;
  2. fires one WMI_REQUEST_PDEV_STAT right after the mgmt frame is queued -- a
     request the firmware answers with WMI_UPDATE_STATS_EVENTID, which is a
     liveness ping that costs nothing when the firmware is healthy;
  3. prints the arrival of that stats event, so "firmware alive" is a log line
     rather than an inference from silence.

Run it after mgmt-tx-diag.py: the wmi.c anchor is the probe that script adds.
"""
import sys
from pathlib import Path

# mac.c: the DMA address handed to the firmware.
PADDR_ANCHOR = "\tskb_cb->paddr = paddr;\n"
PADDR_PROBE = (
    "\tath12k_info(ab,\n"
    "\t\t    \"NWDIAG mac mgmt paddr %#llx buf %d len %u flags %#x\\n\",\n"
    "\t\t    (unsigned long long)paddr, buf_id, skb->len,\n"
    "\t\t    IEEE80211_SKB_CB(skb)->flags);\n"
)

# mac.c: the liveness ping, sent from the same work context as the frame.
PING_ANCHOR = "\t\t\tath12k_info(ar->ab, \"NWDIAG mgmt_work tx_wmi ret %d\\n\", ret);\n"
PING_PROBE = (
    "\t\t\tif (!ret && ar->pdev) {\n"
    "\t\t\t\tint sret;\n"
    "\n"
    "\t\t\t\tsret = ath12k_wmi_send_stats_request_cmd(ar, WMI_REQUEST_PDEV_STAT, 0,\n"
    "\t\t\t\t\t\t\t\t\t  ar->pdev->pdev_id);\n"
    "\t\t\t\tath12k_info(ar->ab, \"NWDIAG wmi stats req ret %d\\n\", sret);\n"
    "\t\t\t}\n"
)

# wmi.c: the fields the firmware will act on, including the frame itself.
FRAME_ANCHOR = "\t\t    le32_to_cpu(cmd->chanfreq));\n"
FRAME_PROBE = (
    "\tif (ieee80211_is_auth(hdr->frame_control)) {\n"
    "\t\tath12k_info(ab,\n"
    "\t\t\t    \"NWDIAG wmi mgmt auth alg %u tx %u st %u paddr %#llx\\n\",\n"
    "\t\t\t    le16_to_cpu(mgmt->u.auth.auth_alg),\n"
    "\t\t\t    le16_to_cpu(mgmt->u.auth.auth_transaction),\n"
    "\t\t\t    le16_to_cpu(mgmt->u.auth.status_code),\n"
    "\t\t\t    (unsigned long long)ATH12K_SKB_CB(frame)->paddr);\n"
    "\t}\n"
)

# wmi.c: proof that the firmware still answers commands.
STATS_ANCHOR = "\tath12k_dbg(ab, ATH12K_DBG_WMI, \"event update stats\");\n"
STATS_PROBE = "\tath12k_info(ab, \"NWDIAG wmi stats rx pdev_id %d\\n\", stats.pdev_id);\n"


def patch_after(path: Path, anchor: str, probe: str, marker: str, label: str) -> None:
    src = path.read_text()
    if marker in src:
        print(f"mgmt-tx-liveness-diag: {label} already instrumented")
        return
    if src.count(anchor) != 1:
        raise SystemExit(
            f"mgmt-tx-liveness-diag: {label} anchor count {src.count(anchor)} (expected 1)"
        )
    path.write_text(src.replace(anchor, anchor + probe))
    print(f"mgmt-tx-liveness-diag: patched {label}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mgmt-tx-liveness-diag.py <ath12kwifi-root>")
    root = Path(sys.argv[1])
    ath = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    mac = ath / "mac.c"
    wmi = ath / "wmi.c"
    patch_after(mac, PADDR_ANCHOR, PADDR_PROBE, "NWDIAG mac mgmt paddr", "ath12k/mac.c paddr")
    patch_after(mac, PING_ANCHOR, PING_PROBE, "NWDIAG wmi stats req", "ath12k/mac.c ping")
    patch_after(wmi, FRAME_ANCHOR, FRAME_PROBE, "NWDIAG wmi mgmt auth", "ath12k/wmi.c frame")
    patch_after(wmi, STATS_ANCHOR, STATS_PROBE, "NWDIAG wmi stats rx", "ath12k/wmi.c stats")


if __name__ == "__main__":
    main()
