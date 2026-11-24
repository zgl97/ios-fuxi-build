#!/usr/bin/env python3
"""Temporary experiment: start the scan vdev before asking firmware to scan.

The first scan in the Linux path is issued right after the scan vdev is
created.  Standard mac80211 reuses an existing interface vdev, but this
stack has no add_interface() call, so the scan vdev is fresh and is never
started.  Start it with the monitor helper (which does not need a link
conf) and log whether firmware acknowledged WMI_VDEV_START.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
mac = root / "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"
reg = root / "deps/linux/drivers/net/wireless/ath/ath12k/reg.c"
s = mac.read_text(encoding="utf-8")

old = """\t\tret = ath12k_mac_vdev_create(ar, arvif);
\t\tif (ret) {
\t\t\tath12k_warn(ar->ab, "unable to create scan vdev %d\\n", ret);
\t\t\tath12k_mac_unassign_link_vif(arvif);
\t\t\treturn ret;
\t\t}
\t}
"""
new = old + """\
\tif (create && n_channels > 0) {
\t\tstruct cfg80211_chan_def scan_chandef = {};
\t\tint start_ret;

\t\tscan_chandef.chan = chan_list[0];
\t\tscan_chandef.width = NL80211_CHAN_WIDTH_20;
\t\tscan_chandef.center_freq1 = chan_list[0]->center_freq;
\t\tu8 saved_mac[ETH_ALEN];
\t\tmemcpy(saved_mac, ar->mac_addr, ETH_ALEN);
\t\tmemcpy(ar->mac_addr, arvif->bssid, ETH_ALEN);
\t\tstart_ret = ath12k_mac_monitor_vdev_start(ar, arvif->vdev_id,
\t\t\t\t\t\t\t  &scan_chandef);
\t\tmemcpy(ar->mac_addr, saved_mac, ETH_ALEN);
\t\tath12k_info(ar->ab, "scan vdev start %d\\n", start_ret);
\t}
"""
if old not in s:
    raise SystemExit("scan vdev create block not found")
s = s.replace(old, new, 1)

# Log the vif address and pdev id in the existing diagnostic line.
old_log = """\tath12k_info(ab, "scan vdev create id %u type %u ret %d\\n",
\t\t    arvif->vdev_id, ahvif->vdev_type, ret);
"""
new_log = """\tath12k_info(ab, "scan vdev create id %u type %u ret %d bssid %02x:%02x:%02x:%02x:%02x:%02x pdev %u\\n",
\t\t    arvif->vdev_id, ahvif->vdev_type, ret,
\t\t    arvif->bssid[0], arvif->bssid[1], arvif->bssid[2],
\t\t    arvif->bssid[3], arvif->bssid[4], arvif->bssid[5],
\t\t    arvif->ar->pdev->pdev_id);
"""
if old_log not in s:
    raise SystemExit("scan vdev create diagnostic line not found")
s = s.replace(old_log, new_log, 1)

# Give the firmware a little longer to emit WMI_SCAN_EVENT_STARTED.
old_wait = """\tret = wait_for_completion_timeout(&ar->scan.started, 1 * HZ);
"""
new_wait = """\tret = wait_for_completion_timeout(&ar->scan.started, 3 * HZ);
"""
if old_wait not in s:
    raise SystemExit("scan started wait not found")
s = s.replace(old_wait, new_wait, 1)

# Log whether the firmware advertised the scan offload service.
old_scan = """\tret = ath12k_start_scan(ar, arg);
"""
new_scan = """\tath12k_info(ar->ab, "scan svc offload %d\\n",
\t\t    test_bit(WMI_TLV_SERVICE_SCAN_OFFLOAD, ar->ab->wmi_ab.svc_map));
\tret = ath12k_start_scan(ar, arg);
"""
if old_scan not in s:
    raise SystemExit("ath12k_start_scan call not found")
s = s.replace(old_scan, new_scan, 1)

mac.write_text(s, encoding="utf-8")
print("scan-vdev-start: patched mac.c")

# Do not let a late regulatory update rewrite the scan channel list while
# the firmware is starting the first scan; the second list command cancels it.
reg_s = reg.read_text(encoding="utf-8")
old_reg = """\tif (ar->ah->state == ATH12K_HW_STATE_RESTARTING)
\t\treturn 0;
"""
new_reg = """\tif (ar->ah->state == ATH12K_HW_STATE_RESTARTING)
\t\treturn 0;

\tif (ar->scan.state != ATH12K_SCAN_IDLE) {
\t\tath12k_dbg(ar->ab, ATH12K_DBG_WMI,
\t\t\t   "skip scan chan list in state %s\\n",
\t\t\t   ath12k_scan_state_str(ar->scan.state));
\t\treturn 0;
\t}
"""
if old_reg not in reg_s:
    raise SystemExit("reg update guard not found")
reg_s = reg_s.replace(old_reg, new_reg, 1)
reg.write_text(reg_s, encoding="utf-8")
print("scan-vdev-start: patched reg.c")
