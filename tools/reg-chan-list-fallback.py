#!/usr/bin/env python3
"""Keep the scan channel list alive when the regulatory range never arrived.

ath12k_reg_update_chan_list() filters the wiphy channels by ar->freq_range,
which only becomes non-zero once WMI_REG_CHAN_LIST_CC_EXT has been handled.
When it is still zero every channel is skipped, the function returns -EINVAL
and ath12k_mac_start() bails out before WMI_SCAN_CHAN_LIST is ever sent, so
the firmware never emits WMI_SCAN_EVENTID for a scan started later.

Fall back to every enabled channel and log the state we were in.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
reg = root / "deps/linux/drivers/net/wireless/ath/ath12k/reg.c"

lines = reg.read_text(encoding="utf-8").split("\n")

RANGE_GUARD = [
    "\t\t\tif (bands[band]->channels[i].center_freq <",
    "\t\t\t    KHZ_TO_MHZ(ar->freq_range.start_freq) ||",
    "\t\t\t    bands[band]->channels[i].center_freq >",
    "\t\t\t    KHZ_TO_MHZ(ar->freq_range.end_freq))",
]
RANGE_GUARD_ALT = [
    "\t\tif (bands[band]->channels[i].center_freq <",
    "\t\t    KHZ_TO_MHZ(ar->freq_range.start_freq) ||",
    "\t\t    bands[band]->channels[i].center_freq >",
    "\t\t    KHZ_TO_MHZ(ar->freq_range.end_freq))",
]

out = []
replaced = 0
i = 0
while i < len(lines):
    window = lines[i:i + 4]
    guard = None
    indent = None
    if window == RANGE_GUARD:
        guard, indent = RANGE_GUARD, "\t\t\t"
    elif window == RANGE_GUARD_ALT:
        guard, indent = RANGE_GUARD_ALT, "\t\t"
    if guard is None:
        out.append(lines[i])
        i += 1
        continue

    out.append(f"{indent}if (ar->freq_range.start_freq &&")
    out.append(f"{indent}    ar->freq_range.end_freq &&")
    out.append(f"{indent}    (bands[band]->channels[i].center_freq <")
    out.append(f"{indent}     KHZ_TO_MHZ(ar->freq_range.start_freq) ||")
    out.append(f"{indent}     bands[band]->channels[i].center_freq >")
    out.append(f"{indent}     KHZ_TO_MHZ(ar->freq_range.end_freq)))")
    replaced += 1
    i += 4

if replaced != 2:
    raise SystemExit(f"expected 2 range guards, replaced {replaced}")

text = "\n".join(out)

anchor = "\tbands = hw->wiphy->bands;\n"
if anchor not in text:
    raise SystemExit("reg.c bands anchor not found")
text = text.replace(
    anchor,
    anchor
    + "\tif (!ar->freq_range.start_freq || !ar->freq_range.end_freq)\n"
    + "\t\tath12k_info(ar->ab,\n"
    + '\t\t\t    "chan list: no regulatory range yet (%u->%u kHz), using every enabled channel\\n",\n'
    + "\t\t\t    ar->freq_range.start_freq, ar->freq_range.end_freq);\n",
    1,
)

tail_anchor = "\tif (!num_channels) {\n"
if tail_anchor not in text:
    raise SystemExit("reg.c num_channels anchor not found")
text = text.replace(
    tail_anchor,
    '\tath12k_info(ar->ab, "chan list: %d channels for pdev %u\\n",\n'
    + "\t\t    num_channels, ar->pdev->pdev_id);\n"
    + tail_anchor,
    1,
)

reg.write_text(text, encoding="utf-8")
print("reg-chan-list-fallback: patched")
