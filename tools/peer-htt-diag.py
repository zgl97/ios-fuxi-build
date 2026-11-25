#!/usr/bin/env python3
"""r562 diagnostics (v2, low-overhead): locate the STA-connect 3 s stall.

The r560 log proves the 3 s block is ath12k_wait_for_peer_created() waiting
3*HZ for the HTT PEER_MAP event that never arrives.  Two things must be
separated:

  (A) the CE/HTC RX path (which carries BOTH WMI events and HTT t2h messages
      such as PEER_MAP) stops being serviced after the first scan; or
  (B) the RX path is alive but the firmware simply never sends PEER_MAP for the
      STA vdev, because the vdev was not started yet (vdev_start_delay=true
      defers ath12k_mac_vdev_start until after peer_create).

v1 of this script ALSO instrumented ce.c and pci.c on a per-message basis and,
critically, called ath12k_info() from the CE *hard-IRQ* handler.  That floods the
serial console and (dev_printk in hard-IRQ context) perturbs/hangs the RX and
control paths -- in two r561 runs wpa_supplicant either never got to authentic-
ate or hung in init.  v2 therefore leaves ce.c and pci.c completely untouched
(wmi-op-rx-log.py already keeps a <40-sample trace of CE activity, exactly as in
the known-good r560 kernel) and keeps only RARE event probes here:

  - peer.c   : peer-create WMI send ret + wait_dp_link_peer ret (a few per auth)
  - dp_peer.c: dp link peer map event (a few)
  - dp_htt.c : every HTT t2h PEER_MAP/UNMAP always; other types only first 20
               then 1-in-1000 as a coarse RX-liveness heartbeat
  - mac.c    : sta_add entry (is_created/is_started), vdev_start_restart entry,
               vdev_setup_sync ret (a few)

Every probe prints via ath12k_info (kernel log L6) with jiffies appended so the
whole trace orders against the 3 s sme-connect window, and the total line count
for a whole run is tiny (well under the r560 baseline), so timing is unchanged.
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


def insert_after_line_after(text, marker, needle, block, label):
    """Insert `block` after `needle`, but only for the occurrence at or after
    the first occurrence of `marker`.  Needed when `needle` (e.g. `int ret;`)
    is not unique in the file but is unambiguous inside the target function."""
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

# Every HTT t2h message: PEER_MAP*/UNMAP* always logged; other types only the
# first 20 then 1-in-1000, as a coarse (cheap) RX-liveness heartbeat.
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
    T + T + "else if (nw_t2h <= 20 || (nw_t2h % 1000) == 0)",
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

print("peer-htt-diag(v2): patched peer/dp_peer/dp_htt/mac (ce.c/pci.c untouched)")
