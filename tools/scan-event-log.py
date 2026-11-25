#!/usr/bin/env python3
"""Log every WMI scan event at info level (r565)."""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
s = path.read_text(encoding="utf-8")

marker = "static void ath12k_scan_event(struct ath12k_base *ab, struct sk_buff *skb)\n{"
pos = s.find(marker)
if pos < 0:
    raise SystemExit("ath12k_scan_event not found")

# Log every event right after the scan-event lock is taken.
anchor = "\tspin_lock_bh(&ar->data_lock);\n"
idx = s.find(anchor, pos)
if idx < 0:
    raise SystemExit("scan_event data_lock not found")
insert = (
    "\n\tath12k_info(ab, \"NWDIAG scan ev type %u reason %u freq %u req %u scan %u vdev %u state %d\\n\",\n"
    "\t\t    le32_to_cpu(scan_ev.event_type), le32_to_cpu(scan_ev.reason),\n"
    "\t\t    le32_to_cpu(scan_ev.channel_freq), le32_to_cpu(scan_ev.scan_req_id),\n"
    "\t\t    le32_to_cpu(scan_ev.scan_id), le32_to_cpu(scan_ev.vdev_id),\n"
    "\t\t    ar->scan.state);\n"
)
s = s[: idx + len(anchor)] + insert + s[idx + len(anchor):]

# Log events for an unknown vdev too: those are silently dropped today.
old = """\tif (!ar) {
\t\tath12k_warn(ab, "Received scan event for unknown vdev");
"""
new = """\tif (!ar) {
\t\tath12k_info(ab, "NWDIAG scan ev unknown vdev %u type %u\\n",
\t\t\t    le32_to_cpu(scan_ev.vdev_id),
\t\t\t    le32_to_cpu(scan_ev.event_type));
\t\tath12k_warn(ab, "Received scan event for unknown vdev");
"""
if old not in s:
    raise SystemExit("unknown vdev block not found")
s = s.replace(old, new, 1)

path.write_text(s, encoding="utf-8")
print("scan-event-log: patched wmi.c")
