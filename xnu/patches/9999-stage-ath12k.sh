#!/bin/bash
# Stage the Ath12kWifi Linux Wi-Fi stack into bsd/fuxi_ath12k for the FUXI kernel.
# dxb runs every patches/*.sh before it applies any patches/*.patch, so this
# script only puts files on disk; 0117-fuxi-ath12k.patch declares them to config.
set -euo pipefail

[[ "${MACHINE_CONFIG:-}" == FUXI ]] || exit 0
: "${FUXI_ATH12K_TREE:?FUXI_ATH12K_TREE must name a staged Ath12kWifi tree}"
tree=$FUXI_ATH12K_TREE
[[ -f "$tree/tools/sources.txt" ]] || { echo "no tools/sources.txt under $tree" >&2; exit 1; }
[[ -f "$tree/out/ath12k-src/core.c" ]] || { echo "$tree is not prepared (run tools/prepare.sh)" >&2; exit 1; }
[[ -f bsd/kern/bsd_init.c ]] || { echo "not an XNU source root: $PWD" >&2; exit 1; }

# The sources are not in git. tools/ci/build.sh stages the pinned ath12k commit
# and the pinned Linux tarball into $tree with the same tools the compile-only
# lane uses, so both lanes compile byte-identical sources.
python3 - "$tree" <<'PY'
from pathlib import Path
import shutil
import sys

tree = Path(sys.argv[1])
dest = Path('bsd/fuxi_ath12k')
if dest.exists():
    shutil.rmtree(dest)

