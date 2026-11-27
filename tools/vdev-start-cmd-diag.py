#!/usr/bin/env python3
"""r584/r586: print what the VDEV_START request actually carries.

r585 finally reached the association path with the correct mac-layer arguments
(2417 MHz, centre 2417, width 20, 2 GHz, vdev type 2), yet the firmware never
answered WMI_VDEV_START and then stopped answering WMI at all.  The first
version of this probe printed the command fields *before*
ath12k_wmi_put_wmi_channel() filled them, so everything read back as zero.  The
probe now sits after the fill, which is the only place the fields mean
anything.
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

BAD = WMI_PROBE + WMI_ANCHOR


def patch_after(path: Path, anchor: str, probe: str, marker: str, label: str) -> None:
    """Insert probe *after* anchor; fix the r584 placement if it is present."""
    src = path.read_text()
    if BAD in src:
        src = src.replace(BAD, anchor + probe)
        path.write_text(src)
        print(f"vdev-start-cmd-diag: {label} moved after the fill")
        return
    if marker in src:
        print(f"vdev-start-cmd-diag: {label} already instrumented")
        return
    if src.count(anchor) != 1:
        raise SystemExit(
            f"vdev-start-cmd-diag: {label} anchor count {src.count(anchor)} (expected 1)"
        )
    path.write_text(src.replace(anchor, anchor + probe))
    print(f"vdev-start-cmd-diag: patched {label}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: vdev-start-cmd-diag.py <ath12kwifi-root>")
    root = Path(sys.argv[1])
    ath = root / "deps/linux/drivers/net/wireless/ath/ath12k"
    patch_after(ath / "wmi.c", WMI_ANCHOR, WMI_PROBE, "NWDIAG vdev_start cmd vdev", "ath12k/wmi.c")
    patch_after(ath / "mac.c", MAC_ANCHOR, MAC_PROBE, "NWDIAG vdev_start arg vdev", "ath12k/mac.c")


if __name__ == "__main__":
    main()
