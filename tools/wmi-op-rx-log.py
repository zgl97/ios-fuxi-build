#!/usr/bin/env python3
"""Add temporary WMI/scan/CE tracing to the staged ath12k sources."""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
wmi = root / "deps/linux/drivers/net/wireless/ath/ath12k/wmi.c"
mac = root / "deps/linux/drivers/net/wireless/ath/ath12k/mac.c"
ce = root / "deps/linux/drivers/net/wireless/ath/ath12k/ce.c"
htc = root / "deps/linux/drivers/net/wireless/ath/ath12k/htc.c"
ctl = root / "src/nw_cfg80211_control.c"


def replace(path, old, new, label):
    s = path.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"{label}: pattern not found in {path}")
    path.write_text(s.replace(old, new, 1), encoding="utf-8")


s = wmi.read_text(encoding="utf-8")
old = """\tcmd_hdr = (struct wmi_cmd_hdr *)skb->data;
\tid = le32_get_bits(cmd_hdr->cmd_id, WMI_CMD_HDR_CMD_ID);

\tif (!skb_pull(skb, sizeof(struct wmi_cmd_hdr)))
"""
new = """\tcmd_hdr = (struct wmi_cmd_hdr *)skb->data;
\tid = le32_get_bits(cmd_hdr->cmd_id, WMI_CMD_HDR_CMD_ID);
\t{
\t\tstatic int nw_wmi_rx_log;
\t\tif (nw_wmi_rx_log < 400) {
\t\t\tnw_wmi_rx_log++;
\t\t\tath12k_info(ab, "wmi op rx cmd 0x%x len %d\\n", id, skb->len);
\t\t}
\t\tif (id == 0x1d011) {
\t\t\tstatic int nw_diag_dump;
\t\t\tif (nw_diag_dump++ < 4 && skb->len >= sizeof(*cmd_hdr) + 32) {
\t\t\t\tu32 d[8];
\t\t\t\tmemcpy(d, skb->data + sizeof(*cmd_hdr), sizeof(d));
\t\t\t\tath12k_info(ab, "diag %08x %08x %08x %08x %08x %08x %08x %08x\\n",
\t\t\t\t\t    d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7]);
\t\t\t}
\t\t}
\t}

\tif (!skb_pull(skb, sizeof(struct wmi_cmd_hdr)))
"""
if old not in s:
    raise SystemExit("wmi.c op_rx pattern not found")
wmi.write_text(s.replace(old, new, 1), encoding="utf-8")

# Scan channel list: show how many channels are actually submitted.
replace(wmi, """\t\tnum_send_chans = min3(arg->nallchans, max_chan_limit,
\t\t\t\t      ATH12K_WMI_MAX_NUM_CHAN_PER_CMD);
""", """\t\tnum_send_chans = min3(arg->nallchans, max_chan_limit,
\t\t\t\t      ATH12K_WMI_MAX_NUM_CHAN_PER_CMD);
\t\tath12k_info(ar->ab, "scan chan list all %u max %u send %u\\n",
\t\t\t    arg->nallchans, max_chan_limit, num_send_chans);
""", "wmi.c chan list")

# Scan start: show the actual command size and the firmware-advertised limit.
replace(wmi, """\tret = ath12k_wmi_cmd_send(wmi, skb,
\t\t\t\t  WMI_START_SCAN_CMDID);
""", """\tath12k_info(ar->ab, "scan start cmd len %u max %u nchan %u vdev %u passive %u\\n",
\t\t    skb->len, wmi->wmi_ab->max_msg_len[ar->pdev_idx],
\t\t    arg->num_chan, arg->vdev_id, arg->scan_f_passive);
\tret = ath12k_wmi_cmd_send(wmi, skb,
\t\t\t\t  WMI_START_SCAN_CMDID);
""", "wmi.c scan start")

s = mac.read_text(encoding="utf-8")
old = """\tret = ath12k_wmi_send_scan_start_cmd(ar, arg);
\tif (ret)
\t\treturn ret;

\tret = wait_for_completion_timeout(&ar->scan.started, 1 * HZ);
\tif (ret == 0) {
"""
new = """\tret = ath12k_wmi_send_scan_start_cmd(ar, arg);
\tath12k_info(ar->ab, "scan start send %d\\n", ret);
\tif (ret)
\t\treturn ret;

\tret = wait_for_completion_timeout(&ar->scan.started, 1 * HZ);
\tath12k_info(ar->ab, "scan start wait %d state %d\\n", ret, ar->scan.state);
\tif (ret == 0) {
"""
if old not in s:
    raise SystemExit("mac.c start_scan pattern not found")
mac.write_text(s.replace(old, new, 1), encoding="utf-8")

replace(mac, """\tret = ath12k_wmi_vdev_create(ar, arvif->bssid, &vdev_arg);
\tif (ret) {
""", """\tret = ath12k_wmi_vdev_create(ar, arvif->bssid, &vdev_arg);
\tath12k_info(ab, "scan vdev create id %u type %u ret %d\\n",
\t\t    arvif->vdev_id, ahvif->vdev_type, ret);
\tif (ret) {
""", "mac.c vdev create")

replace(mac, """\tath12k_wmi_start_scan_init(ar, arg);
\targ->vdev_id = arvif->vdev_id;
\targ->scan_id = ATH12K_SCAN_ID;
""", """\tath12k_wmi_start_scan_init(ar, arg);
\targ->vdev_id = arvif->vdev_id;
\targ->scan_id = ATH12K_SCAN_ID;
\tath12k_info(ar->ab, "scan arg link %u vdev %u created %d started %d nchan %d passive %d\\n",
\t\t    link_id, arvif->vdev_id, arvif->is_created, arvif->is_started,
\t\t    n_channels, req->n_ssids ? 0 : 1);
""", "mac.c scan arg")

