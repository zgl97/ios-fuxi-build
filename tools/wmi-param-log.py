#!/usr/bin/env python3
"""r632: log the WMI vdev parameters and the resource config we hand the firmware.

Rationale: every host-visible RX precondition has been verified correct
(r611-r631).  What has never been inspected is *what we actually tell the
firmware*: the per-vdev parameter list (vdev params include RX decap and
filter settings) and the resource config TLV inside WMI_INIT_CMDID.  If the
port sends a parameter the firmware dislikes, it can keep the RXDMA engine
parked while everything on our side still reads back clean.

MARK: NWDIAG wmi rcfg
"""

import sys
from pathlib import Path

MARK = "NWDIAG wmi rcfg"

SIG = (
    "int ath12k_wmi_vdev_set_param_cmd(struct ath12k *ar, u32 vdev_id,\n"
    "\t\t\t\t  u32 param_id, u32 param_value)\n"
    "{\n"
    "\tstruct ath12k_wmi_pdev *wmi = ar->wmi;\n"
    "\tstruct wmi_vdev_set_param_cmd *cmd;\n"
    "\tstruct sk_buff *skb;\n"
    "\tint ret;\n"
)

PARAM_BLOCK = (
    "\tath12k_info(ar->ab, \"NWDIAG wmi vdev param vdev %u id 0x%x val %u\\n\",\n"
    "\t\t    vdev_id, param_id, param_value);\n"
)

RCFG_ANCHOR = (
    "\tu32 hw_mode_len = 0;\n"
    "\tu16 idx;\n"
)

RCFG_BLOCK = (
    "\tath12k_info(ab, \"NWDIAG wmi rcfg vdev %u peer %u tid %u keys %u msdudesc %u decap %u\\n\",\n"
    "\t\t    arg->res_cfg.num_vdevs, arg->res_cfg.num_peers,\n"
    "\t\t    arg->res_cfg.num_tids, arg->res_cfg.num_peer_keys,\n"
    "\t\t    arg->res_cfg.num_msdu_desc, arg->res_cfg.rx_decap_mode);\n"
    "\tath12k_info(ab, \"NWDIAG wmi rcfg rxchain 0x%x batch %u pri %u %u %u %u iphdr %u\\n\",\n"
    "\t\t    arg->res_cfg.rx_chain_mask, arg->res_cfg.rx_batchmode,\n"
    "\t\t    arg->res_cfg.rx_timeout_pri[0], arg->res_cfg.rx_timeout_pri[1],\n"
    "\t\t    arg->res_cfg.rx_timeout_pri[2], arg->res_cfg.rx_timeout_pri[3],\n"
    "\t\t    arg->res_cfg.iphdr_pad_config);\n"
)


def ath12k_dir(dest: Path) -> Path:
    for cand in (dest, dest / "ath12k", dest / "drivers/net/wireless/ath/ath12k",
                 dest / "deps/linux/drivers/net/wireless/ath/ath12k"):
        if (cand / "wmi.c").is_file():
            return cand
    raise SystemExit("wmi-param-log: no ath12k/wmi.c under %s" % dest)


def patch(path: Path) -> None:
    data = path.read_bytes()
    text = data.decode()

    if MARK in text:
        print("ok wmi-param-log: already patched (marker present)")
        return

    if text.count(SIG) != 1:
        raise SystemExit("wmi-param-log: vdev_set_param signature anchor count=%d"
                         % text.count(SIG))
    text = text.replace(SIG, SIG + PARAM_BLOCK)

    if text.count(RCFG_ANCHOR) != 1:
        raise SystemExit("wmi-param-log: init_cmd anchor count=%d"
                         % text.count(RCFG_ANCHOR))
    text = text.replace(RCFG_ANCHOR, RCFG_ANCHOR + RCFG_BLOCK)

    path.write_bytes(text.encode())
    print("ok wmi-param-log: patched ath12k/wmi.c (vdev params + resource config)")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: wmi-param-log.py <linux tree | ath12k dir>")
    patch(ath12k_dir(Path(sys.argv[1])) / "wmi.c")


if __name__ == "__main__":
    main()
