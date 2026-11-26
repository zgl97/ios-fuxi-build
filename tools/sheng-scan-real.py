#!/usr/bin/env python3
"""Force the wpa scan to the board's 13-channel 2.4 GHz passive set.

The pinned sheng_scan() passes wpa_supplicant's own SSIDs/frequencies to
NW_CONTROL_SCAN.  A one-channel hint (or the all-band list) leaves this
firmware's scan in STARTING until it times out, so always submit the same
13-channel 2.4 GHz passive set the board path uses.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "userspace/driver_sheng.c"
s = path.read_text(encoding="utf-8")

old = """  os_memset(ssids, 0, sizeof(ssids));
  for (size_t i = 0; i < parameters->num_ssids; i++) {
    size_t length = parameters->ssids[i].ssid_len;
    if (length > SSID_MAX_LEN) return -1;
    ssids[i * 33] = length;
    if (length) os_memcpy(ssids + i * 33 + 1, parameters->ssids[i].ssid, length);
  }
  if (parameters->freqs) {
    while (parameters->freqs[count]) {
      if (count == 256 || parameters->freqs[count] <= 0) return -1;
      WPA_PUT_LE32(frequencies + count * 4, parameters->freqs[count]); count++;
    }
  }
  if (!count && driver->join_frequency) {
    WPA_PUT_LE32(frequencies, driver->join_frequency); count = 1;
  }
  // The hint belongs to this first scan only; retries use upstream's search.
  driver->join_frequency = 0;
"""
new = """  os_memset(ssids, 0, sizeof(ssids));
  /* Wildcard SSID (zero length) instead of an empty list: cfg80211 then asks
   * for an active scan, so every channel gets probe requests.  A passive
   * 1.5 s walk completes on this firmware but catches almost no beacons
   * (r581: a real WMI_SCAN_EVENT_COMPLETED and still zero BSS). */
  size_t ssid_count = 1;
  /* Always submit the board's 13-channel 2.4 GHz set: a one-channel hint (or
   * the all-band list) leaves the scan in STARTING until it times out. */
  (void)parameters->freqs;
  driver->join_frequency = 0;
  {
    static const int default_freqs[] = {
      2412, 2417, 2422, 2427, 2432, 2437, 2442,
      2447, 2452, 2457, 2462, 2467, 2472,
    };
    for (size_t i = 0; i < sizeof(default_freqs) / sizeof(default_freqs[0]); i++) {
      WPA_PUT_LE32(frequencies + count * 4, default_freqs[i]);
      count++;
    }
  }
"""
if old not in s:
    raise SystemExit("sheng_scan parameter block not found")
s = s.replace(old, new, 1)

old = "  if (nw_control_put(&b, NW_CONTROL_ATTR_SCAN_SSIDS, ssids, parameters->num_ssids * 33) ||"
new = "  if (nw_control_put(&b, NW_CONTROL_ATTR_SCAN_SSIDS, ssids, ssid_count * 33) ||"
if old not in s:
    raise SystemExit("SCAN_SSIDS put not found")
s = s.replace(old, new, 1)

path.write_text(s, encoding="utf-8")
print("sheng-scan-real: patched userspace/driver_sheng.c")
