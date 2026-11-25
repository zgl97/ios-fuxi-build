#!/usr/bin/env python3
"""r561 diagnostics: locate the STA-connect 3 s stall.

The r560 log proves the 3 s block is ath12k_wait_for_peer_created() waiting
3*HZ for the HTT PEER_MAP event that never arrives.  Two things must be
separated:

  (A) the CE/HTC RX path (which carries BOTH WMI events and HTT t2h messages
      such as PEER_MAP) stops being serviced after the first scan -- i.e. the
      host stops pulling completions, or the firmware stops raising the CE
      interrupt; or
  (B) the RX path is alive but the firmware simply never sends PEER_MAP for the
      STA vdev, because the vdev was not started yet (vdev_start_delay=true
      defers ath12k_mac_vdev_start until after peer_create).

Every probe below prints via ath12k_info (kernel log L6) and is rate-limited so
the log stays readable.  jiffies is appended to every line so the whole trace
can be ordered against the 3 s sme-connect window.
"""
import sys
import pathlib

root = pathlib.Path(sys.argv[1])
A = root / "deps/linux/drivers/net/wireless/ath/ath12k"

T = "\t"
NL = "\\n"   # literal backslash-n for the C string being generated


def read(p):
    return p.read_text(encoding="utf-8")


def write(p, s):
    p.write_text(s, encoding="utf-8")


def _find_line(lines, needle, label):
    for k, l in enumerate(lines):
        if needle in l:
            return k
    raise SystemExit("%s: anchor line not found: %r" % (label, needle))


def insert_after_line(text, needle, block, label):
    lines = text.split("\n")
    k = _find_line(lines, needle, label)
    lines[k + 1:k + 1] = block
    return "\n".join(lines)


def replace_line_span(text, needle, nlines, block, label):
    lines = text.split("\n")
    k = _find_line(lines, needle, label)
    lines[k:k + nlines] = block
    return "\n".join(lines)


def insert_after_line_after(text, marker, needle, block, label):
    """Like insert_after_line but only searches for `needle` at or after the
    first occurrence of `marker`.  Needed when `needle` (e.g. `int ret;` or a
    local decl) is not unique in the file but is unambiguous once we first jump
    to the target function's signature."""
    mi = text.find(marker)
    if mi < 0:
        raise SystemExit("%s: marker not found: %r" % (label, marker))
    ni = text.find(needle, mi)
    if ni < 0:
        raise SystemExit("%s: needle not found after marker: %r" % (label, needle))
    eol = text.find("\n", ni)
    if eol < 0:
        raise SystemExit("%s: no newline after needle" % label)
    return text[:eol + 1] + "\n".join(block) + "\n" + text[eol + 1:]


def insert_after_function_brace(text, sig_needle, block, label):
    """Insert `block` just inside the body of the function whose signature
    contains `sig_needle` (right after its opening brace line).  Only safe when
    the inserted statements reference nothing but function parameters."""
    i = text.find(sig_needle)
    if i < 0:
        raise SystemExit("%s: signature not found: %r" % (label, sig_needle))
    b = text.find("{", i)
    if b < 0:
        raise SystemExit("%s: opening brace not found" % label)
    eol = text.find("\n", b)
    if eol < 0:
        raise SystemExit("%s: no newline after brace" % label)
    return text[:eol + 1] + "\n".join(block) + "\n" + text[eol + 1:]


# ---------------------------------------------------------------- ce.c
ce = A / "ce.c"
s = read(ce)

# CE service entry: uncap (was <40) and add jiffies so we can see whether the
# CE pipes keep being serviced after the first scan.
s = replace_line_span(s, "nw_ce_service_count[ce_id]++ < 40", 2, [
    T + "if (ce_id < 16) {",
    T + T + "unsigned int nw_c = nw_ce_service_count[ce_id]++;",
    T + T + "if (nw_c <= 80 || (nw_c % 200) == 0)",
    T + T + T + 'ath12k_info(ab, "ce service %u cnt %u jiffies %lu' + NL + '", ce_id, nw_c, jiffies);',
    T + "}",
], "ce service")