# CE service/RX/TX counters.
s = ce.read_text(encoding="utf-8")
marker = '#include "hif.h"\n'
if marker not in s:
    raise SystemExit("ce.c include marker not found")
s = s.replace(marker, marker + """
static unsigned int nw_ce_service_count[16];
static unsigned int nw_ce_rx_count[16];
static unsigned int nw_ce_tx_count[16];
""", 1)
ce.write_text(s, encoding="utf-8")

replace(ce, """void ath12k_ce_per_engine_service(struct ath12k_base *ab, u16 ce_id)
{
\tstruct ath12k_ce_pipe *pipe = &ab->ce.ce_pipe[ce_id];

""", """void ath12k_ce_per_engine_service(struct ath12k_base *ab, u16 ce_id)
{
\tstruct ath12k_ce_pipe *pipe = &ab->ce.ce_pipe[ce_id];

\tif (ce_id < 16 && nw_ce_service_count[ce_id]++ < 40)
\t\tath12k_info(ab, "ce service %u\\n", ce_id);
""", "ce.c service")

replace(ce, """\t\tskb_put(skb, nbytes);
\t\t__skb_queue_tail(&list, skb);
""", """\t\tskb_put(skb, nbytes);
\t\tif (pipe->pipe_num < 16 && nw_ce_rx_count[pipe->pipe_num]++ < 40)
\t\t\tath12k_info(ab, "ce rx pipe %u len %u\\n", pipe->pipe_num, nbytes);
\t\t__skb_queue_tail(&list, skb);
""", "ce.c rx")

replace(ce, """\t\tif (!skb)
\t\t\tcontinue;

\t\tdma_unmap_single(ab->dev, ATH12K_SKB_CB(skb)->paddr, skb->len,
""", """\t\tif (!skb)
\t\t\tcontinue;
\t\tif (pipe->pipe_num < 16 && nw_ce_tx_count[pipe->pipe_num]++ < 40)
\t\t\tath12k_info(ab, "ce tx done pipe %u len %u\\n", pipe->pipe_num, skb->len);

\t\tdma_unmap_single(ab->dev, ATH12K_SKB_CB(skb)->paddr, skb->len,
""", "ce.c tx")

# HTC connect: record the firmware-advertised maximum message size.
replace(htc, """\tath12k_info(ab, "htc: service %u tx_alloc %u eid %u\\n",
\t\t    conn_req->service_id, tx_alloc, assigned_eid);
""", """\tath12k_info(ab, "htc: service %u tx_alloc %u eid %u max %u\\n",
\t\t    conn_req->service_id, tx_alloc, assigned_eid, conn_resp->max_msg_len);
""", "htc.c connect")

# Temporary: make the first automatic scan use one channel, to separate
# message-size / channel-count problems from a dead scan engine.
s = ctl.read_text(encoding="utf-8")
marker = "static int scan(struct cfg80211_registered_device *rdev, const struct nw_control_view *view) {\n"
if marker not in s:
    raise SystemExit("nw_cfg80211_control.c scan marker not found")
s = s.replace(marker, "static int nw_scan_one;\n" + marker, 1)
old_ssid = """  const u8 *ies = nw_control_get(view, NW_CONTROL_ATTR_IE, &ie_bytes);
"""
new_ssid = """  const u8 *ies = nw_control_get(view, NW_CONTROL_ATTR_IE, &ie_bytes);
  static const u8 nw_probe_ssid[33] = { 4, 'f', 'u', 'x', 'i' };
  if (nw_scan_one) { ssids = nw_probe_ssid; ssid_bytes = sizeof(nw_probe_ssid); }
"""
if old_ssid not in s:
    raise SystemExit("nw_cfg80211_control.c ssid marker not found")
s = s.replace(old_ssid, new_ssid, 1)
old = """  if (!request->req.n_channels) { kfree(request); return -EINVAL; }
"""
new = """  if (!request->req.n_channels) { kfree(request); return -EINVAL; }
"""
if old not in s:
    raise SystemExit("nw_cfg80211_control.c channel check not found")
s = s.replace(old, new, 1)
old_band = """  for (unsigned band = 0; band < NUM_NL80211_BANDS; band++) {
    struct ieee80211_supported_band *supported = wiphy->bands[band];
"""
new_band = """  for (unsigned band = 0; band < NUM_NL80211_BANDS; band++) {
    if (nw_scan_one && band != NL80211_BAND_2GHZ) continue;
    struct ieee80211_supported_band *supported = wiphy->bands[band];
"""
if old_band not in s:
    raise SystemExit("nw_cfg80211_control.c band loop not found")
s = s.replace(old_band, new_band, 1)
old2 = """  struct nw_control_view view;
  memset(&view, 0, sizeof(view));
  return scan(rdev, &view);
"""
new2 = """  struct nw_control_view view;
  memset(&view, 0, sizeof(view));
  rtnl_lock();
  if (!netif_running(primary)) {
    int opened = dev_open(primary, NULL);
    if (opened) { rtnl_unlock(); return opened; }
  }
  wiphy_lock(&rdev->wiphy);
  nw_scan_one = 1;
  int result = scan(rdev, &view);
  nw_scan_one = 0;
  wiphy_unlock(&rdev->wiphy);
  rtnl_unlock();
  return result;
"""
if old2 not in s:
    raise SystemExit("nw_cfg80211_control.c scan_all return not found")
s = s.replace(old2, new2, 1)
ctl.write_text(s, encoding="utf-8")

print("wmi-op-rx-log: patched wmi/mac/ce/htc/cfg80211")
