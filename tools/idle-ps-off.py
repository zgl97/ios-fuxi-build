#!/usr/bin/env python3
"""Disable WCN7850 idle power save for the L1 bring-up lane."""
import sys
from pathlib import Path

path = Path(sys.argv[1]) / "deps/linux/drivers/net/wireless/ath/ath12k/wifi7/hw.c"
text = path.read_text(encoding="utf-8")
needle = '\t\t.name = "wcn7850 hw2.0",'
start = text.find(needle)
if start < 0:
    raise SystemExit("idle-ps-off: wcn7850 hw2.0 entry not found")
end = text.find('\n\t},', start)
if end < 0:
    raise SystemExit("idle-ps-off: end of wcn7850 entry not found")
block = text[start:end]
if '\t\t.idle_ps = true,' not in block:
    raise SystemExit("idle-ps-off: idle_ps = true not found in wcn7850 entry")
block = block.replace('\t\t.idle_ps = true,', '\t\t.idle_ps = false,', 1)
path.write_text(text[:start] + block + text[end:], encoding="utf-8")
print("idle-ps-off: wcn7850 idle_ps = false")
