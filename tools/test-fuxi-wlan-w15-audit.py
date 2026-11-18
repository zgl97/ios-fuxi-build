#!/usr/bin/env python3
"""Independent v6.12 EI audit; offline by default, --ath12k-src verifies the EI facts.

Wire/layout facts: ath12k qmi.c:1624-1999, hw.c:249-337,457-543,933-977,
hal.c:1338-1448,2428-2497, hal.h and ce.c:145-223,787-822.
BSD-3-Clause-Clear; The Linux Foundation (2018-2021), Qualcomm Innovation
Center, Inc. (2021-2024). License text: xnu/fuxi-wlan/fuxi_wlan.c.
This is an original encoder, not Linux qmi_helpers or the existing W15 golden.
No network or device access. Optional reference sources stay outside the repo.
"""
import argparse
import importlib.util
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
# Transcribed EI metadata: field, TLV, type, elem_len (not wire count width).
# QMI_OPT_FLAG never serializes; QMI_DATA_LEN sizeof(u8) does serialize ONE
# byte even though the native request struct stores each length in a u32.
TARGET = [('pipe_num', 0, 'U4', 1), ('pipe_dir', 0, 'E4', 1),
          ('nentries', 0, 'U4', 1), ('nbytes_max', 0, 'U4', 1), ('flags', 0, 'U4', 1)]
SERVICE = [('service_id', 0, 'U4', 1), ('pipe_dir', 0, 'E4', 1), ('pipe_num', 0, 'U4', 1)]
SHADOW = [('addr', 0, 'U4', 1)]
SHADOW1 = [('id', 0, 'U2', 1), ('offset', 0, 'U2', 1)]
INI = [('enable_fwlog_valid', 0x10, 'OPT', 1), ('enable_fwlog', 0x10, 'U1', 1)]
MODE = [('mode', 1, 'U4', 1), ('hw_debug_valid', 0x10, 'OPT', 1), ('hw_debug', 0x10, 'U1', 1)]
CFG = [('host_version_valid', 0x10, 'OPT', 1), ('host_version', 0x10, 'STR', 17)]
for name, tag, limit, kind in [('tgt_cfg', 0x11, 12, 'TARGET'),
                               ('svc_cfg', 0x12, 24, 'SERVICE'),
                               ('shadow_reg', 0x13, 24, 'SHADOW1'),
                               ('shadow_reg_v3', 0x17, 60, 'SHADOW')]:
    CFG += [(name+'_valid', tag, 'OPT', 1), (name+'_len', tag, 'LEN1', 1),
            (name, tag, kind, limit)]
NESTED = dict(TARGET=TARGET, SERVICE=SERVICE, SHADOW=SHADOW, SHADOW1=SHADOW1)

# hw.c target table: reserved is native-only, NEVER a sixth word.
TARGET_ROWS = [(0, 2, 32, 2048, 0), (1, 1, 32, 2048, 0), (2, 1, 32, 2048, 0),
               (3, 2, 32, 2048, 0), (4, 2, 256, 256, 8), (5, 1, 32, 2048, 0),
               (6, 3, 32, 16384, 0), (7, 4, 0, 0, 8), (8, 3, 32, 16384, 0)]
SERVICE_ROWS = [(0x104, 2, 3), (0x104, 1, 2), (0x102, 2, 3), (0x102, 1, 2),
                (0x101, 2, 3), (0x101, 1, 2), (0x103, 2, 3), (0x103, 1, 2),
                (0x100, 2, 3), (0x100, 1, 2), (1, 2, 0), (1, 1, 2),
                (0x300, 2, 4), (0x300, 1, 1)]
# Independent flat address fixture, checked against upstream arithmetic below.
SHADOW_WORDS = [int(x, 16) for x in '''
 a3b04c a3b054 a3b05c a3b064 a3b06c a3b074 a3b07c a3b084 a3b08c
 a3b028 a3b020 a3b0ac a46000 a46008 a46010 a46018 a46020 a46028 a4604c
 a370b8 a37010 a370cc a370d4 a370dc a370e4 a370ec a370f4 a370fc a37104
 1b80400 1b83400 1b8340c 1b85400 1b8540c 1b86400 1b88400'''.split()]