# CE rx delivery: uncap (was <40) and add jiffies.
s = replace_line_span(s, "nw_ce_rx_count[pipe->pipe_num]++ < 40", 2, [
    T + T + "if (pipe->pipe_num < 16) {",
    T + T + T + "unsigned int nw_c = nw_ce_rx_count[pipe->pipe_num]++;",
    T + T + T + "if (nw_c <= 120 || (nw_c % 200) == 0)",
    T + T + T + T + 'ath12k_info(ab, "ce rx pipe %u len %u cnt %u jiffies %lu' + NL + '", pipe->pipe_num, nbytes, nw_c, jiffies);',
    T + T + "}",
], "ce rx")

# RX replenish retry: fires only when posting new rx buffers fails (-ENOMEM
# etc).  If this keeps firing, the RX ring is starving.
s = insert_after_line(s, "timer_container_of(ab, t, rx_replenish_retry)", [
    T + '{ static unsigned int nw_rr; if (nw_rr++ < 200) ath12k_info(ab, "NWDIAG ce replenish retry cnt %u jiffies %lu' + NL + '", nw_rr, jiffies); }',
], "ce replenish retry")

write(ce, s)

# ---------------------------------------------------------------- pci.c
pci = A / "pci.c"
s = read(pci)

# CE workqueue: entry point (did the queued work run?) ...
s = insert_after_line(s, "static void ath12k_pci_ce_workqueue(struct work_struct *work)", [], "ce wq marker")  # no-op guard
s = insert_after_line(s, "int irq_idx = ATH12K_PCI_IRQ_CE0_OFFSET + ce_pipe->pipe_num;", [
    T + '{ static unsigned int nw_wq; if (nw_wq++ < 200) ath12k_info(ce_pipe->ab, "NWDIAG ce wq enter pipe %d jiffies %lu' + NL + '", ce_pipe->pipe_num, jiffies); }',
], "ce wq enter")
# ... and exit (did per_engine_service return, i.e. not hang?).
s = insert_after_line(s, "ath12k_ce_per_engine_service(ce_pipe->ab, ce_pipe->pipe_num);", [
    T + '{ static unsigned int nw_wqd; if (nw_wqd++ < 200) ath12k_info(ce_pipe->ab, "NWDIAG ce wq done pipe %d jiffies %lu' + NL + '", ce_pipe->pipe_num, jiffies); }',
], "ce wq done")

# CE hard-IRQ handler: does the firmware keep raising the CE interrupt?
s = replace_line_span(s, "if (!test_bit(ATH12K_FLAG_CE_IRQ_ENABLED, &ab->dev_flags))", 2, [
    T + "if (!test_bit(ATH12K_FLAG_CE_IRQ_ENABLED, &ab->dev_flags))",
    T + T + "return IRQ_HANDLED;",
    T + '{ static unsigned int nw_irq; if (nw_irq++ < 400) ath12k_info(ab, "NWDIAG ce irq pipe %d jiffies %lu' + NL + '", ce_pipe->pipe_num, jiffies); }',
], "ce irq")

write(pci, s)

# ---------------------------------------------------------------- peer.c
peer = A / "peer.c"
s = read(peer)

# Did the peer-create WMI command get sent, and what did it return?
s = insert_after_line(s, "ret = ath12k_wmi_send_peer_create_cmd(ar, arg);", [
    T + 'ath12k_info(ar->ab, "NWDIAG peer create send vdev %u ret %d jiffies %lu' + NL + '", arg->vdev_id, ret, jiffies);',
], "peer create send")

# Core wait: ret<=0 means the 3*HZ timeout fired.  Covers both map (create) and
# unmap (delete) waits.
s = insert_after_line(s, "}), 3 * HZ);", [
    T + 'ath12k_info(ab, "NWDIAG wait_dp_link_peer vdev %d expect %d ret %d jiffies %lu' + NL + '", vdev_id, expect_mapped, ret, jiffies);',
], "wait_dp_link_peer")

