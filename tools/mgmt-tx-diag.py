#!/usr/bin/env python3
"""Trace the 802.11 management transmit path (r568).

r567 finally brought up scan, vdev start and the STA peer, but the three
authentication frames never produced a WMI_MGMT_TX_COMPLETION event and the
AP never answered.  This patch follows one auth frame from mac80211 into the
driver:

  ath12k_wifi7_mac_op_tx() -> ath12k_mac_mgmt_tx() -> work
  -> ath12k_mac_mgmt_tx_wmi() -> ath12k_wmi_mgmt_send() -> CE3

and widens the CE3/CE4 transmit-completion sampling so the WMI ring drain is
visible on the serial console.
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
ath = root / "deps/linux/drivers/net/wireless/ath/ath12k"
wmi = ath / "wmi.c"
mac = ath / "mac.c"
hw7 = ath / "wifi7/hw.c"
ce = ath / "ce.c"


def replace(path, old, new, label):
    s = path.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"{label}: pattern not found in {path}")
    path.write_text(s.replace(old, new, 1), encoding="utf-8")


# --- wifi7/hw.c: the mgmt branch of mac op_tx --------------------------------
replace(
    hw7,
    """\t} else if (ieee80211_is_mgmt(hdr->frame_control)) {
\t\tif (sta && sta->mlo)
""",
    """\t} else if (ieee80211_is_mgmt(hdr->frame_control)) {
\t\tath12k_info(ar->ab, "NWDIAG op_tx mgmt vdev %u len %u fc 0x%04x flags 0x%x\\n",
\t\t\t    arvif->vdev_id, skb->len, hdr->frame_control, info_flags);
\t\tif (sta && sta->mlo)
""",
    "hw.c op_tx mgmt",
)

# --- mac.c: queue and wiphy work --------------------------------------------
replace(
    mac,
    """\tskb_queue_tail(q, skb);
\tatomic_inc(&ar->num_pending_mgmt_tx);
""",
    """\tskb_queue_tail(q, skb);
\tath12k_info(ar->ab, "NWDIAG mgmt_tx queued len %u qlen %u\\n",
\t\t    skb->len, skb_queue_len_lockless(q));
\tatomic_inc(&ar->num_pending_mgmt_tx);
""",
    "mac.c mgmt_tx queue",
)

replace(
    mac,
    """\t\tarvif = wiphy_dereference(ah->hw->wiphy, ahvif->link[skb_cb->link_id]);
\t\tif (ar->allocated_vdev_map & (1LL << arvif->vdev_id)) {
""",
    """\t\tarvif = wiphy_dereference(ah->hw->wiphy, ahvif->link[skb_cb->link_id]);
\t\tath12k_info(ar->ab, "NWDIAG mgmt_work skb vdev %u link %u len %u\\n",
\t\t\t    arvif->vdev_id, skb_cb->link_id, skb->len);
\t\tif (ar->allocated_vdev_map & (1LL << arvif->vdev_id)) {
""",
    "mac.c mgmt work",
)

replace(
    mac,
    """\t\t\tret = ath12k_mac_mgmt_tx_wmi(ar, arvif, skb);
\t\t\tif (ret) {
""",
    """\t\t\tret = ath12k_mac_mgmt_tx_wmi(ar, arvif, skb);
\t\t\tath12k_info(ar->ab, "NWDIAG mgmt_work tx_wmi ret %d\\n", ret);
\t\t\tif (ret) {
""",
    "mac.c mgmt work ret",
)

# --- wmi.c: build and submit the WMI command ---------------------------------
replace(
    wmi,
    """\tcmd->tx_params_valid = 0;

\tframe_tlv = (struct wmi_tlv *)(skb->data + sizeof(*cmd));
""",
    """\tcmd->tx_params_valid = 0;

\tath12k_info(ab, "NWDIAG wmi mgmt send vdev %u buf %u flen %u blen %u freq %u\\n",
\t\t    vdev_id, buf_id, frame->len, buf_len,
\t\t    le32_to_cpu(cmd->chanfreq));

\tframe_tlv = (struct wmi_tlv *)(skb->data + sizeof(*cmd));
""",
    "wmi.c mgmt send",
)

replace(
    wmi,
    """send:
\tret = ath12k_wmi_cmd_send(wmi, skb, WMI_MGMT_TX_SEND_CMDID);
""",
    """send:
\tret = ath12k_wmi_cmd_send(wmi, skb, WMI_MGMT_TX_SEND_CMDID);
\tath12k_info(ab, "NWDIAG wmi mgmt send ret %d\\n", ret);
""",
    "wmi.c mgmt send ret",
)

# --- wmi.c: completion event -------------------------------------------------
replace(
    wmi,
    """\tif (ath12k_pull_mgmt_tx_compl_param_tlv(ab, skb, &tx_compl_param) != 0) {
\t\tath12k_warn(ab, "failed to extract mgmt tx compl event");
\t\treturn;
\t}
""",
    """\tif (ath12k_pull_mgmt_tx_compl_param_tlv(ab, skb, &tx_compl_param) != 0) {
\t\tath12k_warn(ab, "failed to extract mgmt tx compl event");
\t\treturn;
\t}

\tath12k_info(ab, "NWDIAG mgmt tx compl desc %u status %u pdev %u\\n",
\t\t    le32_to_cpu(tx_compl_param.desc_id),
\t\t    le32_to_cpu(tx_compl_param.status),
\t\t    le32_to_cpu(tx_compl_param.pdev_id));
""",
    "wmi.c mgmt compl",
)

# --- wmi.c: WMI service bits that select the mgmt tx path --------------------
replace(
    wmi,
    """\t\t\tath12k_wmi_service_bitmap_copy(wmi_handle, ptr);

\t\t\tsvc_ready->wmi_svc_bitmap_done = true;
""",
    """\t\t\tath12k_wmi_service_bitmap_copy(wmi_handle, ptr);

\t\t\tath12k_info(ab, "NWDIAG svc mgmt_tx_htt %d mgmt_tx_wmi %d\\n",
\t\t\t\t    test_bit(WMI_TLV_SERVICE_MGMT_TX_HTT,
\t\t\t\t\t     wmi_handle->wmi_ab->svc_map),
\t\t\t\t    test_bit(WMI_TLV_SERVICE_MGMT_TX_WMI,
\t\t\t\t\t     wmi_handle->wmi_ab->svc_map));

\t\t\tsvc_ready->wmi_svc_bitmap_done = true;
""",
    "wmi.c svc bits",
)

# --- ce.c: widen the CE3 (WMI) and CE4 (HTT) tx-completion sampling ----------
replace(
    ce,
    """\t\tif (pipe->pipe_num < 16 && nw_ce_tx_count[pipe->pipe_num]++ < 12)
\t\t\tath12k_info(ab, "ce tx done pipe %u len %u\\n", pipe->pipe_num, skb->len);
""",
    """\t\tif (pipe->pipe_num < 16 &&
\t\t    nw_ce_tx_count[pipe->pipe_num]++ <
\t\t\t    (pipe->pipe_num == 3 || pipe->pipe_num == 4 ? 240 : 12))
\t\t\tath12k_info(ab, "ce tx done pipe %u len %u jiffies %lu\\n",
\t\t\t\t    pipe->pipe_num, skb->len, jiffies);
""",
    "ce.c tx cap",
)

print("mgmt-tx-diag: patched hw.c/mac.c/wmi.c/ce.c")
