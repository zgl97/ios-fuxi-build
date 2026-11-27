#!/usr/bin/env python3
"""Open the result gate when the scan ends, not three seconds after it starts (r589).

scan-result-gate.py keeps `get_bss` returning NW_CONTROL_NOT_FOUND for three
seconds after a scan starts, so wpa_supplicant cannot pick a BSS while the
firmware still owns the vdev.  That was written when the only scan was the
board's own 1.5 s scan followed by a 5 s retry.

r588 makes every scan a wpa-driven one capped at 1500 ms: the firmware reports
COMPLETED after 1.5 s and wpa asks for results at once, still inside the 3 s
gate, so the shim dumps zero entries (`sheng: scan results 0`) even though the
driver already counted ten BSS from the probe responses.

Clear the timestamp when the scan-done event is built, and keep the gate only
as a 100 ms settle window right after the request, which is what the gate was
really for.  Run after scan-result-gate.py.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
ctl = root / "src/nw_cfg80211_control.c"

text = ctl.read_text()


def replace(old, new, label):
    global text
    if old not in text:
        raise SystemExit(f"scan-gate-on-done: anchor not found: {label}")
    if text.count(old) != 1:
        raise SystemExit(f"scan-gate-on-done: anchor not unique ({text.count(old)}): {label}")
    text = text.replace(old, new, 1)


if "nw_scan_started_jiffies = 0;\n  struct sk_buff *message = alloc_skb" in text:
    print("scan-gate-on-done: already patched")
    sys.exit(0)

replace(
    "  if (!primary || wdev != primary->ieee80211_ptr) return NULL;\n"
    "  struct sk_buff *message = alloc_skb(NW_CONTROL_HEADER_SIZE, GFP_KERNEL);",
    "  if (!primary || wdev != primary->ieee80211_ptr) return NULL;\n"
    "  /* r589: the scan is over, so the BSS list is complete.  Holding the\n"
    "   * gate until three seconds after the start hid the whole result list\n"
    "   * whenever the firmware finished inside that window. */\n"
    "  nw_scan_started_jiffies = 0;\n"
    "  struct sk_buff *message = alloc_skb(NW_CONTROL_HEADER_SIZE, GFP_KERNEL);",
    "clear gate on scan done",
)

replace(
    "      time_before(jiffies, nw_scan_started_jiffies + msecs_to_jiffies(3000)))",
    "      time_before(jiffies, nw_scan_started_jiffies + msecs_to_jiffies(100)))",
    "settle window 3000 -> 100 ms",
)

ctl.write_text(text, encoding="utf-8")
print("scan-gate-on-done: gate opens with the scan-done event, settle window 100 ms")
