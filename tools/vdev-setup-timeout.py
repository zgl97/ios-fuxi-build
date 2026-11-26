#!/usr/bin/env python3
"""Give WMI_VDEV_START a 15 s deadline instead of 5 s (r574).

r567 started vdev 0 87 jiffies after WMI_VDEV_START was queued.  r573 booted
the byte-identical r567 payload (payload-r567.bin and payload-r573.bin share
SHA-256 49328A1C...) and never received WMI_VDEV_START_RESP_EVENTID for any of
its four attempts: ath12k_mac_vdev_setup_sync() waited
ATH12K_VDEV_SETUP_TIMEOUT_HZ and returned -ETIMEDOUT, after which the WMI/HTC
queues filled up and every WMI_PDEV_SET_PARAM returned -ENOBUFS.

The open question is whether the firmware answers late or never.  In r573 the
station asked for its vdev while the firmware was still scanning
(`scan ev type 8 ... state 2` right before `vdev_start_restart`, and no
`scan ev type 2` COMPLETED anywhere in the boot).  Raising the deadline keeps
the answer cheap:

  * a late WMI_VDEV_START_RESP makes setup_sync return 0 and the auth frame
    leaves the driver -> the scan/vdev ordering is the real problem and a
    wait-for-scan-done gate is the fix;
  * a second -110 after 15000 jiffies proves the firmware is wedged, not slow,
    and the ordering has to change without another timing guess.

Both paths print one line, so a single boot log decides it.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
ath = root / "deps/linux/drivers/net/wireless/ath/ath12k"
wmi_h = ath / "wmi.h"
wmi_c = ath / "wmi.c"
mac = ath / "mac.c"


def replace(path, old, new, label):
    s = path.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"{label}: pattern not found in {path}")
    path.write_text(s.replace(old, new, 1), encoding="utf-8")


# --- wmi.h: the deadline itself ---------------------------------------------
replace(
    wmi_h,
    "#define ATH12K_VDEV_SETUP_TIMEOUT_HZ (5 * HZ)",
    "#define ATH12K_VDEV_SETUP_TIMEOUT_HZ (15 * HZ)",
    "wmi.h vdev setup timeout",
)

# --- mac.c: say how long the wait actually took -----------------------------
replace(
    mac,
    """static int ath12k_mac_vdev_setup_sync(struct ath12k *ar)
{
\tlockdep_assert_wiphy(ath12k_ar_to_hw(ar)->wiphy);

\tif (test_bit(ATH12K_FLAG_CRASH_FLUSH, &ar->ab->dev_flags))
\t\treturn -ESHUTDOWN;

\tath12k_dbg(ar->ab, ATH12K_DBG_MAC, "vdev setup timeout %d\\n",
\t\t   ATH12K_VDEV_SETUP_TIMEOUT_HZ);

\tif (!wait_for_completion_timeout(&ar->vdev_setup_done,
\t\t\t\t\t ATH12K_VDEV_SETUP_TIMEOUT_HZ))
\t\treturn -ETIMEDOUT;

\treturn ar->last_wmi_vdev_start_status ? -EINVAL : 0;
}""",
    """static int ath12k_mac_vdev_setup_sync(struct ath12k *ar)
{
\tunsigned long started;

\tlockdep_assert_wiphy(ath12k_ar_to_hw(ar)->wiphy);

\tif (test_bit(ATH12K_FLAG_CRASH_FLUSH, &ar->ab->dev_flags))
\t\treturn -ESHUTDOWN;

\tath12k_dbg(ar->ab, ATH12K_DBG_MAC, "vdev setup timeout %d\\n",
\t\t   ATH12K_VDEV_SETUP_TIMEOUT_HZ);

\tstarted = jiffies;
\tif (!wait_for_completion_timeout(&ar->vdev_setup_done,
\t\t\t\t\t ATH12K_VDEV_SETUP_TIMEOUT_HZ)) {
\t\tath12k_info(ar->ab,
\t\t\t    "NWDIAG vdev_setup timeout waited %lu limit %u jiffies %lu\\n",
\t\t\t    jiffies - started, ATH12K_VDEV_SETUP_TIMEOUT_HZ, jiffies);
\t\treturn -ETIMEDOUT;
\t}

\tath12k_info(ar->ab,
\t\t    "NWDIAG vdev_setup done waited %lu limit %u status %d jiffies %lu\\n",
\t\t    jiffies - started, ATH12K_VDEV_SETUP_TIMEOUT_HZ,
\t\t    ar->last_wmi_vdev_start_status, jiffies);

\treturn ar->last_wmi_vdev_start_status ? -EINVAL : 0;
}""",
    "mac.c vdev setup sync",
)

# --- wmi.c: log the response event itself -----------------------------------
replace(
    wmi_c,
    """\tar->max_allowed_tx_power = (s8)le32_to_cpu(vdev_start_resp.max_allowed_tx_power);

\tcomplete(&ar->vdev_setup_done);""",
    """\tar->max_allowed_tx_power = (s8)le32_to_cpu(vdev_start_resp.max_allowed_tx_power);

\tath12k_info(ab, "NWDIAG vdev_start_resp vdev %u status %d jiffies %lu\\n",
\t\t    le32_to_cpu(vdev_start_resp.vdev_id), status, jiffies);

\tcomplete(&ar->vdev_setup_done);""",
    "wmi.c vdev start resp",
)

print("vdev-setup-timeout: 5 s -> 15 s, elapsed/status/resp logging added")
