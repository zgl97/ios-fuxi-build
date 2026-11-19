#!/usr/bin/env python3
"""ON-only rails: production fw_run/M3/TCS plus the W16 PCI/MHI/QMI peer.

Host-only, synthetic registers and packets. No device, firmware or network.
"""
from pathlib import Path
import importlib.util
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def early_transport():
    """Run the real normal-path hook, M3 rail/GPIO cases and waited transport."""
    m6i = module('rails_m6i', 'test-fuxi-wlan-m6i.py')
    driver = (ROOT / 'xnu/fuxi-wlan/fuxi_wlan.c').read_text()
    shim = (ROOT / 'shim/fuxi-probe/wlan.c').read_text()
    pre = m6i.PREAMBLE.replace('sent[32]', 'sent[128]').replace('nsent<32', 'nsent<128')
    pre = pre.replace('static void wlan_m3(unsigned step) {', 'static void unused_m3(unsigned step) {')
    pre = pre.replace('static int wl_rails_on(unsigned variant, const char *where) { return 0; }', r'''
static unsigned wl_rails_variant;
static bool rh_poisoned;
static void wlan_m3(unsigned);
static void wl_sleep(unsigned ms) { delays+=ms*1000; }
static unsigned expect_votes, corrupt_idx;
static void delay(unsigned us) { fw_delay(us); }
static void ts_gpio(unsigned n,unsigned func,unsigned pull,unsigned ma,int out) {
    assert(n==80 && !func && pull==3 && ma==16 && out==1);
    assert(nsent==expect_votes && !rh_poisoned && !fw_faults); gpio_high++;
}
''')
    pre = pre.replace('    assert(base==WL_GCC); return 0;',
                      '    if(base>=WL_TLMM && base<WL_TLMM+0x100000) return gpio_high?2:0;\n    assert(base==WL_GCC); return 0;')
    # GPIO constants/state must be visible to usb_rd, not only ts_gpio.
    pre = pre.replace('#define WL_GCC', '#define WL_TLMM 0xf100000ull\nstatic unsigned gpio_high;\n#define WL_GCC', 1)
    transport = '#define RH_STRIDE' + driver.split('#define RH_STRIDE', 1)[1].split('#define FUXI_WLAN_XNU', 1)[0]
    # The rail block reads the M2-a fix switches, which live far above it in the
    # driver and are therefore outside the extracted transport/rails slices.
    fixes = ('/* fuxiwlanfix=<bits>' +
             driver.split('/* fuxiwlanfix=<bits>', 1)[1].split('static bool wl_phycap_opt;', 1)[0] +
             'static bool wl_phycap_opt;\n')
    rails = fixes + '/* ---- opt-in ON-only' + driver.split('/* ---- opt-in ON-only', 1)[1].split('/* ---- end opt-in ON-only rail re-votes ---- */', 1)[0]
    accessors = '\n'.join(line for line in shim.splitlines()
                          if line.startswith(('static uint8_t wl_cdb8(', 'static uint32_t wl_cdb16(')))
    icc = '/* ---- milestone 6i:' + driver.split('/* ---- milestone 6i:', 1)[1].split('/* ---- end milestone 6i ---- */', 1)[0]
    runner = 'static void wl17_clk_step(void) {} /* P17 SPMI sleep clock: kernel only */\nstatic void\nfw_run(' + driver.split('static void\nfw_run(', 1)[1].split('static void\nfw_thread(', 1)[0]
    # Execute unmodified cases 2 and 3 from the included production loader.
    m3 = 'static uint32_t wl_vrm' + shim.split('static uint32_t wl_vrm', 1)[1].split('static int wl_poll_clr', 1)[0]
    m3 += 'static int rails_m3(unsigned step) { uint32_t *r=wl3_report; switch(step) {\n'
    m3 += ' case 2:' + shim.split(' case 2:', 1)[1].split(' case 4:', 1)[0]
    m3 += 'default: return 0; }}\n'
    m3 += r'''
static const unsigned addresses[]={0x40a00,0x40200,0x40400,0x40500,0x44000,0x40600};
static void wlan_m3(unsigned step) {
    stages[nstages++]=step;
    if(step==1)for(unsigned i=0;i<6;i++)wl3_report[20+i]=addresses[i];
    int e=rails_m3(step); wl3_report[3]=e?(step<<8)|e:0;
    /* Inject a stale cmd-db result after the baseline votes complete. */
    if(step==2 && corrupt_idx<6)wl3_report[20+corrupt_idx]^=0x100;
}
'''
    fixture = m6i.TESTS.split('int main(void) {', 1)[0]
    tests = r'''
static void boot(unsigned variant) {
    fixture(); memset(wl4_report,0,sizeof(wl4_report));
    enabled_present=true; enabled_arg=0; /* isolate regulators from BCMs */
    wl_rails_variant=variant; wl_rails_requested=variant!=0;
    wl_variant_invalid=wl_seq_requested=false; wl_seq_flags=0;
    gpio_high=0; corrupt_idx=6; expect_votes=variant==1?30:12;
}
static void check(unsigned from) {
    const unsigned addr[]={0x40a00,0x44000,0x40400,0x40200,0x40600,0x40500};
    const unsigned mv[]={1800,1000,980,950,1900,1350};
    for(unsigned i=0;i<18;i++) {
        struct sent *s=&sent[from+i]; unsigned reg=i%3;
        assert(s->addr==addr[i/3]+4*reg && s->data==(reg==0?mv[i/3]:reg==1?1:7));
        assert(s->msgid==0x10108 && s->wait==1 && s->index==0);
        assert(s->trigger==from+i+1);
    }
    assert(!rh_poisoned && rsc[0]==0xa5 && rsc[1]==0x80);
    assert(rsc[(0x2a0+0x2c)/4]==0xdead); /* other TCS unchanged */
}
int main(void) {
    for(unsigned v=0;v<=2;v++) {
        boot(v); fw_run(3); assert(!wl5_report[3] && gpio_high==1);
        assert(nsent==(v==1?30:12));
        /* Absent/late preserves all twelve existing M3 regulator commands. */
        const unsigned addr[]={0x44000,0x40a00,0x40400,0x40200,0x40500,0x40600};
        const unsigned mv[]={1000,1800,980,952,1350,1900};
        for(unsigned i=0;i<12;i++)assert(sent[i].addr==addr[i/2]+4*(i%2) &&
                                       sent[i].data==(i%2?1:mv[i/2]));
        if(v==1) {
            check(12); assert(strstr(logs,"early before WLAN_EN high complete ON-only"));
            assert(strstr(logs,"addr 0x40208 value 7 completion 0"));
        } else assert(!strstr(logs,"rails variant"));
    }
    /* WLFIX_RAILORDER: mode(7) precedes enable(1), as Linux regulator_do_enable
     * and sheng (WifiProbe.cpp:349-351) both order them. */
    boot(1); wl_fix_bits = WLFIX_RAILORDER; fw_run(3);
    assert(!wl5_report[3] && gpio_high==1 && nsent==30);
    {
        const unsigned addr[]={0x40a00,0x44000,0x40400,0x40200,0x40600,0x40500};
        const unsigned mv[]={1800,1000,980,950,1900,1350};
        for(unsigned i=12;i<30;i++) {
            struct sent *s=&sent[i]; unsigned reg=(i-12)%3;
            assert(s->addr==addr[(i-12)/3]+4*reg);
            assert(s->data==(reg==0?mv[(i-12)/3]:(reg==1?7:1)));
        }
        assert(strstr(logs,"mode-HPM/PWM addr 0x40204 value 7 completion 0"));
        assert(strstr(logs,"enable addr 0x40208 value 1 completion 0"));
    }
    wl_fix_bits = 0;
    for(unsigned idx=0;idx<6;idx++) {
        boot(1); corrupt_idx=idx; fw_run(3);
        assert(wl5_report[3] && !gpio_high && nsent==12 && strstr(logs,"rails skip"));
    }
    for(unsigned nth=1;nth<=18;nth++) {
        boot(1); fail_cstat=1; fail_trigger=12+nth; fw_run(3);
        assert(wl5_report[3] && !gpio_high && nsent==12+nth && rh_poisoned);
        assert(strstr(logs,"completion 0x2"));
        unsigned count=nsent; assert(wl_rails_on(1,"poisoned") && nsent==count);
        /* A failed regulator transaction is never cleared/retried by the hook. */
    }
    boot(1); rsc[0x28/4]=0; fw_run(3); assert(!gpio_high && !nsent);
    boot(1); fault_write=RH_TCS_PA+0x38; fw_run(3); assert(!gpio_high && !nsent && fw_faults);
    boot(1); wl_variant_invalid=true; fw_run(3); assert(!gpio_high && !nsent && !nstages);
    boot(1); fw_run(2); assert(!gpio_high && nsent==12); /* early bisect stop */
    puts("Rails normal-path production M3/TCS ordering, baseline, mode, mismatch, faults and quarantine passed");
    return 0;
}
'''
    source = pre + transport + accessors + icc + rails + m3 + runner + fixture + tests
    source = re.sub(r'__asm__ volatile\([^;]+;', '/* host barrier */;', source)
    with tempfile.TemporaryDirectory(prefix='fuxi-wlan-rails-') as tmp:
        cfile, binary = Path(tmp) / 'rails.c', Path(tmp) / 'rails'
        cfile.write_text(source)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-g',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(cfile), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


