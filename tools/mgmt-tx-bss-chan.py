#!/usr/bin/env python3
"""Give the mgmt-tx command the channel mac80211 actually bound (r606).

r605 tried to read the channel from the driver's own `ahvif->chanctx` copy and
found it empty at authentication time:

    NWDIAG vdev_start skip vdev 0 fw_started freq 2417
    NWDIAG mgmt chan vdev 0 ctx 0
    NWDIAG wmi mgmt send vdev 0 buf 0 flen 30 blen 30 freq 0
    wlan0: authentication with ... timed out

`struct ath12k_vif::chanctx` is a stale FIXME in this tree - nothing keeps it
in sync - so `chanfreq` stayed 0 and the firmware had to infer the channel from
the vdev alone.  mac80211 does keep the binding: `vif->bss_conf.chanreq.oper`
is the operating chandef of the link being authenticated.  Read the channel
from there, and fall back to the upstream off-channel helper only when
mac80211 has no chandef at all.

Run after `mgmt-tx-freq.py`: that tool owns the `arvif->ahvif->chanctx` branch,
this one replaces the whole `cmd->chanfreq` assignment that r605 left in place.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"

text = wmi.read_text()

MARK = "NWDIAG mgmt chan vdev %u bss"
if MARK in text:
    print("mgmt-tx-bss-chan: already patched")
    sys.exit(0)

OLD = """	if (arvif->ahvif->chanctx.def.chan) {
		u32 ctx_freq = arvif->ahvif->chanctx.def.chan->center_freq;

		cmd->chanfreq = cpu_to_le32(ctx_freq);
		ath12k_info(ab, "NWDIAG mgmt chan vdev %u ctx %u\\n",
			    vdev_id, ctx_freq);
	} else {
		cmd->chanfreq = cpu_to_le32(ath12k_wmi_mgmt_get_freq(ar, info));
		ath12k_info(ab, "NWDIAG mgmt chan vdev %u ctx 0\\n", vdev_id);
	}
"""

NEW = """	/* r606: the driver-side chanctx copy is never maintained, so ask
	 * mac80211 for the chandef the vif is really bound to.
	 */
	if (vif->bss_conf.chanreq.oper.chan) {
		u32 bss_freq = vif->bss_conf.chanreq.oper.chan->center_freq;

		cmd->chanfreq = cpu_to_le32(bss_freq);
		ath12k_info(ab, "NWDIAG mgmt chan vdev %u bss %u pdev %u idx %u\\n",
			    vdev_id, bss_freq, ar->pdev->pdev_id, ar->pdev_idx);
	} else {
		cmd->chanfreq = cpu_to_le32(ath12k_wmi_mgmt_get_freq(ar, info));
		ath12k_info(ab, "NWDIAG mgmt chan vdev %u bss 0 pdev %u idx %u\\n",
			    vdev_id, ar->pdev->pdev_id, ar->pdev_idx);
	}
"""

if text.count(OLD) != 1:
    raise SystemExit(
        "mgmt-tx-bss-chan: expected one r605 chanctx branch, found %d"
        % text.count(OLD)
    )

wmi.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print("mgmt-tx-bss-chan: command channel now follows mac80211's chandef")
