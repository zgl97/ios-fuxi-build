#!/usr/bin/env python3
"""r596: follow the link-up path from the channel context to the auth frame.

r595 narrowed the r594 auth stall to this window:

    ieee80211_prep_connection()          -> printed  (mac auth prep begin)
      ieee80211_prep_channel()
        ieee80211_link_use_channel()
          ieee80211_assign_link_chanctx()
            drv_assign_vif_chanctx()     -> printed  (vdev_start ... ret 0)
                                          never comes back

so the frame never reaches ieee80211_auth() and no mgmt frame is ever queued.
This tool brackets every step after drv_assign_vif_chanctx returns so the next
run shows which callback (or which mac80211 recalc) never finishes:

    chan.c   assign begin / assign done / recalc done / use done
    mac.c    op_link_info_changed  begin+done    (the callback that carries BSSID)
    mac.c    op_change_chanctx     begin+done    (fired by the chanctx recalcs)
    mac.c    ath12k_mac_bss_info_changed begin+done

All inserts are idempotent (skip when the marker is already present) and each
anchor must appear exactly once, so a kernel re-import cannot silently change
the meaning of a patch.
"""

import pathlib
import sys

CHAN = "deps/linux/net/mac80211/chan.c"
MAC = "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"


def one(text, needle, what):
    count = text.count(needle)
    if count != 1:
        raise SystemExit(f"{what}: anchor count {count} (expected 1)")
    return needle


def patch_before(path, needle, insert, marker, what):
    text = path.read_text()
    if marker in text:
        print(f"  already patched: {what}")
        return
    one(text, needle, what)
    path.write_text(text.replace(needle, insert + needle))
    print(f"  patched: {what}")


def patch_after(path, needle, insert, marker, what):
    text = path.read_text()
    if marker in text:
        print(f"  already patched: {what}")
        return
    one(text, needle, what)
    path.write_text(text.replace(needle, needle + insert))
    print(f"  patched: {what}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: chanctx-path-log.py <tree>")
    root = pathlib.Path(sys.argv[1])
    chan = root / CHAN
    mac = root / MAC

    # ---- mac80211: chan contexts -------------------------------------------
    patch_before(
        chan,
        "\t\tret = drv_assign_vif_chanctx(local, sdata, link->conf, new_ctx);\n",
        '\t\tpr_info("NWDIAG chan assign begin\\n");\n',
        "NWDIAG chan assign begin",
        "chan.c assign begin",
    )
    patch_after(
        chan,
        "\t\tret = drv_assign_vif_chanctx(local, sdata, link->conf, new_ctx);\n",
        '\t\tpr_info("NWDIAG chan assign done %d\\n", ret);\n',
        "NWDIAG chan assign done",
        "chan.c assign done",
    )
    patch_after(
        chan,
        "\tif (new_ctx && ieee80211_chanctx_num_assigned(local, new_ctx) > 0) {\n"
        "\t\tieee80211_recalc_txpower(link, false);\n"
        "\t\tieee80211_recalc_chanctx_min_def(local, new_ctx);\n"
        "\t}\n",
        '\tpr_info("NWDIAG chan recalc done\\n");\n',
        "NWDIAG chan recalc done",
        "chan.c recalc done",
    )
    patch_after(
        chan,
        "\tret = ieee80211_assign_link_chanctx(link, ctx, assign_on_failure);\n",
        '\tpr_info("NWDIAG chan use done %d\\n", ret);\n',
        "NWDIAG chan use done",
        "chan.c use done",
    )

    # ---- ath12k: the two callbacks that run underneath those recalcs --------
    patch_before(
        mac,
        "\tath12k_mac_bss_info_changed(ar, arvif, info, changed);\n",
        '\tath12k_info(ar->ab, "NWDIAG bss_info begin changed 0x%llx\\n",\n'
        "\t\t    (unsigned long long)changed);\n",
        "NWDIAG bss_info begin",
        "mac.c link_info_changed begin",
    )
    patch_after(
        mac,
        "\tath12k_mac_bss_info_changed(ar, arvif, info, changed);\n",
        '\tath12k_info(ar->ab, "NWDIAG bss_info done changed 0x%llx\\n",\n'
        "\t\t    (unsigned long long)changed);\n",
        "NWDIAG bss_info done",
        "mac.c link_info_changed done",
    )
    change_anchor = (
        "\tab = ar->ab;\n\n\tath12k_dbg(ab, ATH12K_DBG_MAC,\n"
        '\t\t   "mac chanctx change freq %u width %d ptr %p changed %x\\n",\n'
        "\t\t   ctx->def.chan->center_freq, ctx->def.width, ctx, changed);\n"
    )
    patch_after(
        mac,
        change_anchor,
        '\tath12k_info(ab, "NWDIAG change_chanctx begin changed 0x%x\\n", changed);\n',
        "NWDIAG change_chanctx begin",
        "mac.c change_chanctx begin",
    )
    patch_before(
        mac,
        "\t/* TODO: Recalc radar detection */\n}\nEXPORT_SYMBOL(ath12k_mac_op_change_chanctx);\n",
        '\tath12k_info(ab, "NWDIAG change_chanctx done changed 0x%x\\n", changed);\n',
        "NWDIAG change_chanctx done",
        "mac.c change_chanctx done",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