def scalar(kind, value):
    return int(value).to_bytes({'U1': 1, 'LEN1': 1, 'U2': 2, 'U4': 4, 'E4': 4}[kind], 'little')


def encode(schema, values, nested=False):
    output = b''
    groups = {}
    enabled = True
    for field, tag, kind, limit in schema:
        if kind == 'OPT':
            enabled = bool(values.get(field, 0))
            continue
        if not enabled:
            continue
        value = values[field]
        if kind in NESTED:
            assert len(value) <= limit
            sub = NESTED[kind]
            body = b''.join(encode(sub, dict(zip((f[0] for f in sub), row)), True) for row in value)
        elif kind == 'STR':
            assert len(value) < limit
            body = value  # top-level QMI_STRING: no count and no terminating NUL
        else:
            body = scalar(kind, value)
        if nested:
            output += body
        else:
            groups[tag] = groups.get(tag, b'') + body
    return output if nested else b''.join(struct.pack('<BH', t, len(v))+v for t, v in groups.items())


def requests(omit_shadow=False):
    values = dict(host_version_valid=1, host_version=b'WIN', tgt_cfg_valid=1,
                  tgt_cfg_len=9, tgt_cfg=TARGET_ROWS, svc_cfg_valid=1,
                  svc_cfg_len=14, svc_cfg=SERVICE_ROWS, shadow_reg_valid=0,
                  shadow_reg_v3_valid=not omit_shadow, shadow_reg_v3_len=36,
                  shadow_reg_v3=[(v,) for v in SHADOW_WORDS])
    # The lane used to send enable_fwlog=0 against upstream 1; it now matches
    # upstream so the firmware log can be inspected when MODE stalls.
    linux_ini = encode(INI, dict(enable_fwlog_valid=1, enable_fwlog=1))
    ini = encode(INI, dict(enable_fwlog_valid=1, enable_fwlog=1))
    assert linux_ini == bytes.fromhex('10 01 00 01')
    assert ini == linux_ini
    bodies = [ini, encode(CFG, values), encode(MODE, dict(mode=0, hw_debug_valid=1, hw_debug=0))]
    assert [len(b) for b in bodies] == [4, 362 if omit_shadow else 510, 11]
    return [struct.pack('<BHHH', 0, txn, msg, len(body))+body
            for txn, msg, body in zip((13, 14, 15), (0x2f, 0x23, 0x22), bodies)]


