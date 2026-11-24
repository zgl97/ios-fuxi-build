#!/usr/bin/env python3
"""Temporary experiment: keep the scan channel list within the first
WMI command.  The 2.4 GHz and 5 GHz bands fit in one 58-entry command on
this board; 6 GHz pushes the list into a second append command.  If the
firmware's scan engine rejects the appended list, this makes the list a
single well-formed command again.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
reg = root / "deps/linux/drivers/net/wireless/ath/ath12k/reg.c"
s = reg.read_text(encoding="utf-8")

old_count = """\t\t\tif (bands[band]->channels[i].flags &
\t\t\t    IEEE80211_CHAN_DISABLED)
\t\t\t\tcontinue;
"""
new_count = old_count + """\t\t\tif (bands[band]->channels[i].band == NL80211_BAND_6GHZ)
\t\t\t\tcontinue;
"""
if old_count not in s:
    raise SystemExit("reg.c count loop marker not found")
s = s.replace(old_count, new_count, 1)

old_fill = """\t\t\tif (channel->flags & IEEE80211_CHAN_DISABLED)
\t\t\t\tcontinue;
"""
new_fill = old_fill + """\t\t\tif (channel->band == NL80211_BAND_6GHZ)
\t\t\t\tcontinue;
"""
if old_fill not in s:
    raise SystemExit("reg.c fill loop marker not found")
s = s.replace(old_fill, new_fill, 1)

old_num = """\tif (!num_channels) {
\t\tath12k_dbg(ar->ab, ATH12K_DBG_REG,
"""
new_num = """\tath12k_info(ar->ab, "chan list skip 6ghz: %d channels\\n", num_channels);

\tif (!num_channels) {
\t\tath12k_dbg(ar->ab, ATH12K_DBG_REG,
"""
if old_num not in s:
    raise SystemExit("reg.c num_channels marker not found")
s = s.replace(old_num, new_num, 1)

reg.write_text(s, encoding="utf-8")
print("chan-list-skip-6g: patched reg.c")