def late_and_cycle():
    w16 = module('rails_w16', 'test-fuxi-wlan-w16.py')
    m6o = w16.w15.w14.w12.w11.w10.m6o
    m6o.m6.PREAMBLE = m6o.m6.PREAMBLE.replace('static bool PE_parse_boot_argn(',
        'static int rails_arg; static bool rails_present; static unsigned wl_rails_variant;\nstatic bool PE_parse_boot_argn(')
    m6o.m6.PREAMBLE = m6o.m6.PREAMBLE.replace(
        'if(!strcmp(name,"fuxiwlanrails")) { assert(size==sizeof(int)); return false; }',
        'if(!strcmp(name,"fuxiwlanrails")) { assert(size==sizeof(int)); if(rails_present)memcpy(out,&rails_arg,size); return rails_present; }')
    # Keep M6o's original strict peer for legacy cyclerails. New votes have
    # their own independent expected address/data sequence and breadcrumbs.
    m6o.PEER = m6o.PEER.replace('    assert(t==0 && nrail<64);', r'''
    assert(t==0 && nrail<64);
    if(wl_rails_variant) {
        const unsigned a[]={0x40a00,0x44000,0x40400,0x40200,0x40600,0x40500};
        const unsigned mv[]={1800,1000,980,950,1900,1350};
        unsigned i=nrail%18,reg=i%3;
        assert(addr==a[i/3]+reg*4 && data==(reg==0?mv[i/3]:reg==1?1:7));
        assert(strstr(last_log_line(),"rails begin"));
        record('R',addr,data);rail_addr[nrail]=addr;rail_data[nrail++]=data;
        return fail_rail && nrail==fail_rail;
    }
''')
    peer = w16.TESTS.split('int main(void) {', 1)[0]
    peer = peer.replace('assert(step<3 && wl14_ready && m3_requests==1);', r'''
        assert(step<3 && wl14_ready && m3_requests==1);
        if(wl_rails_variant==2) {
            assert(nrail==(step==2?18:0));
            if(step==2)assert(strstr(logbuf,"late before WLAN_MODE complete ON-only"));
        }
''')
    w16.TESTS = peer + r'''
int main(void) {
    for(int q=0;q<=6;q++)for(int v=-1;v<=3;v++) {
        qrtr_arg=q;rails_present=true;rails_arg=v;wl_probe_configure();
        assert(wl_variant_invalid==(v<0 || v>2));
        assert(wl_rails_variant==(v>=0 && v<=2?(unsigned)v:0));
    }
    rails_present=false;rails_arg=2;wl_probe_configure();assert(!wl_rails_variant);
    for(unsigned v=1;v<=2;v++) {
        rails_present=true;rails_arg=v;setup("cyclerails");
        assert(wl_variant_invalid && !nrail);
        assert(wl_m6o_rails(false) && wl_m6o_rails(true) && !nrail);
    }
    rails_arg=1;setup("cycle");assert(!run(18) && nrail==18);
    int high=firstwrite(TS_TLMM+0x1000*80+4,2,2),last=-1;
    for(unsigned i=0;i<ne;i++)if(events[i].kind=='R')last=i;
    assert(high>last && last>=0 && strstr(logbuf,"cycle before WLAN_EN high complete ON-only"));
    cleanup(18);assert(nrail==18); /* no rail OFF on containment */
    for(unsigned nth=1;nth<=18;nth++) {
        rails_arg=1;setup("cycle");fail_rail=nth;
        unsigned failed=run(18);
        assert(failed==17 && nrail==nth);
        assert(firstwrite(TS_TLMM+0x1000*80+4,2,2)<0);
        cleanup(17);assert(nrail==nth);
    }
    rails_arg=2;setup("cycle");assert(!run(18) && !nrail);cleanup(18);
    for(unsigned v=0;v<=2;v++)for(unsigned w=0;w<=4;w++) {
        rails_arg=v;w16_arg=w;boot15(0,true);assert(!finish_step());
        assert(seen15==3 && nrail==(v?18:0)); /* W16 boot includes cycle */
        check_no_ce();contain();assert(nrail==(v?18:0));
    }
    for(unsigned nth=1;nth<=18;nth++) {
        rails_arg=2;boot16(3,0,true);fail_rail=nth;
        assert(finish_step() && seen15==2 && nrail==nth && !frees);
        check_no_ce();contain();assert(nrail==nth);
    }
    for(unsigned idx=0;idx<6;idx++) {
        rails_arg=2;boot16(3,0,true);wl3_report[20+idx]^=0x100;
        assert(finish_step() && seen15==2 && !nrail && strstr(logbuf,"rails skip"));contain();
    }
    rails_arg=2;boot16(3,11,true);assert(finish_step() && seen15==2 && !nrail);contain();
    rails_arg=2;boot16(3,22,true);assert(finish_step() && seen15==3 && nrail==18);
    assert(strstr(logbuf,"MODE timeout") && !frees);check_no_ce();contain();assert(nrail==18);
    puts("Rails selectors, cycle resume/containment, late exact QMI ordering, all W16 variants and failures passed");
    return 0;
}
'''
    w16.main()


def main():
    assert 'python3 tools/test-fuxi-wlan-rails.py' in (ROOT / 'tools/ci/build.sh').read_text()
    early_transport()
    late_and_cycle()


if __name__ == '__main__':
    main()
