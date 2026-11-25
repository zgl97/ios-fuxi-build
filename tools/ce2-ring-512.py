#!/usr/bin/env python3
"""Grow the WMI receive ring (CE2) so a diag burst cannot fill it.

The wcn7850 table allocates only 64 destination entries (2 KB each) for the
WMI pipe.  The debug firmware emitted 106 WMI_DIAG_EVENTID messages of about
1.5 KB right after the first scan, i.e. more than the ring can hold at once.
Use the same 512-entry size as the HTT pipe so the burst can be buffered while
the host drains it.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "deps/linux/drivers/net/wireless/ath/ath12k/wifi7/ce.c"
s = path.read_text(encoding="utf-8")

old = """\t/* CE2: target->host WMI */
\t{
\t\t.flags = CE_ATTR_FLAGS,
\t\t.src_nentries = 0,
\t\t.src_sz_max = 2048,
\t\t.dest_nentries = 64,
\t\t.recv_cb = ath12k_htc_rx_completion_handler,
\t},
"""

new = """\t/* CE2: target->host WMI */
\t{
\t\t.flags = CE_ATTR_FLAGS,
\t\t.src_nentries = 0,
\t\t.src_sz_max = 2048,
\t\t.dest_nentries = 512,
\t\t.recv_cb = ath12k_htc_rx_completion_handler,
\t},
"""

if old not in s:
    raise SystemExit("wcn7850 CE2 block not found")
s = s.replace(old, new, 1)
path.write_text(s, encoding="utf-8")
print("ce2-ring-512: patched wifi7/ce.c")