ENTRIES = [
    ('src/nw_crypto_mac80211.c', 'src/fxa_nw_crypto_mac80211.c'),
    ('src/nw_control_wire.c', 'src/fxa_nw_control_wire.c'),
    ('src/nw_control.c', 'src/fxa_nw_control.c'),
    ('src/nw_cfg80211_os.c', 'src/fxa_nw_cfg80211_os.c'),
    ('src/nw_cfg80211_native.c', 'src/fxa_nw_cfg80211_native.c'),
    ('src/nw_cfg80211_control.c', 'src/fxa_nw_cfg80211_control.c'),
    ('src/nw_cfg80211_encoding.c', 'src/fxa_nw_cfg80211_encoding.c'),
    ('src/nw_crc32.c', 'src/fxa_nw_crc32.c'),
    ('src/nw_debug.c', 'src/fxa_nw_debug.c'),
    ('src/nw_device.c', 'src/fxa_nw_device.c'),
    ('src/nw_drop_reasons.c', 'src/fxa_nw_drop_reasons.c'),
    ('src/nw_events.c', 'src/fxa_nw_events.c'),
    ('src/nw_mainline.c', 'src/fxa_nw_mainline.c'),
    ('src/nw_fils.c', 'src/fxa_nw_fils.c'),
    ('src/nw_mhi.c', 'src/fxa_nw_mhi.c'),
    ('src/nw_net.c', 'src/fxa_nw_net.c'),
    ('src/nw_netdev.c', 'src/fxa_nw_netdev.c'),
    ('src/nw_pci.c', 'src/fxa_nw_pci.c'),
    ('src/nw_qrtr.c', 'src/fxa_nw_qrtr.c'),
    ('src/nw_qrtr_mhi.c', 'src/fxa_nw_qrtr_mhi.c'),
    ('src/nw_rfkill.c', 'src/fxa_nw_rfkill.c'),
    ('src/nw_wifi.c', 'src/fxa_nw_wifi.c'),
    ('shim/src/lx_rt.c', 'shim/src/fxa_lx_rt.c'),
    ('deps/linux/lib/rbtree.c', 'deps/linux/lib/fxa_rbtree.c'),
    ('deps/linux/lib/crypto/arc4.c', 'deps/linux/lib/crypto/fxa_arc4.c'),
    ('generated:cfg80211-native-protocol.c', 'out/fxa_cfg80211-native-protocol.c'),
    ('generated:cfg80211-native-events.c', 'out/fxa_cfg80211-native-events.c'),
    ('crypto:aes-internal.c', 'out/crypto/fxa_aes-internal.c'),
    ('crypto:aes-internal-enc.c', 'out/crypto/fxa_aes-internal-enc.c'),
    ('crypto:aes-ccm.c', 'out/crypto/fxa_aes-ccm.c'),
    ('crypto:aes-gcm.c', 'out/crypto/fxa_aes-gcm.c'),
    ('crypto:aes-omac1.c', 'out/crypto/fxa_aes-omac1.c'),
    ('crypto:aes-ctr.c', 'out/crypto/fxa_aes-ctr.c'),
    ('crypto:aes-siv.c', 'out/crypto/fxa_aes-siv.c'),
    ('mac80211:main.c', 'out/mac80211/fxa_mac80211_main.c'),
    ('mac80211:driver-ops.c', 'out/mac80211/fxa_driver-ops.c'),
    ('mac80211:sta_info.c', 'out/mac80211/fxa_sta_info.c'),
    ('mac80211:scan.c', 'out/mac80211/fxa_mac80211_scan.c'),
    ('mac80211:offchannel.c', 'out/mac80211/fxa_offchannel.c'),
    ('mac80211:ht.c', 'out/mac80211/fxa_ht.c'),
    ('mac80211:agg-tx.c', 'out/mac80211/fxa_agg-tx.c'),
    ('mac80211:agg-rx.c', 'out/mac80211/fxa_agg-rx.c'),
    ('mac80211:vht.c', 'out/mac80211/fxa_vht.c'),
    ('mac80211:he.c', 'out/mac80211/fxa_he.c'),
    ('mac80211:eht.c', 'out/mac80211/fxa_eht.c'),
    ('mac80211:iface.c', 'out/mac80211/fxa_iface.c'),
    ('mac80211:link.c', 'out/mac80211/fxa_link.c'),
    ('mac80211:rate.c', 'out/mac80211/fxa_rate.c'),
    ('mac80211:cfg.c', 'out/mac80211/fxa_cfg.c'),
    ('mac80211:rx.c', 'out/mac80211/fxa_rx.c'),
    ('mac80211:tx.c', 'out/mac80211/fxa_tx.c'),
    ('mac80211:key.c', 'out/mac80211/fxa_key.c'),
    ('mac80211:util.c', 'out/mac80211/fxa_mac80211_util.c'),
    ('mac80211:parse.c', 'out/mac80211/fxa_parse.c'),
    ('mac80211:wme.c', 'out/mac80211/fxa_wme.c'),
    ('mac80211:chan.c', 'out/mac80211/fxa_mac80211_chan.c'),
    ('mac80211:mlme.c', 'out/mac80211/fxa_mac80211_mlme.c'),
    ('mac80211:status.c', 'out/mac80211/fxa_status.c'),
    ('mac80211:s1g.c', 'out/mac80211/fxa_s1g.c'),
    ('mac80211:ibss.c', 'out/mac80211/fxa_mac80211_ibss.c'),
    ('mac80211:michael.c', 'out/mac80211/fxa_michael.c'),
    ('mac80211:tkip.c', 'out/mac80211/fxa_tkip.c'),
    ('mac80211:wep.c', 'out/mac80211/fxa_wep.c'),
    ('mac80211:wpa.c', 'out/mac80211/fxa_wpa.c'),
    ('mac80211:spectmgmt.c', 'out/mac80211/fxa_spectmgmt.c'),
    ('mac80211:tdls.c', 'out/mac80211/fxa_tdls.c'),
    ('mac80211:ocb.c', 'out/mac80211/fxa_mac80211_ocb.c'),
    ('mac80211:airtime.c', 'out/mac80211/fxa_airtime.c'),
    ('mac80211:uhr.c', 'out/mac80211/fxa_uhr.c'),
    ('mac80211:wbrf.c', 'out/mac80211/fxa_wbrf.c'),
    ('cfg80211:core.c', 'out/cfg80211/fxa_cfg80211_core.c'),
    ('cfg80211:scan.c', 'out/cfg80211/fxa_cfg80211_scan.c'),
    ('cfg80211:chan.c', 'out/cfg80211/fxa_cfg80211_chan.c'),
    ('cfg80211:util.c', 'out/cfg80211/fxa_cfg80211_util.c'),
    ('cfg80211:mlme.c', 'out/cfg80211/fxa_cfg80211_mlme.c'),
    ('cfg80211:sme.c', 'out/cfg80211/fxa_sme.c'),
    ('cfg80211:reg.c', 'out/cfg80211/fxa_cfg80211_reg.c'),
    ('cfg80211:radiotap.c', 'out/cfg80211/fxa_radiotap.c'),
    ('cfg80211:ibss.c', 'out/cfg80211/fxa_cfg80211_ibss.c'),
    ('cfg80211:mesh.c', 'out/cfg80211/fxa_mesh.c'),
    ('cfg80211:ap.c', 'out/cfg80211/fxa_ap.c'),
    ('cfg80211:ocb.c', 'out/cfg80211/fxa_cfg80211_ocb.c'),
    ('shim/src/mhi_hook.c', 'shim/src/fxa_mhi_hook.c'),
    ('mhi:init.c', 'out/mhi-src/fxa_init.c'),
    ('mhi:main.c', 'out/mhi-src/fxa_mhi_main.c'),
    ('mhi:pm.c', 'out/mhi-src/fxa_pm.c'),
    ('mhi:boot.c', 'out/mhi-src/fxa_boot.c'),
    ('qmi:qmi_interface.c', 'deps/linux/drivers/soc/qcom/fxa_qmi_interface.c'),
    ('qmi:qmi_encdec.c', 'deps/linux/drivers/soc/qcom/fxa_qmi_encdec.c'),
    ('ath12k:ce.c', 'out/ath12k-src/fxa_ath12k_ce.c'),
    ('ath12k:core.c', 'out/ath12k-src/fxa_ath12k_core.c'),
    ('ath12k:dbring.c', 'out/ath12k-src/fxa_dbring.c'),
    ('ath12k:dp.c', 'out/ath12k-src/fxa_ath12k_dp.c'),
    ('ath12k:dp_htt.c', 'out/ath12k-src/fxa_dp_htt.c'),
    ('ath12k:dp_mon.c', 'out/ath12k-src/fxa_ath12k_dp_mon.c'),
    ('ath12k:dp_peer.c', 'out/ath12k-src/fxa_dp_peer.c'),
    ('ath12k:dp_rx.c', 'out/ath12k-src/fxa_ath12k_dp_rx.c'),
    ('ath12k:dp_tx.c', 'out/ath12k-src/fxa_ath12k_dp_tx.c'),
    ('ath12k:fw.c', 'out/ath12k-src/fxa_fw.c'),
    ('ath12k:hal.c', 'out/ath12k-src/fxa_ath12k_hal.c'),
    ('ath12k:htc.c', 'out/ath12k-src/fxa_htc.c'),
    ('ath12k:mac.c', 'out/ath12k-src/fxa_mac.c'),
    ('ath12k:mhi.c', 'out/ath12k-src/fxa_ath12k_mhi.c'),
    ('ath12k:p2p.c', 'out/ath12k-src/fxa_p2p.c'),
    ('ath12k:pci.c', 'out/ath12k-src/fxa_ath12k_pci.c'),
    ('ath12k:peer.c', 'out/ath12k-src/fxa_peer.c'),
    ('ath12k:qmi.c', 'out/ath12k-src/fxa_qmi.c'),
    ('ath12k:reg.c', 'out/ath12k-src/fxa_ath12k_reg.c'),
    ('ath12k:wmi.c', 'out/ath12k-src/fxa_ath12k_wmi.c'),
    ('ath12k:wifi7/ce.c', 'out/ath12k-src/wifi7/fxa_wifi7_ce.c'),
    ('ath12k:wifi7/core.c', 'out/ath12k-src/wifi7/fxa_wifi7_core.c'),
    ('ath12k:wifi7/dp.c', 'out/ath12k-src/wifi7/fxa_wifi7_dp.c'),
    ('ath12k:wifi7/dp_mon.c', 'out/ath12k-src/wifi7/fxa_wifi7_dp_mon.c'),
    ('ath12k:wifi7/dp_rx.c', 'out/ath12k-src/wifi7/fxa_wifi7_dp_rx.c'),
    ('ath12k:wifi7/dp_tx.c', 'out/ath12k-src/wifi7/fxa_wifi7_dp_tx.c'),
    ('ath12k:wifi7/hal.c', 'out/ath12k-src/wifi7/fxa_wifi7_hal.c'),
    ('ath12k:wifi7/hal_qcc2072.c', 'out/ath12k-src/wifi7/fxa_hal_qcc2072.c'),
    ('ath12k:wifi7/hal_qcn9274.c', 'out/ath12k-src/wifi7/fxa_hal_qcn9274.c'),
    ('ath12k:wifi7/hal_rx.c', 'out/ath12k-src/wifi7/fxa_hal_rx.c'),
    ('ath12k:wifi7/hal_tx.c', 'out/ath12k-src/wifi7/fxa_hal_tx.c'),
    ('ath12k:wifi7/hal_wcn7850.c', 'out/ath12k-src/wifi7/fxa_hal_wcn7850.c'),
    ('ath12k:wifi7/hw.c', 'out/ath12k-src/wifi7/fxa_hw.c'),
    ('ath12k:wifi7/mhi.c', 'out/ath12k-src/wifi7/fxa_wifi7_mhi.c'),
    ('ath12k:wifi7/pci.c', 'out/ath12k-src/wifi7/fxa_wifi7_pci.c'),
    ('ath12k:wifi7/wmi.c', 'out/ath12k-src/wifi7/fxa_wifi7_wmi.c'),
]

