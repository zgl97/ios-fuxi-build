#!/usr/bin/env python3
"""Sample the endpoint the moment the first scan gives up.

The receive side stops delivering events as soon as the firmware takes the
scan request, so the useful evidence is the state at that instant: the boot
interface error words, the MHI state, and the copy-engine rings.  Applied
after nw-mode-timeout-diag.patch, whose log_tap hook this extends.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
wifi = root / "src/nw_wifi.c"
s = wifi.read_text(encoding="utf-8")

decl = "static int g_mode_stuck;\n"
if decl not in s:
    raise SystemExit("nw_wifi.c mode stuck declaration not found")

scan_fn = """\
/* The firmware stops answering once it takes the scan request: no event, no
 * copy-engine completion.  Read the endpoint state at the moment the wait
 * gives up so the reason is visible instead of inferred. */
static int g_scan_stuck;
static void scan_state(void) {
  char b[240];
  if (nw_board && nw_board->bar) {
    const volatile u8 *m = nw_board->bar; u32 bhi = readl(m + 0x28);
    if (bhi >= nw_board->bar_len) bhi = 0x100;
    lx_log(3, "scan: mhistatus %08x mhictrl %08x ee %08x bhi-status %08x err %08x dbg %08x %08x %08x",
           readl(m + 0x48), readl(m + 0x38), readl(m + bhi + 0x28), readl(m + bhi + 0x2c), readl(m + bhi + 0x30),
           readl(m + bhi + 0x34), readl(m + bhi + 0x38), readl(m + bhi + 0x3c)); }
  nw_qrtr_rx_stats(b, sizeof b); lx_log(3, "scan: %s", b);
  if (!g_hw) return;
  struct ath12k *ar = ath12k_ah_to_ar(g_hw->priv, 0); struct ath12k_base *ab = ar->ab;
  for (int p = 1; p <= 4; p++) ring_state(ab, p);
  lx_log(3, "scan: state %d wmi credits %d, mhi ee %d state %d", ar->scan.state,
         ab->htc.endpoint[ATH12K_HTC_EP_2].tx_credits,
         ath12k_pci_priv(ab)->mhi_ctrl->ee, ath12k_pci_priv(ab)->mhi_ctrl->dev_state); }

"""
s = s.replace(decl, decl + scan_fn, 1)

trigger_old = """  if (!g_mode_stuck && has_text(line, "qmi failed set mode request")) { g_mode_stuck = 1; mode_state(); }
"""
trigger_new = trigger_old + """  if (!g_scan_stuck && has_text(line, "failed to start hw scan")) { g_scan_stuck = 1; scan_state(); }
"""
if trigger_old not in s:
    raise SystemExit("nw_wifi.c mode trigger not found")
s = s.replace(trigger_old, trigger_new, 1)

wifi.write_text(s, encoding="utf-8")
print("nw-scan-stuck-diag: patched src/nw_wifi.c")
