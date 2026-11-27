#!/usr/bin/env python3
"""Stamp the station authentication path inside mac80211 (r595).

r594 finally gets the whole stack to line up: the firmware completes the 1.5 s
scan, cfg80211 hands four BSS entries to wpa_supplicant, `selected BSS
a4:39:b3:c6:4f:22 ssid='409'` leads to `SME: Trying to authenticate`, and the
control request for NW_CONTROL_AUTHENTICATE assigns the channel context and
starts vdev 0 at 2417 MHz (`vdev_setup done waited 30 limit 15000 status 0`).

Then nothing.  The kernel never logs `NWDIAG op_tx mgmt`, `mgmt_tx queued` or
`wmi mgmt send`, so mac80211 never handed an authentication frame to the
driver, and the control request itself never returns - the supplicant gives up
after its 3 s timeout and drops the interface.  Decide whether the block sits in
ieee80211_prep_connection() (driver callbacks for BSSID/STA state) or right
before the frame is queued, by stamping each step.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
mlme = root / "deps/linux/net/mac80211/mlme.c"

text = mlme.read_text()


def replace(old, new, label):
    global text
    if old not in text:
        raise SystemExit(f"auth-path-log: anchor not found: {label}")
    if text.count(old) != 1:
        raise SystemExit(f"auth-path-log: anchor not unique ({text.count(old)}): {label}")
    text = text.replace(old, new, 1)


if "NWDIAG mac auth prep begin" in text:
    print("auth-path-log: already patched")
    sys.exit(0)

# 1) Entering/leaving ieee80211_prep_connection() from ieee80211_mgd_auth().
replace(
    "\terr = ieee80211_prep_connection(sdata, req->bss, req->link_id,\n"
    "\t\t\t\t\treq->ap_mld_addr, cont_auth,\n"
    "\t\t\t\t\t&conn, false,\n"
    "\t\t\t\t\tauth_data->userspace_selectors);\n"
    "\tif (err)\n"
    "\t\tgoto err_clear;\n",
    "\tsdata_info(sdata, \"NWDIAG mac auth prep begin freq %u\\n\",\n"
    "\t\t   req->bss->channel->center_freq);\n"
    "\terr = ieee80211_prep_connection(sdata, req->bss, req->link_id,\n"
    "\t\t\t\t\treq->ap_mld_addr, cont_auth,\n"
    "\t\t\t\t\t&conn, false,\n"
    "\t\t\t\t\tauth_data->userspace_selectors);\n"
    "\tsdata_info(sdata, \"NWDIAG mac auth prep done %d\\n\", err);\n"
    "\tif (err)\n"
    "\t\tgoto err_clear;\n",
    "mgd_auth prep_connection",
)

# 2) Around the frame submission itself.
replace(
    "\terr = ieee80211_auth(sdata);\n"
    "\tif (err) {\n"
    "\t\tsta_info_destroy_addr(sdata, auth_data->ap_addr);\n"
    "\t\tgoto err_clear;\n"
    "\t}\n",
    "\tsdata_info(sdata, \"NWDIAG mac auth send begin\\n\");\n"
    "\terr = ieee80211_auth(sdata);\n"
    "\tsdata_info(sdata, \"NWDIAG mac auth send done %d\\n\", err);\n"
    "\tif (err) {\n"
    "\t\tsta_info_destroy_addr(sdata, auth_data->ap_addr);\n"
    "\t\tgoto err_clear;\n"
    "\t}\n",
    "mgd_auth ieee80211_auth",
)

# 3) The two driver callbacks ieee80211_prep_connection() makes once the STA
#    entry exists: BSSID notification and sta_info_insert().
replace(
    "\t\tieee80211_link_info_change_notify(sdata, link,\n"
    "\t\t\t\t\t\t  BSS_CHANGED_BSSID |\n"
    "\t\t\t\t\t\t  BSS_CHANGED_BASIC_RATES |\n"
    "\t\t\t\t\t\t  BSS_CHANGED_BEACON_INT);\n",
    "\t\tsdata_info(sdata, \"NWDIAG mac prep notify begin bssid %pM\\n\",\n"
    "\t\t\t   link->u.mgd.bssid);\n"
    "\t\tieee80211_link_info_change_notify(sdata, link,\n"
    "\t\t\t\t\t\t  BSS_CHANGED_BSSID |\n"
    "\t\t\t\t\t\t  BSS_CHANGED_BASIC_RATES |\n"
    "\t\t\t\t\t\t  BSS_CHANGED_BEACON_INT);\n"
    "\t\tsdata_info(sdata, \"NWDIAG mac prep notify done\\n\");\n",
    "prep_connection bss_info_changed",
)
replace(
    "\t\terr = sta_info_insert(new_sta);\n"
    "\t\tnew_sta = NULL;\n"
    "\t\tif (err) {\n",
    "\t\tsdata_info(sdata, \"NWDIAG mac prep insert begin\\n\");\n"
    "\t\terr = sta_info_insert(new_sta);\n"
    "\t\tnew_sta = NULL;\n"
    "\t\tsdata_info(sdata, \"NWDIAG mac prep insert done %d\\n\", err);\n"
    "\t\tif (err) {\n",
    "prep_connection sta_info_insert",
)

mlme.write_text(text)
print("auth-path-log: patched net/mac80211/mlme.c")
