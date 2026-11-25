#!/usr/bin/env python3
"""r570: let the delayed board scan wait for a real control client.

r569 showed the delayed scan was aborted 2 ms after WMI_SCAN_EVENT_STARTED,
while a wpa_supplicant instance whose init had just failed was tearing the
interface down.  ath12k_mac_op_unassign_vif_chanctx() calls ath12k_scan_abort()
for the shared vdev, which cancels the only scan this firmware will ever run.

A single INFO exchange is not enough evidence: wpa can complete INFO and then
fail its very next request, so the scan would still race its teardown.  Count
GET_BSS batches instead - the device wpa_supplicant only reaches its scan
result loop after init has fully succeeded, and two batches prove it survived
a full 5 s scan interval with the control socket still open.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "src/nw_cfg80211_control.c"
s = path.read_text(encoding="utf-8")


def replace(old, new, label):
    global s
    if old not in s:
        raise SystemExit(f"scan-after-client: anchor not found: {label}")
    s = s.replace(old, new, 1)


replace(
    "static uint64_t owner, generation = 1;",
    "static uint64_t owner, generation = 1;\n"
    "static unsigned long owner_since, last_info_jiffies, last_get_bss_jiffies;\n"
    "static unsigned nw_get_bss_batches;",
    "owner globals",
)

replace(
    "static void opened(void *unused, uint64_t session) { (void)unused; owner = session; generation++; snapshot_free(); }",
    "static void opened(void *unused, uint64_t session) { (void)unused; owner = session; owner_since = jiffies;\n"
    "  last_info_jiffies = 0; last_get_bss_jiffies = 0; nw_get_bss_batches = 0; generation++; snapshot_free(); }",
    "opened",
)

replace(
    "  owner = 0; generation++; snapshot_free();\n  if (primary) {",
    "  owner = 0; last_info_jiffies = 0; last_get_bss_jiffies = 0; nw_get_bss_batches = 0;\n"
    "  generation++; snapshot_free();\n  if (primary) {",
    "closed",
)

replace(
    "  if (nw_control_get_u32(request, NW_CONTROL_ATTR_BSS_INDEX, &index)) return NW_CONTROL_INVALID;",
    "  if (nw_control_get_u32(request, NW_CONTROL_ATTR_BSS_INDEX, &index)) return NW_CONTROL_INVALID;\n"
    "  if (owner) {\n"
    "    if (!index) nw_get_bss_batches++;\n"
    "    last_get_bss_jiffies = jiffies;\n"
    "  }",
    "get_bss bookkeeping",
)

replace(
    "int nw_cfg80211_begin_attempt(void) {",
    "int nw_cfg80211_client_ready(void) {\n"
    "  return owner && last_info_jiffies && nw_get_bss_batches >= 2 &&\n"
    "         time_after(jiffies, last_get_bss_jiffies + msecs_to_jiffies(300));\n"
    "}\n"
    "int nw_cfg80211_begin_attempt(void) {",
    "client_ready helper",
)

path.write_text(s, encoding="utf-8")
print("scan-after-client: patched src/nw_cfg80211_control.c")