write(peer, s)

# ---------------------------------------------------------------- dp_peer.c
dppeer = A / "dp_peer.c"
s = read(dppeer)

s = insert_after_function_brace(s,
    "void ath12k_dp_link_peer_map_event(struct ath12k_base *ab, u8 vdev_id, u16 peer_id,",
    [
        T + 'ath12k_info(ab, "NWDIAG dppeer map vdev %u peer %u addr %pM ast %u jiffies %lu' + NL + '", vdev_id, peer_id, mac_addr, ast_hash, jiffies);',
    ], "dppeer map event")

write(dppeer, s)

# ---------------------------------------------------------------- dp_htt.c
dphtt = A / "dp_htt.c"
s = read(dphtt)

# Every HTT t2h message: PEER_MAP* always logged; other types rate-limited.
s = insert_after_line(s, "type = le32_get_bits(resp->version_msg.version, HTT_T2H_MSG_TYPE);", [
    T + "{",
    T + T + "static unsigned int nw_t2h;",
    T + T + "nw_t2h++;",
    T + T + "if (type == HTT_T2H_MSG_TYPE_PEER_MAP ||",
    T + T + "    type == HTT_T2H_MSG_TYPE_PEER_MAP2 ||",
    T + T + "    type == HTT_T2H_MSG_TYPE_PEER_MAP3 ||",
    T + T + "    type == HTT_T2H_MSG_TYPE_PEER_UNMAP ||",
    T + T + "    type == HTT_T2H_MSG_TYPE_PEER_UNMAP2)",
    T + T + T + 'ath12k_info(ab, "NWDIAG htt t2h PEER type 0x%x len %d total %u jiffies %lu' + NL + '", type, skb->len, nw_t2h, jiffies);',
    T + T + "else if (nw_t2h <= 500 || (nw_t2h % 200) == 0)",
    T + T + T + 'ath12k_info(ab, "NWDIAG htt t2h type 0x%x total %u jiffies %lu' + NL + '", type, nw_t2h, jiffies);',
    T + "}",
], "htt t2h")

write(dphtt, s)

# ---------------------------------------------------------------- mac.c
mac = A / "mac.c"
s = read(mac)

# station_add entry: is_created / is_started tell us whether vdev_start_delay
# left the STA vdev un-started when peer_create runs.
s = insert_after_line(s, "struct ath12k_wmi_peer_create_arg peer_param = {};", [
    T + 'ath12k_info(ab, "NWDIAG sta_add vdev %u created %d started %d addr %pM jiffies %lu' + NL + '", arvif->vdev_id, arvif->is_created, arvif->is_started, arsta->addr, jiffies);',
], "sta_add")

# vdev_start_restart (NB: NOT ath12k_mac_monitor_vdev_start, which also has a
# `struct wmi_vdev_start_req_arg arg = {};` local).  Anchor on the function
# signature first so we land inside the right body.
VR_SIG = "ath12k_mac_vdev_start_restart(struct ath12k_link_vif *arvif,"
# entry: after the last local declaration (`int ret;`) inside this function.
s = insert_after_line_after(s, VR_SIG, "int ret;", [
    T + 'ath12k_info(ab, "NWDIAG vdev_start_restart vdev %u restart %d jiffies %lu' + NL + '", arvif->vdev_id, restart, jiffies);',
], "vdev_start_restart entry")

# vdev_start completion: ret of ath12k_mac_vdev_setup_sync (waits for
# vdev_setup_done from firmware).
s = insert_after_line_after(s, VR_SIG, "ret = ath12k_mac_vdev_setup_sync(ar);", [
    T + 'ath12k_info(ab, "NWDIAG vdev_start setup_sync vdev %u ret %d jiffies %lu' + NL + '", arvif->vdev_id, ret, jiffies);',
], "vdev_start setup_sync")

write(mac, s)

print("peer-htt-diag: patched ce/pci/peer/dp_peer/dp_htt/mac")
