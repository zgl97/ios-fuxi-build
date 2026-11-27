#!/usr/bin/env python3
"""r599: the txpower WMI call is what eats the authentication frame now.

r598 finally clears the channel assignment:

    L9173  NWDIAG mac auth prep begin freq 2417
    L9175  NWDIAG change_chanctx begin changed 0x10    -> done
    L9177  NWDIAG chan assign begin
    L9179  NWDIAG vdev_start skip vdev 0 fw_started freq 2417
    L9180  NWDIAG chan assign done 0                   <- first success
    L9182  NWDIAG bss_info begin changed 0x40000       <- BSS_CHANGED_TXPOWER
    (no `bss_info done`)
    L9189  SME: Authentication request to the driver failed

0x40000 is BSS_CHANGED_TXPOWER (include/net/mac80211.h:388), and that branch
calls ath12k_mac_txpower_recalc(), which issues
ath12k_wmi_pdev_set_param(WMI_PDEV_PARAM_TXPOWER_LIMIT2G/5G).  With the WMI
command path already saturated that send never returns, so the callback never
completes and mac80211 never builds the authentication frame.

Transmit power is irrelevant for getting an association through, so record the
value and skip the firmware call; the log line keeps the fact visible.
"""

import pathlib
import sys

MAC = "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"

OLD = """	if (changed & BSS_CHANGED_TXPOWER) {
		ath12k_dbg(ar->ab, ATH12K_DBG_MAC, "mac vdev_id %i txpower %d\\n",
			   arvif->vdev_id, info->txpower);

		arvif->txpower = info->txpower;
		ath12k_mac_txpower_recalc(ar);
	}
"""

NEW = """	if (changed & BSS_CHANGED_TXPOWER) {
		/* r599: ath12k_mac_txpower_recalc() -> ath12k_wmi_pdev_set_param()
		 * never returns here, which leaves link_info_changed() unfinished
		 * and mac80211 never sends the authentication frame.  Keep the
		 * value, skip the firmware call.
		 */
		ath12k_info(ar->ab, "NWDIAG bss_info skip txpower vdev %u txpower %d\\n",
			    arvif->vdev_id, info->txpower);

		arvif->txpower = info->txpower;
	}
"""


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: bss-info-skip-txpower.py <tree>")
    mac = pathlib.Path(sys.argv[1]) / MAC
    text = mac.read_text()

    if "NWDIAG bss_info skip txpower" in text:
        print("bss-info-skip-txpower: already patched")
        return 0

    n = text.count(OLD)
    if n != 1:
        raise SystemExit(
            "bss-info-skip-txpower: txpower block count %d (expected 1)" % n
        )
    mac.write_text(text.replace(OLD, NEW))
    print("bss-info-skip-txpower: patched ath12k/mac.c (txpower recalc skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
