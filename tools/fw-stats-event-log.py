#!/usr/bin/env python3
"""r634: print the firmware PDEV counters where they actually exist.

r633 hung the stats request off ath12k_mac_station_remove(); mac80211 never
takes the station down between authentication retries, so the probe never ran
(zero lines in monitor-r633).  This version does two independent things:

  A.  bss_info_changed() runs on every connect attempt and is sleepable, so
      the request is issued from there (first six calls only, with a marker so
      we can tell "request went out" from "request never sent").
  B.  The stats event handler parses the counters into stats.pdevs
      before splicing them into ar->fw_stats.  Print them right there (anchor is
      upstream-native text, not one of our own probes), at the point where they
      are guaranteed to be present - no waiting, no context assumption.

MARK: NWDIAG fw rx
"""

import sys
from pathlib import Path

MARK_MAC = "NWDIAG fw stats req"
MARK_WMI = "NWDIAG fw rx"

MAC_ANCHOR = (
    "\tstruct ieee80211_bss_conf *info,\n"
    "\t\t\t\t\tu64 changed)\n"
    "{\n"
    "\tstruct ath12k_vif *ahvif = arvif->ahvif;\n"
    "\tstruct ieee80211_vif *vif = ath12k_ahvif_to_vif(ahvif);\n"
    "\tstruct ieee80211_vif_cfg *vif_cfg = &vif->cfg;\n"
    "\tstruct ath12k_link_vif *tx_arvif;\n"
    "\tstruct cfg80211_chan_def def;\n"
    "\tu32 param_id, param_value;\n"
    "\tenum nl80211_band band;\n"
    "\tu32 vdev_param;\n"
    "\tint mcast_rate;\n"
    "\tu32 preamble;\n"
    "\tu16 hw_value;\n"
    "\tu16 bitrate;\n"
    "\tu8 rateidx;\n"
    "\tu32 rate;\n"
    "\tint ret;\n"
)

MAC_BLOCK = (
    "\n"
    "\t{\n"
    "\t\tstatic unsigned int dbg_req_n;\n"
    "\n"
    "\t\tif (dbg_req_n < 6) {\n"
    "\t\t\tint dbg_ret;\n"
    "\n"
    "\t\t\tdbg_ret = ath12k_wmi_send_stats_request_cmd(ar,\n"
    "\t\t\t\t\t\tWMI_REQUEST_PDEV_STAT, 0, 0);\n"
    "\t\t\tath12k_info(ar->ab, \"NWDIAG fw stats req n %u chg 0x%llx ret %d\\n\",\n"
    "\t\t\t\t    dbg_req_n, changed, dbg_ret);\n"
    "\t\t\tdbg_req_n++;\n"
    "\t\t}\n"
    "\t}\n"
)

WMI_ANCHOR = '\tath12k_dbg(ab, ATH12K_DBG_WMI, "event update stats");\n'

WMI_BLOCK = (
    '\t{\n'
    '\t\tstruct ath12k_fw_stats_pdev *dbg_fp;\n'
    '\n'
    '\t\tlist_for_each_entry(dbg_fp, &stats.pdevs, list) {\n'
    '\t\t\tath12k_info(ab, "NWDIAG fw rx %u tx %u fcs %u nobcn %u phy %u\\n",\n'
    '\t\t\t\t    dbg_fp->rx_frame_count, dbg_fp->tx_frame_count,\n'
    '\t\t\t\t    dbg_fp->fcs_bad, dbg_fp->no_beacons,\n'
    '\t\t\t\t    dbg_fp->phy_err_count);\n'
    '\t\t\tath12k_info(ab, "NWDIAG fw txko %u reque %u drop %u hwq %u\\n",\n'
    '\t\t\t\t    dbg_fp->tx_ko, dbg_fp->mpdus_requed,\n'
    '\t\t\t\t    dbg_fp->wmm_drop, dbg_fp->hw_queued);\n'
    '\t\t}\n'
    '\t}\n'
)


def ath12k_dir(dest: Path) -> Path:
    for cand in (dest, dest / "ath12k",
                 dest / "drivers/net/wireless/ath/ath12k",
                 dest / "deps/linux/drivers/net/wireless/ath/ath12k"):
        if (cand / "mac.c").is_file():
            return cand
    raise SystemExit("fw-stats-event-log: no ath12k/mac.c under %s" % dest)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: fw-stats-event-log.py <linux tree | ath12k dir>")
    root = ath12k_dir(Path(sys.argv[1]))
    mac = root / "mac.c"
    wmi = root / "wmi.c"
    mac_text = mac.read_bytes().decode()
    wmi_text = wmi.read_bytes().decode()

    if MARK_MAC in mac_text and MARK_WMI in wmi_text:
        print("ok fw-stats-event-log: already patched (marker present)")
        return

    n_mac = mac_text.count(MAC_ANCHOR)
    n_wmi = wmi_text.count(WMI_ANCHOR)
    if n_mac != 1 or n_wmi != 1:
        raise SystemExit("fw-stats-event-log: anchors mac=%d wmi=%d, refusing"
                         % (n_mac, n_wmi))

    mac.write_bytes(mac_text.replace(MAC_ANCHOR, MAC_ANCHOR + MAC_BLOCK).encode())
    wmi.write_bytes(wmi_text.replace(WMI_ANCHOR, WMI_ANCHOR + WMI_BLOCK).encode())
    print("ok fw-stats-event-log: patched ath12k/mac.c + ath12k/wmi.c")


if __name__ == "__main__":
    main()
