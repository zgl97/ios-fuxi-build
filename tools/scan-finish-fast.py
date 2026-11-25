#!/usr/bin/env python3
"""r567: finish the first scan quickly and keep its vdev for the connection.

Two facts from the r566 boot log:

  * the firmware acknowledges WMI_SCAN_STARTED but never sends
    WMI_SCAN_EVENT_COMPLETED (only FOREIGN_CHAN reports arrive), so the
    driver waits the full 20 s scan timeout and then fails to abort the
    scan (another 3 s) and to delete the scan vdev (another 3 s);
  * the scan uses the primary interface's link-0 arvif, i.e. the same
    vdev the later station connection needs.  Deleting it on scan
    cleanup leaves only a stale vdev behind.

The first scan therefore only has to collect beacons; there is no point in
waiting 20 s for a completion event that this firmware never sends.  This
patch caps max_scan_time at 1.5 s, makes ath12k_scan_stop() skip the WMI
cancel command and its 3 s wait, and keeps the scan vdev instead of
deleting it during scan cleanup.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
ath = root / "deps/linux/drivers/net/wireless/ath/ath12k"


def replace(path, old, new, label):
    text = path.read_text()
    if old not in text:
        raise SystemExit("%s: pattern not found" % label)
    if text.count(old) != 1:
        raise SystemExit("%s: pattern not unique (%d)" % (label, text.count(old)))
    path.write_text(text.replace(old, new, 1))
    print("patched %s: %s" % (path.name, label))


wmi = ath / "wmi.c"
replace(wmi,
        "\targ->max_scan_time = 20000;",
        "\targ->max_scan_time = 1500;",
        "max_scan_time 20000 -> 1500")

mac = ath / "mac.c"
replace(mac,
        "\t/* TODO: Fill other STOP Params */\n"
        "\targ.pdev_id = ar->pdev->pdev_id;\n"
        "\n"
        "\tret = ath12k_wmi_send_scan_stop_cmd(ar, &arg);\n"
        "\tif (ret) {\n"
        "\t\tath12k_warn(ar->ab, \"failed to stop wmi scan: %d\\n\", ret);\n"
        "\t\tgoto out;\n"
        "\t}\n"
        "\n"
        "\tret = wait_for_completion_timeout(&ar->scan.completed, 3 * HZ);\n"
        "\tif (ret == 0) {\n"
        "\t\tath12k_warn(ar->ab,\n"
        "\t\t\t    \"failed to receive scan abort comple: timed out\\n\");\n"
        "\t\tret = -ETIMEDOUT;\n"
        "\t} else if (ret > 0) {\n"
        "\t\tret = 0;\n"
        "\t}\n",
        "\t/* TODO: Fill other STOP Params */\n"
        "\targ.pdev_id = ar->pdev->pdev_id;\n"
        "\n"
        "\t/* r567: this firmware never completes the first scan; do not\n"
        "\t * wait for a WMI cancel response that never arrives. */\n"
        "\tath12k_info(ar->ab, \"NWDIAG scan_stop skip wmi pdev %u\\n\",\n"
        "\t\t   arg.pdev_id);\n"
        "\tret = -ETIMEDOUT;\n",
        "scan_stop skips WMI cancel")

replace(mac,
        "\tath12k_mac_remove_link_interface(ah->hw, arvif);\n"
        "\tath12k_mac_unassign_link_vif(arvif);\n",
        "\tath12k_info(ar->ab, \"NWDIAG scan vdev kept link %u\\n\",\n"
        "\t\t   arvif->link_id);\n",
        "scan cleanup keeps the vdev")
