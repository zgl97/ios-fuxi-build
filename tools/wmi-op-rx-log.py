#!/usr/bin/env python3
"""Add temporary WMI/scan tracing to the staged ath12k sources."""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
mac = root / "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"

s = wmi.read_text(encoding="utf-8")
old = """\tcmd_hdr = (struct wmi_cmd_hdr *)skb->data;
\tid = le32_get_bits(cmd_hdr->cmd_id, WMI_CMD_HDR_CMD_ID);

\tif (!skb_pull(skb, sizeof(struct wmi_cmd_hdr)))
"""
new = """\tcmd_hdr = (struct wmi_cmd_hdr *)skb->data;
\tid = le32_get_bits(cmd_hdr->cmd_id, WMI_CMD_HDR_CMD_ID);
\t{
\t\tstatic int nw_wmi_rx_log;
\t\tif (nw_wmi_rx_log < 400) {
\t\t\tnw_wmi_rx_log++;
\t\t\tath12k_info(ab, "wmi op rx cmd 0x%x len %d\\n", id, skb->len);
\t\t}
\t}

\tif (!skb_pull(skb, sizeof(struct wmi_cmd_hdr)))
"""
if old not in s:
    raise SystemExit("wmi.c pattern not found")
wmi.write_text(s.replace(old, new, 1), encoding="utf-8")

s = mac.read_text(encoding="utf-8")
old = """\tret = ath12k_wmi_send_scan_start_cmd(ar, arg);
\tif (ret)
\t\treturn ret;

\tret = wait_for_completion_timeout(&ar->scan.started, 1 * HZ);
\tif (ret == 0) {
"""
new = """\tret = ath12k_wmi_send_scan_start_cmd(ar, arg);
\tath12k_info(ar->ab, "scan start send %d\\n", ret);
\tif (ret)
\t\treturn ret;

\tret = wait_for_completion_timeout(&ar->scan.started, 1 * HZ);
\tath12k_info(ar->ab, "scan start wait %d state %d\\n", ret, ar->scan.state);
\tif (ret == 0) {
"""
if old not in s:
    raise SystemExit("mac.c pattern not found")
mac.write_text(s.replace(old, new, 1), encoding="utf-8")
print("wmi-op-rx-log: patched")

