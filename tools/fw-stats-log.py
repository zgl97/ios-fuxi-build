#!/usr/bin/env python3
"""r633: ask the firmware for its own PDEV counters right after the station is
removed (i.e. right after the authentication attempt failed).

Everything the host can see has been verified correct (r611-r632), including
the WMI resource config and the per-vdev parameters, which match upstream
field for field.  What we still do not know is whether the air ever carried
the AP's authentication response.  The firmware keeps its own MAC counters;
`WMI_REQUEST_PDEV_STAT` returns them.  If rx_frame_count stays at the beacon
baseline while our beacons are counted, the response never arrived; if it
jumps by one around the auth window, the firmware received it and simply never
handed it to us.

MARK: NWDIAG fw pdev
"""

import sys
from pathlib import Path

MARK = "NWDIAG fw pdev"

ANCHOR = (
    "static int ath12k_mac_station_remove(struct ath12k *ar,\n"
    "\t\t\t\t     struct ath12k_link_vif *arvif,\n"
    "\t\t\t\t     struct ath12k_link_sta *arsta)\n"
    "{\n"
    "\tstruct ieee80211_sta *sta = ath12k_ahsta_to_sta(arsta->ahsta);\n"
    "\tstruct ath12k_vif *ahvif = arvif->ahvif;\n"
    "\tint ret = 0;\n"
    "\tstruct ath12k_link_sta *temp_arsta;\n"
)

BLOCK = (
    "\n"
    "\t{\n"
    "\t\tstruct ath12k_fw_stats_pdev *dbg_fp;\n"
    "\t\tunsigned long dbg_left;\n"
    "\t\tint dbg_n = 0, dbg_ret;\n"
    "\n"
    "\t\treinit_completion(&ar->fw_stats_done);\n"
    "\t\tdbg_ret = ath12k_wmi_send_stats_request_cmd(ar,\n"
    "\t\t\t\t\t     WMI_REQUEST_PDEV_STAT, 0, 0);\n"
    "\t\tdbg_left = wait_for_completion_timeout(&ar->fw_stats_done,\n"
    "\t\t\t\t\t       WMI_SERVICE_READY_TIMEOUT_HZ);\n"
    "\t\tspin_lock_bh(&ar->data_lock);\n"
    "\t\tlist_for_each_entry(dbg_fp, &ar->fw_stats.pdevs, list) {\n"
    "\t\t\tath12k_info(ar->ab, \"NWDIAG fw pdev rx %u tx %u fcs %u nobcn %u\\n\",\n"
    "\t\t\t\t    dbg_fp->rx_frame_count, dbg_fp->tx_frame_count,\n"
    "\t\t\t\t    dbg_fp->fcs_bad, dbg_fp->no_beacons);\n"
    "\t\t\tath12k_info(ar->ab, \"NWDIAG fw pdev phy %u ackbad %u noise %d\\n\",\n"
    "\t\t\t\t    dbg_fp->phy_err_count, dbg_fp->ack_rx_bad,\n"
    "\t\t\t\t    dbg_fp->ch_noise_floor);\n"
    "\t\t\tdbg_n++;\n"
    "\t\t}\n"
    "\t\tspin_unlock_bh(&ar->data_lock);\n"
    "\t\tath12k_info(ar->ab, \"NWDIAG fw stats req ret %d left %lu n %d\\n\",\n"
    "\t\t\t    dbg_ret, dbg_left, dbg_n);\n"
    "\t\tath12k_fw_stats_reset(ar);\n"
    "\t}\n"
)


def ath12k_dir(dest: Path) -> Path:
    for cand in (dest, dest / "ath12k",
                 dest / "drivers/net/wireless/ath/ath12k",
                 dest / "deps/linux/drivers/net/wireless/ath/ath12k"):
        if (cand / "mac.c").is_file():
            return cand
    raise SystemExit("fw-stats-log: no ath12k/mac.c under %s" % dest)


def patch(path: Path) -> None:
    data = path.read_bytes()
    text = data.decode()

    if MARK in text:
        print("ok fw-stats-log: already patched (marker present)")
        return

    n = text.count(ANCHOR)
    if n != 1:
        raise SystemExit("fw-stats-log: station_remove anchor count=%d" % n)

    path.write_bytes(text.replace(ANCHOR, ANCHOR + BLOCK).encode())
    print("ok fw-stats-log: patched ath12k/mac.c (pdev stats after station remove)")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: fw-stats-log.py <linux tree | ath12k dir>")
    patch(ath12k_dir(Path(sys.argv[1])) / "mac.c")


if __name__ == "__main__":
    main()
