#!/usr/bin/env python3
"""Keep firmware scan events alive when the vif that started the scan is gone (r575).

r574 console: wpa's first NW_CONTROL_INFO timed out, the interface was removed
("Remove interface en0 from radio"), yet the board-path scan kept running on
vdev 0.  The firmware's WMI_SCAN_EVENT_COMPLETED then arrived with that vdev id,
ath12k_mac_get_ar_by_vdev_id() walked the active interface list, found nothing,
and ath12k_scan_event() dropped the event: the BSS cache stayed empty and wpa
reported "No suitable network found" until the end of the boot.

This target has a single radio, so attribute an event whose vdev no longer has
an interface to the first active pdev instead of dropping it.  The existing
"unknown vdev" branch stays as the last resort.

Anchor-independent on purpose: scan-event-log.py (r565) rewrites the
"if (!ar)" body, and its wording changed between revisions.  We therefore
insert right before the first "if (!ar) {" that follows the vdev lookup
instead of matching the whole block.  Run after scan-event-log.py.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
s = path.read_text(encoding="utf-8")

if "NWDIAG scan ev fallback" in s:
    print("scan-event-fallback: already patched")
    raise SystemExit(0)

marker = "static void ath12k_scan_event(struct ath12k_base *ab, struct sk_buff *skb)\n{"
pos = s.find(marker)
if pos < 0:
    raise SystemExit("ath12k_scan_event not found")

lookup = "ath12k_mac_get_ar_by_vdev_id(ab, le32_to_cpu(scan_ev.vdev_id))"
idx = s.find(lookup, pos)
if idx < 0:
    raise SystemExit("vdev lookup not found inside ath12k_scan_event")

anchor = "\n\tif (!ar) {\n"
cut = s.find(anchor, idx)
if cut < 0:
    raise SystemExit("unknown-vdev branch not found after the vdev lookup")

insert = """
\tif (!ar) {
\t\tstruct ath12k_pdev *fb_pdev;
\t\tint fb_i;

\t\tath12k_info(ab, "NWDIAG scan ev fallback vdev %u type %u reason %u jiffies %lu\\n",
\t\t\t    le32_to_cpu(scan_ev.vdev_id),
\t\t\t    le32_to_cpu(scan_ev.event_type),
\t\t\t    le32_to_cpu(scan_ev.reason), jiffies);
\t\t/* r575: the vif may already be gone (wpa restart tore it down) while
\t\t * the radio keeps scanning.  This target has one radio, so attribute
\t\t * the event to the first active pdev instead of dropping it. */
\t\tfor (fb_i = 0; fb_i < ab->num_radios; fb_i++) {
\t\t\tfb_pdev = rcu_dereference(ab->pdevs_active[fb_i]);
\t\t\tif (fb_pdev && fb_pdev->ar) {
\t\t\t\tar = fb_pdev->ar;
\t\t\t\tbreak;
\t\t\t}
\t\t}
\t}
"""

s = s[:cut] + insert + s[cut:]
path.write_text(s, encoding="utf-8")
print("scan-event-fallback: patched ath12k/wmi.c")