def check_upstream(path):
    """Parse the actual EI declarations, not fuxi C or our earlier tests."""
    qmi = (path/'qmi.c').read_text()
    qh = (path/'qmi.h').read_text()
    assert re.search(r'#define\s+ATH12K_HOST_VERSION_STRING\s+"WIN"', qh)
    constants = {k: int(v, 0) for k, v in re.findall(r'^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$', qh, re.M)}
    assert constants['QMI_WLANFW_WLAN_MODE_REQ_V01'] == 0x22
    assert constants['QMI_WLANFW_WLAN_CFG_REQ_V01'] == 0x23
    assert constants['ATH12K_QMI_WLANFW_WLAN_INI_REQ_V01'] == 0x2f
    types = dict(OPT='QMI_OPT_FLAG', LEN1='QMI_DATA_LEN', STR='QMI_STRING',
                 U1='QMI_UNSIGNED_1_BYTE', U2='QMI_UNSIGNED_2_BYTE',
                 U4='QMI_UNSIGNED_4_BYTE', E4='QMI_SIGNED_4_BYTE_ENUM')
    names = [('wlan_ini_req_msg', INI), ('wlan_mode_req_msg', MODE), ('wlan_cfg_req_msg', CFG),
             ('ce_tgt_pipe_cfg_s', TARGET), ('ce_svc_pipe_cfg_s', SERVICE),
             ('shadow_reg_v3_cfg_s', SHADOW), ('shadow_reg_cfg_s', SHADOW1)]
    for name, schema in names:
        table = re.search(r'qmi_wlanfw_'+name+r'_v01_ei\[\] = \{(.*?)\n\};', qmi, re.S)[1]
        entries = [e for e in re.findall(r'\{(.*?)\}', table, re.S) if 'QMI_EOTI' not in e]
        assert len(entries) == len(schema), name
        for entry, (field, tag, kind, limit) in zip(entries, schema):
            assert re.search(r'offsetof\([^,]+,\s*'+field+r'\)', entry), (name, field)
            assert int(re.search(r'\.tlv_type\s*=\s*(\w+)', entry)[1], 0) == tag
            length = re.search(r'\.elem_len\s*=\s*([^,]+)', entry)[1].strip()
            for key in sorted(constants, key=len, reverse=True):
                length = re.sub(r'\b'+key+r'\b', str(constants[key]), length)
            assert re.fullmatch(r'[\d +]+', length) and sum(map(int, length.split('+'))) == limit
            size = re.search(r'\.elem_size\s*=\s*sizeof\(([^)]+)\)', entry)[1]
            if kind in NESTED:
                assert '.ei_array' in entry and 'VAR_LEN_ARRAY' in entry and 'QMI_STRUCT' in entry
                struct_name = {'TARGET': 'ce_tgt_pipe', 'SERVICE': 'ce_svc_pipe',
                               'SHADOW': 'shadow_reg_v3', 'SHADOW1': 'shadow_reg'}[kind]
                assert size == 'struct qmi_wlanfw_'+struct_name+'_cfg_s_v01'
                assert re.search(r'\.ei_array\s*=\s*qmi_wlanfw_'+struct_name+r'_cfg_s_v01_ei\b', entry)
            else:
                assert types[kind] in entry and 'NO_ARRAY' in entry
                assert size == {'OPT':'u8', 'LEN1':'u8', 'STR':'char', 'U1':'u8', 'U2':'u16',
                                'U4':'u32', 'E4':'enum qmi_wlanfw_pipedir_enum_v01'}[kind], (field, size)
    # Check every target table word straight from hw.c, independent of fuxi.
    hw = (path/'hw.c').read_text()
    table = hw.split('ath12k_target_ce_config_wlan_wcn7850[] = {', 1)[1].split('\n};', 1)[0]
    ce = (path/'ce.h').read_text()
    macros = {k: int(v, 0) for k, v in re.findall(r'^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\b', ce, re.M)}
    def number(expr):
        return int(expr, 0) if expr.strip()[0].isdigit() else macros[expr.strip()]
    rows = []
    for entry in re.findall(r'\{(.*?)\}', table, re.S):
        fields = dict(re.findall(r'\.(\w+)\s*=\s*__cpu_to_le32\(([^)]+)\)', entry))
        row = [number(fields[f]) for f in ['pipenum', 'pipedir', 'nentries', 'nbytes_max']]
        row.append(sum(number(term) for term in fields['flags'].split('|')))
        assert number(fields['reserved']) == 0
        rows.append(tuple(row))
    assert rows == TARGET_ROWS
    htc = (path/'htc.h').read_text()
    groups = {'RSVD': 0, 'WMI': 1, 'HTT': 3}
    for group, value in groups.items():
        assert re.search(r'ATH12K_HTC_SVC_GRP_'+group+r'\s*=\s*'+str(value)+r'\b', htc)
    macros.update({name: (groups[group] << 8) | int(index) for name, group, index in
                   re.findall(r'(ATH12K_HTC_SVC_ID_\w+)\s*=\s*SVC\(ATH12K_HTC_SVC_GRP_(RSVD|WMI|HTT),\s*(\d+)\)', htc)})
    table = hw.split('ath12k_target_service_to_ce_map_wlan_wcn7850[] = {', 1)[1].split('\n};', 1)[0]
    rows = [tuple(number(v) for v in re.findall(r'__cpu_to_le32\(([^)]+)\)', entry))
            for entry in re.findall(r'\{(.*?)\}', table, re.S)]
    assert rows == SERVICE_ROWS+[(0, 0, 0)]  # sentinel is not transmitted
    params = hw.split('.name = "wcn7850 hw2.0"', 1)[1].split('\n\t},', 1)[0]
    for field, value in [('ce_count', 9), ('target_ce_count', 9), ('svc_to_ce_map_len', 14)]:
        assert re.search(r'\.'+field+r'\s*=\s*'+str(value)+r'\b', params)
    assert re.search(r'\.supports_shadow_regs\s*=\s*true', params)
    # Derive all 36 shadow addresses from upstream HAL macros, template and
    # WCN7850 overrides. No addresses or strides read from our C or W15 test.
    hal = (path/'hal.c').read_text()
    hh = (path/'hal.h').read_text()
    macros.update({k: int(v, 0) for k, v in re.findall(r'^#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)\s*$', hh, re.M)})
    template = hal.split('hw_srng_config_template[] = {', 1)[1].split('\n};', 1)[0]
    config = hal.split('static int ath12k_hal_srng_create_config_wcn7850', 1)[1].split('\n}', 1)[0]
    enum = hh.split('enum hal_ring_type {', 1)[1].split('HAL_MAX_RING_TYPES', 1)[0]
    result = []
    def arithmetic(expr):
        for key in sorted(macros, key=len, reverse=True):
            expr = re.sub(r'\b'+key+r'\b', str(macros[key]), expr)
        assert re.fullmatch(r'[\d +\-]+', expr), expr
        return sum(int(v.replace(' ', '')) for v in re.findall(r'[+-]?\s*\d+', expr))
    for ring in re.findall(r'\bHAL_\w+', enum):
        entry = re.search(r'\['+ring+r'\]\s*=\s*\{(.*?)\}', template, re.S)[1]
        override = re.search(r's = &hal->srng_config\['+ring+r'\];(.*?)(?=\n\ts =|$)', config, re.S)
        override = override[1] if override else ''
        if 'HAL_CE_' in ring or re.search(r'\.mac_type = ATH12K_HAL_SRNG_[DP]MAC', entry) or 's->mac_type = ATH12K_HAL_SRNG_PMAC' in override:
            continue
        count_match = re.search(r'(?:s->|\.)max_rings\s*=\s*(\d+)', override if 'max_rings' in override else entry)
        count = int(count_match[1]) if count_match else 0
        if not count:
            continue
        start = arithmetic(re.search(r's->reg_start\[1\]\s*=\s*([^;]+)', override)[1])
        stride = re.search(r's->reg_size\[1\]\s*=\s*([^;]+)', override)
        stride = arithmetic(stride[1]) if stride else 0
        result.extend(start+i*stride+(4 if 'HAL_SRNG_DIR_DST' in entry else 0) for i in range(count))
    assert len(result) == 29
    host = (path/'ce.c').read_text().split('ath12k_host_ce_config_wcn7850[] = {', 1)[1].split('\n};', 1)[0]
    for i, entry in enumerate(re.findall(r'\{(.*?)\}', host, re.S)):
        if int(re.search(r'\.src_nentries\s*=\s*(\d+)', entry)[1]):
            stride = macros['HAL_SEQ_WCSS_UMAC_CE1_SRC_REG']-macros['HAL_SEQ_WCSS_UMAC_CE0_SRC_REG']
            result.append(macros['HAL_SEQ_WCSS_UMAC_CE0_SRC_REG']+i*stride+macros['HAL_CE_DST_RING_HP'])
        if int(re.search(r'\.dest_nentries\s*=\s*(\d+)', entry)[1]):
            stride = macros['HAL_SEQ_WCSS_UMAC_CE1_DST_REG']-macros['HAL_SEQ_WCSS_UMAC_CE0_DST_REG']
            result += [macros['HAL_SEQ_WCSS_UMAC_CE0_DST_REG']+i*stride+macros['HAL_CE_DST_RING_HP'],
                       macros['HAL_SEQ_WCSS_UMAC_CE0_DST_REG']+i*stride+macros['HAL_CE_DST_STATUS_RING_HP']+macros['HAL_OFFSET_FROM_HP_TO_TP']]
    assert result == SHADOW_WORDS
    print('Upstream EI field order/types/sizes/TLVs/limits and WCN7850 target/shadow derivation verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ath12k-src', type=Path)
    args = parser.parse_args()
    import fuxi_wlan_mlo_test as mlo
    if args.ath12k_src:
        check_upstream(args.ath12k_src)
        mlo.check_upstream(args.ath12k_src)
    spec = importlib.util.spec_from_file_location('w15', ROOT/'tools/test-fuxi-wlan-w15.py')
    w15 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w15)
    tests = w15.TESTS
    for i, request in enumerate(requests()):
        tests, count = re.subn(r'static const uint8_t golden'+str(i)+r'\[\]=\{[^}]+\};',
                              'static const uint8_t golden%d[]={%s};' % (i, ','.join(map(str, request))), tests)
        assert count == 1, 'independent EI bytes must replace each earlier golden'
    # Both default and no-shadow variants traverse the full production state
    # machine; the default also retains all malformed-peer/containment cases.
    extra = '''
    for(unsigned variant=0;variant<2;variant++) {
        w15_arg=variant;boot15(0,true);assert(wl15_variant==variant);
        assert(!finish_step() && wl_w11_ok && seen15==3);check_no_ce();contain();
    }
    qrtr_arg=5;w15_arg=2;wl_probe_configure();assert(wl_variant_invalid);
    w15_arg=-1;wl_probe_configure();assert(wl_variant_invalid);
    qrtr_arg=4;wl_probe_configure();assert(!wl_variant_invalid && !wl15_variant);
    w15_arg=0;
'''
    # A second CFG fixture explicitly omits TLV 0x17 (not zero-length).
    omit = requests(True)[1]
    tests = tests.replace('int main(void) {', 'static const uint8_t noshadow[]={%s};\nint main(void) {' % ','.join(map(str, omit)))
    tests = tests.replace('const uint8_t *golden[]={golden0,golden1,golden2};', 'const uint8_t *golden[]={golden0,wl15_variant?noshadow:golden1,golden2};')
    tests = tests.replace('sizeof(golden0),sizeof(golden1),sizeof(golden2)', 'sizeof(golden0),wl15_variant?sizeof(noshadow):sizeof(golden1),sizeof(golden2)')
    tests = tests.replace('for(unsigned i=0;i<4;i++) {', 'for(unsigned i=0;i<(wl15_variant?3:4);i++) {')
    tests = tests.replace('    puts("W15 INI/', extra+'    puts("W15 INI/')
    # Declaration must precede the peer's use of the omitted fixture.
    declaration = re.search(r'static const uint8_t noshadow\[\]=\{[^}]+\};', tests)[0]
    tests = tests.replace(declaration+'\n', '').replace('static const uint8_t golden0', declaration+'\nstatic const uint8_t golden0')
    w15.TESTS = tests
    m6 = w15.w14.w12.w11.w10.m6o.m6
    m6.PREAMBLE = m6.PREAMBLE.replace('static bool PE_parse_boot_argn(', 'static int w15_arg;\nstatic bool PE_parse_boot_argn(')
    m6.PREAMBLE = m6.PREAMBLE.replace('if(!strcmp(name,"fuxiwlanw15")) { assert(size==sizeof(int)); return false; }',
                                    'if(!strcmp(name,"fuxiwlanw15")) { assert(size==sizeof(int)); memcpy(out,&w15_arg,size); return true; }')
    w15.main()
    spec = importlib.util.spec_from_file_location('w15_mlo', ROOT/'tools/test-fuxi-wlan-w15.py')
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    fresh.TESTS = mlo.tests(fresh.TESTS, w15=True)
    fresh.main()
    print('Independent EI bytes and opt-in shadow omission passed against production C')


if __name__ == '__main__':
    main()
