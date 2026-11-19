#!/usr/bin/env python3
"""M2a host tests: the four fuxiwlanfix switches against ath12k semantics.

Each bit reproduces one confirmed fuxi/ath12k divergence found in M0-a/M0-c so
a single CI kernel can bisect the WLAN_MODE(0x22) assertion. The test asserts
both directions: default 0 keeps the r485 wire bytes exactly, and each bit
changes exactly the field it owns and nothing else.

Host only; no device access, no firmware, no kernel build.
"""
from pathlib import Path
import importlib.util
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('m6', ROOT / 'tools/test-fuxi-wlan-m6.py')
m6 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m6)

TESTS = r'''
static uint32_t bdf_file_id(struct wl12_encoder *e) {
    /* TLV 0x01 is four bytes, so TLV 0x10 starts at data+11. */
    assert(e->data[11] == 0x10 && e->data[12] == 4 && e->data[13] == 0);
    uint32_t v; memcpy(&v, e->data + 14, sizeof(v)); return v;
}
static bool host_cap_has_mlo(void) {
    static struct wl12_encoder e;
    bzero(&e, sizeof(e)); e.len = 7;
    assert(!wl12_host_cap(&e));
    /* First MLO TLV is 0x21 and the group is contiguous after 0x1c. */
    for (unsigned i = 7; i + 3 <= e.len; ) {
        unsigned type = e.data[i], len = e.data[i+1] | (unsigned)e.data[i+2] << 8;
        if (type == 0x21) { return true; }
        i += 3 + len;
    }
    return false;
}
int main(void) {
    /* Selector: absent means zero, out-of-range is refused before any write. */
    init();
    assert(!wl_fix_bits && !wl_phycap_opt && !wl_variant_invalid);
    fix_present = true; fix_arg = 0x1f; wl_probe_configure();
    assert(!wl_variant_invalid && wl_fix_bits == 0x1f);
    assert(strstr(logbuf, "M2a fix bits 0x1f (bdfid 1 mlogate 1 erintmod 1 limithi 1 railorder 1) valid 1"));
    fix_arg = 0x20; wl_probe_configure(); assert(wl_variant_invalid);
    fix_arg = -1; wl_probe_configure(); assert(wl_variant_invalid);
    fix_arg = 0; wl_probe_configure(); assert(!wl_variant_invalid && !wl_fix_bits);
    fix_present = false; wl_probe_configure(); assert(!wl_fix_bits);
    fix_present = true;

    /* 0x1 BDFID: only a missing CAP board id is replaced, and only with 0xff. */
    {
        static struct wl12_encoder e;
        const uint8_t data[3] = {0xa5, 0x5a, 0x11};
        wl14_board = 0x12345678; wl14_board_valid = true;
        for (unsigned bits = 0; bits <= 1; bits++) {
            fix_arg = (int)bits; wl_probe_configure();
            e.len = 7; assert(!wl14_encode_bdf(&e, data, 3, 0, 1));
            assert(bdf_file_id(&e) == 0x12345678); /* reported id always wins */
        }
        wl14_board = 0; wl14_board_valid = false;
        fix_arg = 0; wl_probe_configure();
        e.len = 7; assert(!wl14_encode_bdf(&e, data, 3, 0, 1));
        assert(bdf_file_id(&e) == 0); /* r485 bytes */
        fix_arg = 1; wl_probe_configure();
        e.len = 7; assert(!wl14_encode_bdf(&e, data, 3, 0, 1));
        assert(bdf_file_id(&e) == 0xff); /* ATH12K_BOARD_ID_DEFAULT */
        /* Remaining bytes are untouched by the switch.  Layout after the 7-byte
         * QMI header: 0x01@7, 0x10@11, 0x11@18, 0x12@25, 0x13@32, 0x14@40, 0x15@44. */
        assert(e.len == 48);
        assert(e.data[18] == 0x11 && e.data[21] == 3 && e.data[22] == 0 && e.data[23] == 0 && e.data[24] == 0);
        assert(e.data[25] == 0x12 && e.data[28] == 0);
        assert(e.data[32] == 0x13 && e.data[33] == 5 && e.data[35] == 3 &&
               e.data[37] == 0xa5 && e.data[38] == 0x5a && e.data[39] == 0x11);
        assert(e.data[40] == 0x14 && e.data[43] == 1 && e.data[44] == 0x15 && e.data[47] == 1);
        wl14_board = 0; wl14_board_valid = false;
    }

    /* 0x2 MLOGATE: ath12k emits no 0x21-0x26 without a PHY_CAP reply. */
    fix_arg = 0; wl_probe_configure();
    wl12_mlo_enabled = true; wl12_num_phy = 2;
    assert(host_cap_has_mlo()); /* r485 sent the whole group */
    fix_arg = 2; wl_probe_configure();
    wl12_mlo_enabled = !(wl_fix_bits & 2u);
    assert(!host_cap_has_mlo() && !wl12_mlo_enabled);
    /* A PHY_CAP reply with support=1 re-enables the group; support=0 keeps it off. */
    wl12_mlo_enabled = true;
    assert(host_cap_has_mlo());
    /* The 0x57 request itself becomes unconditional under the bit. */
    fix_arg = 0; wl_probe_configure();
    assert(!wl_phycap_opt);
    fix_arg = 2; wl_probe_configure();
    assert(!wl_phycap_opt); /* set by wl_w12_run, not by the selector */
    wl12_mlo_variant = 1; fix_arg = 0; wl_probe_configure();
    assert(!wl_phycap_opt);
    wl12_mlo_variant = 0;

    /* 0x4 ERINTMOD: event contexts carry intmod/msivec like ath12k and sheng. */
    for (unsigned bits = 0; bits <= 4; bits += 4) {
        init(); fix_present = true; fix_arg = (int)bits; wl_probe_configure();
        for (unsigned s = 17; s <= 20; s++) assert(wl5_step(s) == 0);
        struct wl_context *e = (void *)(wl_contexts.va + WL_ECTX);
        if (!bits) {
            assert(!e[0].cfg && !e[0].index && !e[1].cfg && !e[1].index);
        } else {
            assert(!e[0].cfg && e[0].index == 1);          /* 0 ms, msivec 1 */
            assert(e[1].cfg == (1u << 16) && e[1].index == 2); /* 1 ms, msivec 2 */
        }
        /* Ring geometry and the command context are identical either way. */
        assert(e[0].len == 512 && e[1].len == 4096);
        assert(e[1].base == wl_contexts.iova + WL_ER1);
        assert(e[0].wp == e[0].base + 512 - 16 && e[1].wp == e[1].base + 4096 - 16);
        struct wl_context *c = (void *)(wl_contexts.va + WL_CCTX);
        assert(!c->cfg && !c->index && c->len == 2048);
        release();
    }

    /* 0x8 LIMITHI: MHICTRLLIMIT/MHIDATALIMIT high word is 0xf (36-bit mask). */
    for (unsigned bits = 0; bits <= 8; bits += 8) {
        init(); fix_present = true; fix_arg = (int)bits; wl_probe_configure();
        for (unsigned s = 17; s <= 24; s++) assert(wl5_step(s) == 0);
        uint32_t hi = bits ? 0xfu : 0u;
        assert(findwrite(WL_BAR0 + 0x8c, hi, 0) > 0);
        assert(findwrite(WL_BAR0 + 0xa4, hi, 0) > 0);
        /* Low words and both base registers stay at the ath12k values. */
        assert(findwrite(WL_BAR0 + 0x88, 0xffffffff, 0) > 0);
        assert(findwrite(WL_BAR0 + 0xa0, 0xffffffff, 0) > 0);
        assert(findwrite(WL_BAR0 + 0x80, 0, 0) > 0);
        assert(findwrite(WL_BAR0 + 0x98, 0, 0) > 0);
        /* High word precedes its low word, as mhi_write_reg does. */
        assert(findwrite(WL_BAR0 + 0x8c, hi, 0) < findwrite(WL_BAR0 + 0x88, 0xffffffff, 0));
        release();
    }

    /* All five together still pass every step and publish the contexts. */
    init(); fix_present = true; fix_arg = 0x1f; wl_probe_configure();
    for (unsigned s = 17; s <= 28; s++) assert(wl5_step(s) == 0);
    assert(strstr(logbuf, "contexts published"));
    release();
    puts("M2a fuxiwlanfix selectors, BDF file_id, MLO gate, event intmod/msivec and 36-bit limits passed");
    return 0;
}
'''


def main():
    assert 'python3 tools/test-fuxi-wlan-m2a.py' in (ROOT / 'tools/ci/build.sh').read_text()
    driver = (ROOT / 'xnu/fuxi-wlan/fuxi_wlan.c').read_text()
    block = driver.split('/* ---- milestone 5:', 1)[1].split('static void\nfw_dump(', 1)[0]
    block = '/* ---- milestone 5:' + block
    block = re.sub(r'__asm__ volatile\([^;]+;', '/* host memory barrier */;', block)
    helpers = m6.TESTS.split('int main(void) {', 1)[0]
    source = m6.PREAMBLE + block + helpers + TESTS
    with tempfile.TemporaryDirectory(prefix='fuxi-wlan-m2a-') as tmp:
        cfile, binary = Path(tmp) / 'm2a.c', Path(tmp) / 'm2a'
        cfile.write_text(source)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_DEFAULT_SOURCE', '-g',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(cfile), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
