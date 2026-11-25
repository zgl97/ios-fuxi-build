#!/usr/bin/env python3
"""r570: let the delayed board scan wait for a real control client.

r569 showed the delayed scan was aborted 2 ms after WMI_SCAN_EVENT_STARTED,
while a wpa_supplicant instance whose init had just failed was tearing the
interface down.  ath12k_mac_op_unassign_vif_chanctx() calls ath12k_scan_abort()
for the shared vdev, which cancels the only scan this firmware will ever run.

Track when a control client opens the socket and when it completes its first
NW_CONTROL_INFO exchange.  nw_mainline polls nw_cfg80211_client_ready() before
starting the board scan, so the scan never races a client that is still
starting up or failing.
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
    "static uint64_t owner, generation = 1;\nstatic unsigned long owner_since, last_info_jiffies;",
    "owner globals",
)

replace(
    "static void opened(void *unused, uint64_t session) { (void)unused; owner = session; generation++; snapshot_free(); }",
    "static void opened(void *unused, uint64_t session) { (void)unused; owner = session; owner_since = jiffies; last_info_jiffies = 0; generation++; snapshot_free(); }",
    "opened",
)

replace(
    "  owner = 0; generation++; snapshot_free();\n  if (primary) {",
    "  owner = 0; last_info_jiffies = 0; generation++; snapshot_free();\n  if (primary) {",
    "closed",
)

replace(
    "  } else result = common_error(result);\n  rtnl_unlock();\n  return result;\n}",
    "  } else result = common_error(result);\n  rtnl_unlock();\n  if (!result && request->header.operation == NW_CONTROL_INFO)\n    last_info_jiffies = jiffies;\n  return result;\n}",
    "native_request INFO bookkeeping",
)

replace(
    "int nw_cfg80211_begin_attempt(void) {",
    "int nw_cfg80211_client_ready(void) {\n"
    "  return owner && last_info_jiffies &&\n"
    "         time_after(jiffies, last_info_jiffies + msecs_to_jiffies(500));\n"
    "}\n"
    "int nw_cfg80211_begin_attempt(void) {",
    "client_ready helper",
)

path.write_text(s, encoding="utf-8")
print("scan-after-client: patched src/nw_cfg80211_control.c")
