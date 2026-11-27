#!/usr/bin/env python3
"""r598: stop asking the firmware to start a vdev it is already running.

r597 (with the relaxed wpa profile) finally walks the whole path and the new
chanctx stamps say exactly where it dies:

    L9291  NWDIAG mac auth prep begin freq 2417
    L9293  NWDIAG change_chanctx begin changed 0x10      -> done
    L9295  NWDIAG chan assign begin
    L9297  NWDIAG vdev_start_restart vdev 0 restart 0 jiffies 221646
    L9298  NWDIAG vdev_start cmd ... mhz 2417 cf1 2417
    L9303  SME: Authentication request to the driver failed      (3 s client)
    L9381  NWDIAG vdev_start setup_sync vdev 0 ret -110 jiffies 236647
    L9384  NWDIAG chan assign done -110

Only the very first WMI_VDEV_START of the boot is ever answered
(`wmi op rx cmd 0x5001` at jiffies 73084, from r590's add_interface start).
Every later start for the same vdev id gets no reply at all, so the setup sync
burns its 15 s window and mac80211 gives up on the channel - which is why the
authentication frame is never built.

Root cause: ath12k_mac_op_unassign_vif_chanctx() deliberately leaves a STA vdev
running in firmware (only clears arvif->is_started), so the next
assign_vif_chanctx() sends a *start* for a vdev the firmware already started and
the firmware silently drops it.

Fix: remember whether the firmware side is running (`fw_started`, set when the
start sync succeeds, cleared when we actually stop the vdev) and, when it is
still running, skip the redundant WMI start and hand mac80211 a started vdev.
Also move r590's early start from channel 1 (2412 MHz) to channel 2
(2417 MHz), which is where both "409" BSSes actually live, so reusing the
already-started vdev does not leave the station on the wrong channel.
"""

import pathlib
import sys

CORE = "deps/linux/drivers/net/wireless/ath/ath12k/core.h"
MAC = "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"


def one(text, needle, what):
    n = text.count(needle)
    if n != 1:
        raise SystemExit(f"{what}: anchor count {n} (expected 1)")
    return needle


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: vdev-start-skip.py <tree>")
    root = pathlib.Path(sys.argv[1])
    core = root / CORE
    mac = root / MAC

    # ---- 1. a field that tracks the firmware side -------------------------
    text = core.read_text()
    if "bool fw_started;" not in text:
        anchor = one(text, "\tbool is_started;\n", "core.h is_started")
        text = text.replace(
            anchor, anchor + "\tbool fw_started; /* r598: vdev running in firmware */\n"
        )
        core.write_text(text)
        print("  patched: core.h fw_started")
    else:
        print("  already patched: core.h fw_started")

    text = mac.read_text()

    # ---- 2. set it where the start sync succeeds --------------------------
    if "arvif->fw_started = true;" not in text:
        anchor = one(
            text,
            "\tar->num_started_vdevs++;\n"
            '\tath12k_dbg(ab, ATH12K_DBG_MAC,  "vdev %pM started, vdev_id %d\\n",\n',
            "mac.c start success",
        )
        text = text.replace(
            anchor,
            "\tar->num_started_vdevs++;\n\tarvif->fw_started = true;\n"
            '\tath12k_dbg(ab, ATH12K_DBG_MAC,  "vdev %pM started, vdev_id %d\\n",\n',
        )
        print("  patched: mac.c set fw_started")
    else:
        print("  already patched: mac.c set fw_started")

    # ---- 3. clear it when the vdev is really stopped ----------------------
    if "arvif->fw_started = false;" not in text:
        anchor = one(
            text,
            "\tar->num_started_vdevs--;\n"
            '\tath12k_dbg(ar->ab, ATH12K_DBG_MAC, "vdev %pM stopped, vdev_id %d\\n",\n',
            "mac.c stop success",
        )
        text = text.replace(
            anchor,
            "\tar->num_started_vdevs--;\n\tarvif->fw_started = false;\n"
            '\tath12k_dbg(ar->ab, ATH12K_DBG_MAC, "vdev %pM stopped, vdev_id %d\\n",\n',
        )
        print("  patched: mac.c clear fw_started")
    else:
        print("  already patched: mac.c clear fw_started")

    # ---- 4. reuse a vdev the firmware already runs ------------------------
    SKIP = """	/* r598: the firmware answers one WMI_VDEV_START per boot and silently
	 * drops every later start for the same vdev id, so asking again only
	 * burns the 15 s setup sync and makes mac80211 drop the channel (and
	 * with it the authentication frame).  Reuse the running vdev instead.
	 */
	if (arvif->fw_started) {
		ath12k_info(ab, "NWDIAG vdev_start skip vdev %u fw_started freq %u\\n",
			    arvif->vdev_id, ctx->def.chan->center_freq);
		arvif->is_started = true;
		ret = 0;
		goto out;
	}

"""
    if "NWDIAG vdev_start skip" not in text:
        anchor = one(text, "\tret = ath12k_mac_vdev_start(arvif, ctx);\n",
                     "mac.c assign_vif_chanctx start")
        text = text.replace(anchor, SKIP + anchor)
        print("  patched: mac.c reuse started vdev")
    else:
        print("  already patched: mac.c reuse started vdev")

    # ---- 5. r590's early start must sit on the channel we will join ------
    if "ieee80211_get_channel(hw->wiphy, 2417)" not in text:
        anchor = one(text, "ieee80211_get_channel(hw->wiphy, 2412)",
                     "mac.c r590 start channel")
        text = text.replace(
            anchor,
            "ieee80211_get_channel(hw->wiphy, 2417)",
        )
        print("  patched: mac.c r590 start channel 2412 -> 2417")
    else:
        print("  already patched: mac.c r590 start channel")

    mac.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
