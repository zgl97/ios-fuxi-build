#!/usr/bin/env python3
"""Force the STA vdev to start in assign_vif_chanctx for wcn7850 (r563).

r562 NWDIAG proved the 3 s peer-create timeout is a start-order deadlock:
  NWDIAG sta_add vdev 1 created 1 started 0        (is_started == 0)
  NWDIAG peer create send vdev 1 ret 0             (WMI PEER_CREATE TX ok)
  NWDIAG wait_dp_link_peer vdev 1 expect 1 ret 0   (wait_event_timeout -> 0 = no PEER_MAP)
  (no "NWDIAG htt t2h PEER", no "NWDIAG dppeer map")
Because wifi7 hw_params has vdev_start_delay = true for wcn7850, the sequence is:
  assign_vif_chanctx -> sees no dp link peer -> skips vdev_start (is_started stays 0)
  station_add        -> peer_create first, waits 3 s for HTT PEER_MAP
  (deadlock: vdev only starts AFTER peer_create succeeds, but firmware won't
   map the peer until the vdev is started)
Flip vdev_start_delay to false so assign_vif_chanctx starts the STA vdev first;
firmware should then emit PEER_MAP on peer_create.  Instrumentation (peer-htt-diag
v2) stays in place to confirm vdev_start_restart / setup_sync / PEER_MAP.

The string ".vdev_start_delay = true," occurs twice (wcn7850 hw2.0 AND qcc2072),
so anchor on the "wcn7850 hw2.0" name and replace only the first occurrence after
it, leaving qcc2072 untouched.  Idempotent: a re-run reports "already applied".
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "deps/linux/drivers/net/wireless/ath/ath12k/wifi7/hw.c"
s = path.read_text(encoding="utf-8")

name_marker = '"wcn7850 hw2.0"'
ni = s.find(name_marker)
if ni < 0:
    raise SystemExit('wcn7850 hw2.0 block not found')

target = "\t\t.vdev_start_delay = true,"
replacement = "\t\t.vdev_start_delay = false,"

ti_true = s.find(target, ni)
ti_false = s.find(replacement, ni)

# Already applied? first vdev_start_delay after the wcn7850 name is `false`
# (and any remaining `true` belongs to qcc2072 further down).
if ti_false != -1 and (ti_true == -1 or ti_false < ti_true):
    print("vdev-start-delay-off: already applied")
    raise SystemExit(0)

if ti_true < 0:
    raise SystemExit('vdev_start_delay = true not found inside wcn7850 block')

s = s[:ti_true] + replacement + s[ti_true + len(target):]

if s.count(target) != 1:
    raise SystemExit(f"expected exactly 1 remaining vdev_start_delay=true (qcc2072), got {s.count(target)}")

path.write_text(s, encoding="utf-8")
print("vdev-start-delay-off: patched wifi7/hw.c (wcn7850 vdev_start_delay -> false)")
