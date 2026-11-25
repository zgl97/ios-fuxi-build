#!/usr/bin/env python3
"""Use the real 13-channel 2.4 GHz passive scan in the sheng driver.

The old reuse path only fakes EVENT_SCAN_RESULTS from the board scan cache.
Now that the boot scan is gone, wpa_supplicant's scan must be issued for real
so vdev 0 belongs to it.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
path = root / "userspace/driver_sheng.c"
s = path.read_text(encoding="utf-8")

# 1. The reuse helper is no longer used once sheng_scan calls the real path.
old = """static void sheng_scan_reuse_results(void *eloop_ctx, void *timeout_ctx)
"""
new = """static void __attribute__((unused)) sheng_scan_reuse_results(void *eloop_ctx, void *timeout_ctx)
"""
if old not in s:
    raise SystemExit("sheng_scan_reuse_results not found")
s = s.replace(old, new, 1)

# 2. Forward declaration: the real implementation now follows sheng_scan.
old = """static int sheng_scan(void *priv, struct wpa_driver_scan_params *parameters)
{
"""
new = """static int sheng_scan_real(void *priv, struct wpa_driver_scan_params *parameters);
static int sheng_scan(void *priv, struct wpa_driver_scan_params *parameters)
{
"""
if old not in s:
    raise SystemExit("sheng_scan signature not found")
s = s.replace(old, new, 1)

# 3. Point sheng_scan at the real implementation instead of the cached results.
old = """  /* The board path already completed a 13-channel 2.4 GHz passive scan and
   * cached its BSS list before wpa_supplicant starts.  A second cfg80211 scan
   * gets a new vdev id (vdev 0 is consumed by the board scan), and this
   * firmware only acknowledges SCAN_STARTED on vdev 0, so reuse the cached
   * results instead of creating a second scan vdev. */
  wpa_printf(MSG_INFO, "sheng: scan reuse board results nssids=%zu", parameters->num_ssids);
  if (eloop_register_timeout(0, 100000, sheng_scan_reuse_results, driver, NULL))
    return -1;
  return 0;
}
#if 0
static int sheng_scan_old(void *priv, struct wpa_driver_scan_params *parameters)
{
"""
new = """  return sheng_scan_real(priv, parameters);
}
static int sheng_scan_real(void *priv, struct wpa_driver_scan_params *parameters)
{
"""
if old not in s:
    raise SystemExit("sheng_scan reuse block not found")
s = s.replace(old, new, 1)

# 3. Close the former #if 0 block around the real implementation.
old = """  return sheng_rpc(driver, &b, &reply);
}
#endif
static struct wpa_scan_results *sheng_scan_results(void *priv)
"""
new = """  return sheng_rpc(driver, &b, &reply);
}
static struct wpa_scan_results *sheng_scan_results(void *priv)
"""
if old not in s:
    raise SystemExit("sheng_scan_old tail not found")
s = s.replace(old, new, 1)

# 4. Make the wrapper a one-liner so no locals are left unused (-Werror).
old = """static int sheng_scan_real(void *priv, struct wpa_driver_scan_params *parameters);
static int sheng_scan(void *priv, struct wpa_driver_scan_params *parameters)
{
  struct sheng_driver *driver = priv;
  u8 bytes[8192], ssids[WPAS_MAX_SCAN_SSIDS * 33], frequencies[256 * 4];
  struct nw_control_builder b; struct nw_control_view reply;
  size_t count = 0;
  if (parameters->num_ssids > WPAS_MAX_SCAN_SSIDS ||
      parameters->num_ssids > (size_t)driver->capability.max_scan_ssids ||
      parameters->mac_addr_rand || parameters->p2p_probe) return -1;
  return sheng_scan_real(priv, parameters);
}
"""
new = """static int sheng_scan_real(void *priv, struct wpa_driver_scan_params *parameters);
static int sheng_scan(void *priv, struct wpa_driver_scan_params *parameters)
{
  return sheng_scan_real(priv, parameters);
}
"""
if old not in s:
    raise SystemExit("sheng_scan wrapper not found")
s = s.replace(old, new, 1)

path.write_text(s, encoding="utf-8")
print("sheng-scan-real: patched userspace/driver_sheng.c")
