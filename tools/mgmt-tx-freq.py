#!/usr/bin/env python3
"""Tell the firmware which channel an on-channel mgmt frame belongs to (r605).

Upstream leaves `cmd->chanfreq` at 0 for on-channel frames: the firmware is
supposed to send the frame on whatever channel the vdev already runs on.  That
assumption breaks for us, because the station vdev is created and started by
`vdev-start-on-add.py` *before* mac80211 knows the target AP, and
`vdev-start-skip.py` then keeps that vdev instead of restarting it on the
channel the supplicant picked.  The firmware therefore has a vdev on one
channel while authentication is aimed at another, and the auth frames go out on
the wrong frequency:

    NWDIAG vdev_start skip vdev 0 fw_started freq 2417
    NWDIAG wmi mgmt send vdev 0 buf 0 flen 30 blen 30 freq 0
    wlan0: authentication with ... timed out

Report the channel the vif is actually bound to and pass it in the command, so
the frame and the vdev cannot disagree.  When mac80211 has no chanctx yet the
upstream helper stays in charge (off-channel / roaming cases).

Run after `mgmt-tx-tlv-len.py`: that tool rewrites the command header, this one
only rewrites the channel field.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"

text = wmi.read_text()

MARK = "NWDIAG mgmt chan vdev"
if MARK in text:
    print("mgmt-tx-freq: already patched")
    sys.exit(0)

OLD = "\tcmd->chanfreq = cpu_to_le32(ath12k_wmi_mgmt_get_freq(ar, info));\n"

NEW = """	/* r605: prefer the channel the vif is bound to over the upstream
	 * off-channel-only helper, and say out loud which one we used.
	 */
	if (arvif->ahvif->chanctx.def.chan) {
		u32 ctx_freq = arvif->ahvif->chanctx.def.chan->center_freq;

		cmd->chanfreq = cpu_to_le32(ctx_freq);
		ath12k_info(ab, "NWDIAG mgmt chan vdev %u ctx %u\\n",
			    vdev_id, ctx_freq);
	} else {
		cmd->chanfreq = cpu_to_le32(ath12k_wmi_mgmt_get_freq(ar, info));
		ath12k_info(ab, "NWDIAG mgmt chan vdev %u ctx 0\\n", vdev_id);
	}
"""

if text.count(OLD) != 1:
    raise SystemExit(
        "mgmt-tx-freq: expected one chanfreq assignment, found %d"
        % text.count(OLD)
    )

wmi.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print("mgmt-tx-freq: command channel now follows the vif chanctx")
