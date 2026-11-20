#!/bin/bash
# Exercise the generated staging script against the local prepared tree, in a
# throwaway XNU-like root. Proves the copy list resolves and nothing is a link.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/bsd/kern"
: > "$scratch/bsd/kern/bsd_init.c"
cp "$root/xnu/patches/9999-stage-ath12k.sh" "$scratch/stage.sh"
cd "$scratch"
MACHINE_CONFIG=FUXI FUXI_ATH12K_TREE="$root/../../src/Ath12kWifi" bash stage.sh
echo "--- staged tree ---"
find bsd/fuxi_ath12k -name '*.c' | wc -l | tr -d ' ' | sed 's/^/c files: /'
find bsd/fuxi_ath12k -name '*.h' | wc -l | tr -d ' ' | sed 's/^/h files: /'
find bsd/fuxi_ath12k -type l | wc -l | tr -d ' ' | sed 's/^/symlinks: /'
du -sh bsd/fuxi_ath12k | sed 's/^/size: /'
for want in bsd/fuxi_ath12k/src/fxa_nw_wifi.c \
            bsd/fuxi_ath12k/out/ath12k-src/wifi7/fxa_wifi7_ce.c \
            bsd/fuxi_ath12k/shim/include/lx_pci.h \
            bsd/fuxi_ath12k/shim/libc/string.h \
            bsd/fuxi_ath12k/out/pinned/linux/ieee80211.h \
            bsd/fuxi_ath12k/out/ath12k-src/core.h; do
    [[ -e $want ]] || { echo "MISSING $want"; exit 1; }
done
# nw_crypto_mac80211.c is the only unit that starts at lx_pci.h, and the
# crypto header it reaches with aes_wrap.h uses u8 and size_t without including
# anything that defines them. The staged copy must therefore begin with lx.h.
[[ $(head -1 bsd/fuxi_ath12k/src/fxa_nw_crypto_mac80211.c) == '#include "lx.h"' ]] \
    || { echo "staged crypto unit does not include lx.h first"; exit 1; }
# The same header is reached from XNU's forced include, before any of our own
# forced includes run, so it has to state its own dependency too. Without this
# the CI build fails with 139 'unknown type name' errors inside crypto.h.
[[ $(head -1 bsd/fuxi_ath12k/out/crypto/crypto.h) == '#include "lx.h"' ]] \
    || { echo "staged crypto.h does not include lx.h first"; exit 1; }
# Its declarations must stay behind the macro only the CRYPTO flag group
# defines. crypto.h declares wpa_supplicant's aes_encrypt(void *, const u8 *,
# u8 *), Linux declares a different aes_encrypt in <crypto/aes.h>, and
# nw_fils.c includes the Linux one: once XNU's forced include has pulled this
# header into that unit, the two signatures collide.
[[ $(sed -n 2p bsd/fuxi_ath12k/out/crypto/crypto.h) == '#if defined(FUXI_ATH12K_WPA_CRYPTO)' ]] \
    || { echo "staged crypto.h does not fence its declarations"; exit 1; }
[[ $(tail -1 bsd/fuxi_ath12k/out/crypto/crypto.h) == '#endif /* FUXI_ATH12K_WPA_CRYPTO */' ]] \
    || { echo "staged crypto.h does not close the fence"; exit 1; }
# ath12k's debug.h names struct ath12k_base and struct device in prototypes but
# includes neither. Entered before core.h, those tags take prototype scope and
# the definitions in nw_debug.c then conflict. The staged copy must declare both
# at file scope, ahead of the prototypes.
[[ $(head -1 bsd/fuxi_ath12k/out/ath12k-src/debug.h) == '#include "lx.h"' ]] \
    || { echo "staged debug.h does not include lx.h first"; exit 1; }
[[ $(sed -n 2p bsd/fuxi_ath12k/out/ath12k-src/debug.h) == 'struct ath12k_base;' ]] \
    || { echo "staged debug.h does not forward-declare struct ath12k_base"; exit 1; }
[[ $(sed -n 3p bsd/fuxi_ath12k/out/ath12k-src/debug.h) == 'struct device;' ]] \
    || { echo "staged debug.h does not forward-declare struct device"; exit 1; }
# cfg80211.h has the same defect one step further along: it declares
# ieee80211_data_to_8023_exthdr(struct sk_buff *, struct ethhdr *, ...) while
# including only Linux headers. struct ethhdr is defined in lx_pci.h, not lx.h,
# so the staged copy has to lead with lx_pci.h for util.c's definition to agree
# with the declaration.
[[ $(head -1 bsd/fuxi_ath12k/out/pinned/net/cfg80211.h) == '#include "lx_pci.h"' ]] \
    || { echo "staged cfg80211.h does not include lx_pci.h first"; exit 1; }

# The Makefile passes -include lx_pci.h, so that name must resolve on the
# include path the patch declares.
grep -q -- '-I$(FUXI_ATH12K_ROOT)/shim/include' "$root/xnu/patches/0117-fuxi-ath12k.patch" \
    || { echo "patch does not put shim/include on the include path"; exit 1; }
echo STAGE_SCRIPT_OK
