#!/usr/bin/env python3
"""Keep the primary netdev open across control clients (r594).

r593 stamps the request stages and names the blocker exactly:

    NWDIAG ctl req op 1 gen 0 session 4 owner 4
    NWDIAG ctl open_dev begin radio 1          <- and no "open_dev done"

So the second client's INFO hangs inside dev_open(primary).  The first INFO ran
dev_open in 338 ticks and worked; the difference is that the session in between
closed, and closed() calls dev_close(primary), which runs ath12k_mac_op_stop()
and tears down the vdev that r590 started in add_interface().  The next
dev_open() re-enters ath12k_mac_op_start() with that half-started vdev and never
returns, which parks the wireless worker for good.

Stop closing the netdev when a client goes away.  wpa exiting is a normal event
here (launchd restarts it), the interface itself stays, and the board pump
expects the radio to stay up; keeping it open also makes the next dev_open() a
no-op instead of a re-entry into the start path.  Run after control-stage-log.py
so the keep-open line sits next to the stage trace.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
ctl = root / "src/nw_cfg80211_control.c"

text = ctl.read_text()


def replace(old, new, label):
    global text
    if old not in text:
        raise SystemExit(f"keep-netdev-open: anchor not found: {label}")
    if text.count(old) != 1:
        raise SystemExit(f"keep-netdev-open: anchor not unique ({text.count(old)}): {label}")
    text = text.replace(old, new, 1)


if "NWDIAG ctl keep netdev open" in text:
    print("keep-netdev-open: already patched")
    sys.exit(0)

replace(
    "    cfg80211_mlme_unregister_socket(primary->ieee80211_ptr, 1); sa_query_registered = false;\n"
    "    wiphy_unlock(primary->ieee80211_ptr->wiphy);\n"
    "    dev_close(primary); rtnl_unlock();\n",
    "    cfg80211_mlme_unregister_socket(primary->ieee80211_ptr, 1); sa_query_registered = false;\n"
    "    wiphy_unlock(primary->ieee80211_ptr->wiphy);\n"
    "    /* r594: closing the netdev here runs ath12k_mac_op_stop() on a vdev that\n"
    "     * r590 started in add_interface(); the next client's dev_open() then\n"
    "     * re-enters ath12k_mac_op_start() and never returns (r593 shows\n"
    "     * \"ctl open_dev begin\" with no \"done\").  Keep the radio and the netdev\n"
    "     * up across clients: wpa exiting is routine, the interface stays. */\n"
    "    lx_log(6, \"NWDIAG ctl keep netdev open session %lu\", (unsigned long)session);\n"
    "    rtnl_unlock();\n",
    "closed() dev_close",
)

ctl.write_text(text)
print("keep-netdev-open: dropped dev_close() from the session-close path")
