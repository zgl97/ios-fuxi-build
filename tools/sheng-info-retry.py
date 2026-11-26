#!/usr/bin/env python3
"""r575: retry NW_CONTROL_INFO instead of tearing the interface down.

r574 console (build/monitor-r574/boot.log):

  L4974 sheng: rpc op=0 failed errno=60 (INFO)
  L4977 en0: Failed to initialize driver interface
  L5640 Remove interface en0 from radio

The board scan runs right after the interface appears and holds the wiphy
locks for a few seconds, so the first INFO exchange can time out.  Failing it
makes wpa destroy the interface, and every later scan lands on a dead vdev -
that is the whole r574 regression.  Retry a few times before giving up.

Two source variants exist: the upstream sheng_init() (what CI checks out) and
the instrumented one the device currently runs, which also prints the errno.
Both are accepted so this script works on either base.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "userspace/driver_sheng.c"
s = path.read_text(encoding="utf-8")

upstream = """  if (sheng_begin(driver, &b, bytes, sizeof(bytes), NW_CONTROL_INFO) ||
      sheng_rpc(driver, &b, &reply)) {
    wpa_printf(MSG_ERROR, "sheng: native INFO failed: %s", strerror(errno)); goto fail;
  }
"""

instrumented = """  if (sheng_begin(driver, &b, bytes, sizeof(bytes), NW_CONTROL_INFO) ||
      sheng_rpc(driver, &b, &reply)) {
    fprintf(stderr, "sheng: INFO rpc failed errno=%d\\n", errno);
    wpa_printf(MSG_ERROR, "sheng: native INFO failed: %s", strerror(errno)); goto fail;
  }
"""

new = """  /* r575: the board-path scan grabs the wiphy locks right after the interface
   * appears, so INFO can time out while wpa is still starting.  Retrying keeps
   * the interface alive; destroying it turned every later scan into a scan on
   * a dead vdev (_r574 console: "Remove interface en0 from radio" then
   * "Received scan event for unknown vdev"). */
  {
    int info_try, info_errno = 0;

    for (info_try = 1; info_try <= 8; info_try++) {
      if (!sheng_begin(driver, &b, bytes, sizeof(bytes), NW_CONTROL_INFO) &&
          !sheng_rpc(driver, &b, &reply))
        break;
      info_errno = errno;
      fprintf(stderr, "sheng: INFO attempt %d failed errno=%d\\n",
              info_try, info_errno);
      if (info_try < 8)
        os_sleep(1, 0);
    }

    if (info_try > 8) {
      errno = info_errno;
      fprintf(stderr, "sheng: INFO rpc failed errno=%d\\n", errno);
      wpa_printf(MSG_ERROR, "sheng: native INFO failed: %s", strerror(errno)); goto fail;
    }
  }
"""

matches = [candidate for candidate in (upstream, instrumented) if s.count(candidate) == 1]
if len(matches) != 1:
    raise SystemExit(
        "sheng_init INFO anchor not found (upstream=%d instrumented=%d)" % (
            s.count(upstream), s.count(instrumented)))
s = s.replace(matches[0], new, 1)
path.write_text(s, encoding="utf-8")
print("sheng-info-retry: patched userspace/driver_sheng.c (retry x8)")
