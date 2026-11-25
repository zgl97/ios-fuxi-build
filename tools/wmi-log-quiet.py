#!/usr/bin/env python3
"""Make the WMI/CE receive tracing cheap enough for the serial console.

r563 boot.log showed the firmware emitting a burst of 106 WMI_DIAG_EVENTID
(0x1d011) messages of ~1.5 KB each immediately after the first scan, and then
going completely silent: no vdev-delete response, no vdev-start response, no
HTT PEER_MAP.  The console is slow, and the rx work item prints every WMI
event and every CE rx buffer before the CE ring is replenished, so the burst
can hold the CE2 ring hostage long enough for the firmware to block on its
diag pipe.

Keep the rare, useful events; drop the per-event logging for the diag flood
and shrink the CE sampling windows.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
ce = root / "deps/linux/drivers/net/wireless/ath/ath12k/ce.c"


def replace(path, old, new, label):
    s = path.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"{label}: pattern not found in {path}")
    path.write_text(s.replace(old, new, 1), encoding="utf-8")


old_block = """\t{
\t\tstatic int nw_wmi_rx_log;
\t\tif (nw_wmi_rx_log < 400) {
\t\t\tnw_wmi_rx_log++;
\t\t\tath12k_info(ab, "wmi op rx cmd 0x%x len %d\\n", id, skb->len);
\t\t}
\t\tif (id == 0x1d011) {
\t\t\tstatic int nw_diag_dump;
\t\t\tif (nw_diag_dump++ < 4 && skb->len >= sizeof(*cmd_hdr) + 32) {
\t\t\t\tu32 d[8];
\t\t\t\tmemcpy(d, skb->data + sizeof(*cmd_hdr), sizeof(d));
\t\t\t\tath12k_info(ab, "diag %08x %08x %08x %08x %08x %08x %08x %08x\\n",
\t\t\t\t\t    d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7]);
\t\t\t}
\t\t}
\t}
"""

new_block = """\t{
\t\tstatic int nw_wmi_rx_log;
\t\tstatic int nw_diag_evt;
\t\tif (id == 0x1d011) {
\t\t\tnw_diag_evt++;
\t\t\tif (nw_diag_evt <= 2 || (nw_diag_evt % 500) == 0)
\t\t\t\tath12k_info(ab, "wmi diag evt %d len %d\\n",
\t\t\t\t\t    nw_diag_evt, skb->len);
\t\t} else if (nw_wmi_rx_log < 120) {
\t\t\tnw_wmi_rx_log++;
\t\t\tath12k_info(ab, "wmi op rx cmd 0x%x len %d\\n", id, skb->len);
\t\t}
\t}
"""

replace(wmi, old_block, new_block, "wmi.c quiet")

replace(ce, "nw_ce_service_count[ce_id]++ < 40",
       "nw_ce_service_count[ce_id]++ < 12", "ce.c service cap")
replace(ce, "nw_ce_rx_count[pipe->pipe_num]++ < 40",
       "nw_ce_rx_count[pipe->pipe_num]++ < 6", "ce.c rx cap")
replace(ce, "nw_ce_tx_count[pipe->pipe_num]++ < 40",
       "nw_ce_tx_count[pipe->pipe_num]++ < 12", "ce.c tx cap")

print("wmi-log-quiet: patched wmi.c/ce.c")
