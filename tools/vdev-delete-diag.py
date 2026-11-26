#!/usr/bin/env python3
"""r575: stamp every vdev teardown with vdev id, link and jiffies.

r574 lost its scan results because the interface was removed before the scan
completed, and the console only showed mac80211's "Remove interface en0 from
radio" - no ath12k-side time marker.  Both teardown sites are stamped here:

  * ath12k_mac_vdev_delete()          - the WMI vdev delete
  * ath12k_mac_op_remove_interface()  - the interface removal that also forces
                                        a scan completion (ATH12K_SCAN_IDLE)
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"
s = path.read_text(encoding="utf-8")

old = """	lockdep_assert_wiphy(ath12k_ar_to_hw(ar)->wiphy);

	reinit_completion(&ar->vdev_delete_done);
"""
new = """	lockdep_assert_wiphy(ath12k_ar_to_hw(ar)->wiphy);

	/* r575: name the teardown so the console shows when vdev 0 disappears. */
	ath12k_info(ab, "NWDIAG vdev delete vdev %u link %u created %u started %u scan_state %d jiffies %lu\\n",
		    arvif->vdev_id, arvif->link_id, arvif->is_created,
		    arvif->is_started, ar->scan.state, jiffies);

	reinit_completion(&ar->vdev_delete_done);
"""
if old not in s:
    raise SystemExit("ath12k_mac_vdev_delete anchor not found")
if s.count(old) != 1:
    raise SystemExit("ath12k_mac_vdev_delete anchor not unique (%d)" % s.count(old))
s = s.replace(old, new, 1)

old = """		ar = arvif->ar;

		/* Scan abortion is in progress since before this, cancel_hw_scan()
"""
new = """		ar = arvif->ar;

		ath12k_info(ar->ab, "NWDIAG remove interface link %u vdev %u type %u started %u scan_arvif %u scan_state %d jiffies %lu\\n",
			    link_id, arvif->vdev_id, vif->type, arvif->is_started,
			    ar->scan.arvif ? ar->scan.arvif->vdev_id : 99, ar->scan.state,
			    jiffies);

		/* Scan abortion is in progress since before this, cancel_hw_scan()
"""
if old not in s:
    raise SystemExit("ath12k_mac_op_remove_interface anchor not found")
if s.count(old) != 1:
    raise SystemExit("ath12k_mac_op_remove_interface anchor not unique (%d)" % s.count(old))
s = s.replace(old, new, 1)

path.write_text(s, encoding="utf-8")
print("vdev-delete-diag: patched ath12k/mac.c")
