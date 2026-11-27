#!/usr/bin/env python3
"""r584: print what the VDEV_START request actually carries.

r583 finally scanned all thirteen channels and fed seven real BSS to the
supplicant, and the supplicant then asked the driver to authenticate.  The
firmware never answered WMI_VDEV_START, so ath12k_mac_vdev_setup_sync() timed
out after 15 s (ret -110) and the connect attempt died.

The remaining unknown is the request itself: if the channel context is empty
the driver still builds a command, but with mhz = 0 and an invalid phymode the
firmware silently drops it.  These two probes print the arguments the mac
layer picked and the fields the WMI command ended up with, so the next run
tells a bad channel apart from a bad flag set.
"""
import sys
from pathlib import Path

WMI_ANCHOR = "\tath12k_wmi_put_wmi_channel(chan, arg);\n"
WMI_PROBE = (
    "\tath12k_info(ar->ab,\n"
    "\t\t    \"NWDIAG vdev_start cmd vdev %u restart %d mhz %u cf1 %u cf2 %u info 0x%x flags 0x%x punct 0x%x\\n\",\n"
    "\t\t    arg->vdev_id, restart,\n"
    "\t\t    le32_to_cpu(chan->mhz),\n"
    "\t\t    le32_to_cpu(chan->band_center_freq1),\n"
    "\t\t    le32_to_cpu(chan->band_center_freq2),\n"
    "\t\t    le32_to_cpu(chan->info),\n"
    "\t\t    le32_to_cpu(cmd->flags),\n"
    "\t\t    le32_to_cpu(cmd->punct_bitmap));\n"
)

MAC_ANCHOR = "\tret = ath12k_wmi_vdev_start(ar, &arg, restart);\n"
MAC_PROBE = (
    "\tath12k_info(ab,\n"
    "\t\t    \"NWDIAG vdev_start arg vdev %u freq %u cf1 %u cf2 %u mode %u width %d band %d type %u started %u\\n\",\n"
    "\t\t    arg.vdev_id, arg.freq, arg.band_center_freq1, arg.band_center_freq2,\n"
    "\t\t    arg.mode, (int)chandef->width, (int)chandef->chan->band,\n"
    "\t\t    (unsigned int)ahvif->vdev_type, arvif->is_started);\n"
)


def patch(path: Path, anchor: str, probe: str, marker: str, label: str) -> None:
    src = path.read_text()
    if marker in src:
        print(f"vdev-start-cmd-diag: {label} already instrumented")
        return
    if src.count(anchor) != 1:
        raise SystemExit(
            f"vdev-start-cmd-diag: {label} anchor count {src.count(anchor)} (expected 1)"
        )
    path.write_text(src.replace(anchor, probe + anchor))
    print(f"vdev-start-cmd-diag: patched {label}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: vdev-start-cmd-diag.py <ath12kwifi-root>")
    root = Path(sys.argv[1])
    ath = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    patch(ath / "wmi.c", WMI_ANCHOR, WMI_PROBE, "NWDIAG vdev_start cmd vdev", "ath12k/wmi.c")
    patch(ath / "mac.c", MAC_ANCHOR, MAC_PROBE, "NWDIAG vdev_start arg vdev", "ath12k/mac.c")


if __name__ == "__main__":
    main()