PREFIX = {
    'ath12k': 'out/ath12k-src',
    'mac80211': 'out/mac80211',
    'cfg80211': 'out/cfg80211',
    'mhi': 'out/mhi-src',
    'qmi': 'deps/linux/drivers/soc/qcom',
    'crypto': 'out/crypto',
    'generated': 'out',
}

missing = []
for label, rel in ENTRIES:
    if ':' in label:
        base, rest = label.split(':', 1)
        source = tree / PREFIX[base] / rest
    else:
        source = tree / label
    if not source.is_file():
        missing.append((label, str(source)))
        continue
    target = dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    # Dereference: out/ is a symlink farm, and a link leaving bsd/ would break
    # both the linker and any later tarball of the tree.
    data = source.resolve().read_bytes()
    if label == 'src/nw_crypto_mac80211.c':
        # This is the one unit whose first include is lx_pci.h, and crypto.h
        # (pulled in through aes_wrap.h) uses u8 and size_t without including
        # anything that defines them. State the dependency in the file itself
        # rather than relying on the forced include reaching it.
        data = b'#include "lx.h"' + bytes([10]) + data
    target.write_bytes(data)
if missing:
    for label, source in missing:
        print(f'missing {source} (from {label})', file=sys.stderr)
    raise SystemExit(1)

# Headers travel whole: every source includes its siblings by relative path, and
# the generated out/ trees are symlink farms into deps/, so copy contents rather
# than links. Only the include roots the Makefile names are walked; deps/linux is
# 7k files and only its soc/qcom headers are ever reached.
for sub in ('include', 'shim/include', 'shim/libc', 'shim/crypto', 'out', 'src',
            'deps/linux/drivers/soc/qcom'):
    source = tree / sub
    if not source.is_dir():
        continue
    for header in source.rglob('*'):
        if header.suffix not in ('.h', '.inc') or not header.is_file():
            continue
        target = dest / header.relative_to(tree)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(header.resolve().read_bytes())

print(f'staged {len(ENTRIES)} C files into {dest}')
PY

echo "ath12k: $(find bsd/fuxi_ath12k -name '*.c' | wc -l | tr -d ' ') sources staged for FUXI_ATH12K"
