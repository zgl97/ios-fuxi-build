#!/usr/bin/env python3
"""Create and start the STA vdev before anything scans (r590).

r589 finally handed six BSS to wpa (`sheng: scan results 6`) and the
supplicant picked `409`, but the firmware never answered the
`WMI_VDEV_START` that `ath12k_mac_op_assign_vif_chanctx()` sends at
authentication time.  The unanswered command then jammed the WMI channel for
good:

    NWDIAG vdev_setup timeout waited 15000 limit 15000
    NWDIAG vdev_start setup_sync vdev 0 ret -110
    Timeout in receiving vdev delete response
    failed to send WMI_PDEV_SET_PARAM cmd
    failed to submit WMI_VDEV_DELETE_CMDID
    failed to submit vdev_start cmd
    failed to submit WMI_VDEV_CREATE_CMDID

`ath12k_mac_op_add_interface()` deliberately defers vdev creation, so up to
now the first vdev only existed because a scan created one, and the very first
start request was aimed at the vdev the scan had just torn down.  Bring the
station vdev up on channel 1 at interface-add time instead: the firmware sees a
start on a live vdev while nothing else is running, and authentication later
finds it already started.

Run before scan-* tools that touch mac.c so the anchors stay unique.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
mac = root / "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"

text = mac.read_text()

if "r590: create and start the STA vdev" in text:
    print("vdev-start-on-add: already patched")
    sys.exit(0)

DECL = """static int ath12k_mac_vdev_create(struct ath12k *ar, struct ath12k_link_vif *arvif);
"""
DECL_NEW = """static int ath12k_mac_vdev_create(struct ath12k *ar, struct ath12k_link_vif *arvif);
static int ath12k_mac_vdev_start(struct ath12k_link_vif *arvif,
				 struct ieee80211_chanctx_conf *ctx);
"""

if text.count(DECL) != 1:
    raise SystemExit(
        "vdev-start-on-add: expected one vdev_create forward declaration, found %d"
        % text.count(DECL)
    )

DEFER = """	/* Defer vdev creation until assign_chanctx or hw_scan is initiated as driver
	 * will not know if this interface is an ML vif at this point.
	 */
	return 0;
}
"""

START = """	/* r590: create and start the STA vdev right here, on channel 1, before
	 * any scan runs.  The firmware never answered the start request that
	 * assign_vif_chanctx() used to send at authentication, and that single
	 * unanswered command jammed every later WMI command with -105/-110.
	 */
	if (vif->type == NL80211_IFTYPE_STATION) {
		struct ieee80211_chanctx_conf start_ctx = { };
		const struct ieee80211_channel *start_chan;
		bool started = false;

		for_each_ar(ah, ar, i) {
			if (!arvif->ar)
				arvif->ar = ar;
		}

		start_chan = ieee80211_get_channel(hw->wiphy, 2417);
		if (arvif->ar && start_chan) {
			cfg80211_chandef_create(&start_ctx.def, start_chan,
						NL80211_CHAN_HT20);
			if (ath12k_mac_vdev_create(arvif->ar, arvif))
				ath12k_warn(arvif->ar->ab,
					    "r590: vdev create at add_interface failed\\n");
			else if (ath12k_mac_vdev_start(arvif, &start_ctx))
				ath12k_warn(arvif->ar->ab,
					    "r590: vdev start at add_interface failed\\n");
			else
				started = true;
		}
		if (arvif->ar)
			ath12k_info(arvif->ar->ab,
				    "NWDIAG r590 sta vdev prepared %d vdev %u\\n",
				    started, arvif->vdev_id);
	}

	/* Defer the rest until assign_chanctx or hw_scan: the interface may
	 * still turn out to be an ML vif.
	 */
	return 0;
}
"""

if text.count(DEFER) != 1:
    raise SystemExit(
        "vdev-start-on-add: expected one deferral block, found %d" % text.count(DEFER)
    )

text = text.replace(DECL, DECL_NEW, 1).replace(DEFER, START, 1)
mac.write_text(text, encoding="utf-8")
print("vdev-start-on-add: STA vdev created and started at add_interface")
