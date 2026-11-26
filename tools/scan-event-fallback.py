#!/usr/bin/env python3
"""r575: keep scan events when the interface that started the scan is gone.

r574 console (build/monitor-r574/boot.log):

  L5640 Remove interface en0 from radio        <- wpa's INFO RPC timed out and
                                                  the interface was torn down
  L5704 scan start cmd len 332 ... vdev 0      <- board scan on the dead vdev
  L5713 NWDIAG scan abort cancel_hw_scan
  L5723 NWDIAG scan ev unknown vdev 0 type 2   <- WMI_SCAN_EVENT_COMPLETED
  L5724 Received scan event for unknown vdev   <- dropped, BSS list stays empty

ath12k_scan_event() resolves the radio through ath12k_mac_get_ar_by_vdev_id(),
which iterates *active* interfaces, so a torn-down interface means no ar and the
completion (plus the BSS entries behind it) is thrown away even though the radio
and its scan state are still alive.  This target has exactly one radio, so fall
back to the first active pdev and keep parsing the event.

Run after scan-event-log.py (that script inserts the NWDIAG lines used as the
anchor here).
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
s = path.read_text(encoding="utf-8")

old = """	if (!ar) {
		ath12k_info(ab, "NWDIAG scan ev unknown vdev %u type %u\\n",
			    le32_to_cpu(scan_ev.vdev_id),
			    le32_to_cpu(scan_ev.event_type));
		ath12k_warn(ab, "Received scan event for unknown vdev");
		rcu_read_unlock();
		return;
	}
"""

new = """	if (!ar) {
		struct ath12k_pdev *fb_pdev;
		int fb_i;

		ath12k_info(ab, "NWDIAG scan ev fallback vdev %u type %u reason %u\\n",
			    le32_to_cpu(scan_ev.vdev_id),
			    le32_to_cpu(scan_ev.event_type),
			    le32_to_cpu(scan_ev.reason));
		/* r575: the vif may already be gone (wpa restart tore it down) while
		 * the radio keeps scanning.  This target has one radio, so attribute
		 * the event to the first active pdev instead of dropping it. */
		for (fb_i = 0; fb_i < ab->num_radios; fb_i++) {
			fb_pdev = rcu_dereference(ab->pdevs_active[fb_i]);
			if (fb_pdev && fb_pdev->ar) {
				ar = fb_pdev->ar;
				break;
			}
		}
	}

	if (!ar) {
		ath12k_info(ab, "NWDIAG scan ev unknown vdev %u type %u\\n",
			    le32_to_cpu(scan_ev.vdev_id),
			    le32_to_cpu(scan_ev.event_type));
		ath12k_warn(ab, "Received scan event for unknown vdev");
		rcu_read_unlock();
		return;
	}
"""

if old not in s:
    raise SystemExit("scan-event fallback anchor not found (run scan-event-log.py first)")
if s.count(old) != 1:
    raise SystemExit("scan-event fallback anchor not unique (%d)" % s.count(old))
s = s.replace(old, new, 1)
path.write_text(s, encoding="utf-8")
print("scan-event-fallback: patched ath12k/wmi.c")
