#!/usr/bin/env python3
"""Split the control request into its stages (r593).

r592's trace shows the shape precisely:

    NWDIAG ctl open session 2 ... / ctl open done session 2
    NWDIAG ctl req op 1 gen 0 session 2 owner 2
    NWDIAG ctl rsp op 1 result 0 ticks 663        <- INFO took 663 ms but worked
    NWDIAG ctl close session 2 owner 2 / close done
    NWDIAG ctl open session 4 ... / open done
    NWDIAG ctl req op 1 gen 0 session 4 owner 4   <- and then nothing

op 1 is NW_CONTROL_INFO (nw_control.h:25), so the worker wedges inside the
second session's very first INFO.  That path takes rtnl_lock, calls
dev_open(primary), then takes the wiphy lock and runs the handler.  The 663 ms
on the first INFO says dev_open already does real work (a full
ath12k_mac_op_start), and r590 moved a vdev_start into add_interface, so the
second dev_open re-enters that state.

Stamp each stage separately so the log names the one that never returns.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
ctl = root / "src/nw_cfg80211_control.c"

text = ctl.read_text()


def replace(old, new, label):
    global text
    if old not in text:
        raise SystemExit(f"control-stage-log: anchor not found: {label}")
    if text.count(old) != 1:
        raise SystemExit(f"control-stage-log: anchor not unique ({text.count(old)}): {label}")
    text = text.replace(old, new, 1)


if "NWDIAG ctl open_dev begin" in text:
    print("control-stage-log: already patched")
    sys.exit(0)

replace(
    "  int result = radio_enabled ? dev_open(primary, NULL) : 0;\n"
    "  if (!result) {\n"
    "    wiphy_lock(&rdev->wiphy);\n"
    "    result = request_locked(rdev, request, reply);\n"
    "    wiphy_unlock(&rdev->wiphy);\n"
    "  } else result = common_error(result);\n",
    "  /* r593: one line per stage; the stage without a \"done\" line is the\n"
    "   * one that blocks the wireless worker. */\n"
    "  lx_log(6, \"NWDIAG ctl open_dev begin radio %d\", radio_enabled ? 1 : 0);\n"
    "  int result = radio_enabled ? dev_open(primary, NULL) : 0;\n"
    "  lx_log(6, \"NWDIAG ctl open_dev done result %d\", result);\n"
    "  if (!result) {\n"
    "    lx_log(6, \"NWDIAG ctl lock begin\");\n"
    "    wiphy_lock(&rdev->wiphy);\n"
    "    lx_log(6, \"NWDIAG ctl lock done\");\n"
    "    result = request_locked(rdev, request, reply);\n"
    "    lx_log(6, \"NWDIAG ctl handler done result %d\", result);\n"
    "    wiphy_unlock(&rdev->wiphy);\n"
    "  } else result = common_error(result);\n",
    "native_request_impl stages",
)

ctl.write_text(text)
print("control-stage-log: patched src/nw_cfg80211_control.c")
