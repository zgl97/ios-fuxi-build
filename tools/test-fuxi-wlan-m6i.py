#!/usr/bin/env python3
"""Run production M6i cmd-db, BCM, RSC and fw_run code against synthetic data.

Host-only; no device access, firmware input, kernel build or network needed.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PREAMBLE = r'''
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <assert.h>
#define MIN(a,b) ((a)<(b)?(a):(b))
#define RH_DRV_PA 0x17a20000UL
#define RH_TCS_PA 0x17a20d00UL
#define WL_CMDDB 0x81c60000UL
#define WL_CMDDB_BYTES 0x20000u
#define WL_DBI 0x60000000UL
#define WL3_STEPS 13
#define WL4_STEPS 16
#define WL4_MAGIC 0x574c0004
#define WL5_STEPS 28
#define WL_GCC 0x100000UL
static uint8_t db[WL_CMDDB_BYTES];
static uint32_t rsc[1024], cfg, revision, fw_faults, fw_fault_esr, wl3_faults;
static uint64_t fw_fault_far;
static uint64_t wl_perst_done, wl_link_done;
static uint32_t wl3_report[128], wl4_report[64], wl5_report[128], link;
static unsigned writes, triggers, delays, stages[32], nstages, bhi, aborts;
static unsigned fail_irq, fail_trigger, idle_busy, bad_cleanup, fail_cstat;
static uint32_t cmd_status;
static unsigned status_reads;
static uint64_t fault_read, fault_write, drop_write;
static bool enabled_present, bw_present;
static int enabled_arg, bw_arg;
static char logs[32768];
static size_t loglen;
static void test_printf(const char *fmt, ...) {
    va_list ap; va_start(ap,fmt);
    int n=vsnprintf(logs+loglen,sizeof(logs)-loglen,fmt,ap);
    va_end(ap); assert(n>=0 && (size_t)n<sizeof(logs)-loglen); loglen+=n;
}
#define printf test_printf
struct sent { uint32_t addr,data,msgid,wait,trigger,index; };
static struct sent sent[32];
static unsigned nsent;
static uint32_t usb_rd(uintptr_t base, uint32_t off) {
    uint64_t pa=base+off;
    if(pa==fault_read) { fw_faults++; return ~0u; }
    if(base==WL_CMDDB) {
        assert(!(off&3) && off<=WL_CMDDB_BYTES-4);
        return db[off] | (uint32_t)db[off+1]<<8 | (uint32_t)db[off+2]<<16 | (uint32_t)db[off+3]<<24;
    }
    if(base==RH_DRV_PA) { assert(off==0 || off==12); return off ? cfg : revision; }
    if(base==RH_TCS_PA) {
        assert(!(off&3) && off/4<1024);
        if(off>=0x40 && off<=0x40+15*24 && (off-0x40)%24==0) status_reads++;
        return rsc[off/4];
    }
    if(base==WL_DBI) { assert(off==0x80); return link; }
    assert(base==WL_GCC); return 0;
}
static void ts_wr(uintptr_t base, uint32_t off, uint32_t v) {
    assert(base==RH_TCS_PA && off/4<1024 && !(off&3));
    if(fw_faults) return;
    if(base+off==fault_write) { fw_faults++; return; }
    writes++;
    if(base+off==drop_write) return;
    if(off==8) { rsc[1]&=~v; return; }
    if(off==0x24 && v==0 && bad_cleanup && triggers) return;
    rsc[off/4]=v;
    if(off==0x24 && v==0x1010000) {
        triggers++;
        uint32_t en=rsc[0x2c/4];
        for(unsigned i=0;i<16 && (en & (1u<<i));i++) {
            assert(nsent<32);
            sent[nsent++]=(struct sent){rsc[(0x38+i*24)/4],rsc[(0x3c+i*24)/4],
                rsc[(0x34+i*24)/4],rsc[0x20/4],triggers,i};
            rsc[(0x40+i*24)/4]=fail_cstat && (!fail_trigger || fail_trigger==triggers) ? 0x100 : cmd_status;
        }
        if(!fail_irq || (fail_trigger && fail_trigger!=triggers)) rsc[1]|=1;
        rsc[0x28/4]=idle_busy ? 0 : 1;
    }
}
static void fw_delay(unsigned us) { delays+=us; }
static bool PE_parse_boot_argn(const char *name, void *out, int size) {
    assert(size==sizeof(int));
    if(!strcmp(name,"fuxiwlanicc")) { if(enabled_present) memcpy(out,&enabled_arg,size); return enabled_present; }
    assert(!strcmp(name,"fuxiwlaniccbw"));
    if(bw_present) { memcpy(out,&bw_arg,size); }
    return bw_present;
}
static uint64_t mach_absolute_time(void) { return 0; }
static void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns) { *ns=t; }
static void wlan_m3(unsigned step) {
    stages[nstages++]=step;
    if(step==2) assert(!wl_icc_test_expect_votes || triggers==4);
    if(step==11) { wl3_report[67]=0x70; link=0x20220000; }
}
static void wl_mark(unsigned s) { }
static int wl4_step(unsigned s) { return 0; }
static int wl5_step(unsigned s) { if(s==21) bhi++; return 0; }
static unsigned wl_pdc_acks, pdc_error;
static unsigned wl_seq_flags;
static bool wl_variant_invalid, wl_seq_requested;
static bool wl_rails_requested;
static bool wl_linux_takeover; /* candidate B: step 19 handover flag */
static int wl_rails_on(unsigned variant, const char *where) { return 0; }
static int wl_cnss_pdc(void) { return pdc_error; } /* peer exercised by M6n suite */
static void wl5_status(unsigned s) { }
static bool wl_smmu_fault(unsigned s) { return false; }
static void fw_m3_summary(unsigned s) { }
static void wl5_abort(unsigned s) { aborts++; }
static void wl_finish(unsigned limit) { if(limit>=17) wl5_abort(limit); }
static void fw_dump(const char *tag, const uint32_t *r, unsigned n) { }
'''
# Keep the fw_run mock's expectation visible before its definition.
PREAMBLE = PREAMBLE.replace('static void wlan_m3(', 'static bool wl_icc_test_expect_votes;\nstatic void wlan_m3(')
TESTS = r'''
static void put16(unsigned at, uint16_t v) {
    assert(at+2<=sizeof(db)); db[at]=v; db[at+1]=v>>8;
}
static void put32(unsigned at, uint32_t v) { put16(at,v); put16(at+2,v>>16); }
static void fixture(void) {
    memset(db,0,sizeof(db)); memset(rsc,0,sizeof(rsc));
    memset(wl3_report,0,sizeof(wl3_report)); memset(wl5_report,0,sizeof(wl5_report));
    fw_faults=wl3_faults=writes=triggers=delays=nstages=bhi=aborts=nsent=0;
    fault_read=fault_write=drop_write=0; fail_irq=fail_trigger=idle_busy=bad_cleanup=fail_cstat=status_reads=0;
    cmd_status=0x10101; /* device M6j: ISSUED|COMPLETED|bit0, IRQ_STATUS 0 */
    cfg=(16u<<27)|(8u<<12); revision=3u<<16;
    rsc[0x28/4]=1; rsc[0]=0xa5; rsc[1]=0x80;
    rsc[(0x2a0+0x2c)/4]=0xdead; /* another slot must survive */
    enabled_present=bw_present=false; enabled_arg=bw_arg=0;
    wl_icc_enabled=1; wl_icc_bw=0; wl_icc_configured=wl_icc_ready=wl_icc_failed=false;
    wl_icc_acks=0; rh_poisoned=false; wl_icc_test_expect_votes=true;
    wl_linux_takeover=false;
    loglen=0; logs[0]=0;
    put32(0,1); put32(4,0x0c0330db);
    put16(8,5); put16(10,0); put16(12,512); put16(14,6); put16(16,0x100);
    /* Synthetic, deliberately non-source order VCDs and non-default units.
       MC0+ACV share VCD0, SH1+SH0 VCD1; CN0 VCD2, SN7 VCD3. */
    const unsigned vcd[]={3,1,1,0,0,2};
    for(unsigned i=0;i<6;i++) {
        wl_bcms[i]=(struct wl_bcm){.name=wl_bcms[i].name,.mask=(i==1 || i==4 || i==5)};
        unsigned e=144+i*24, a=656+i*8;
        memcpy(db+e,wl_bcms[i].name,strlen(wl_bcms[i].name));
        put32(e+16,0x50100+i*4); put16(e+20,8); put16(e+22,i*8);
        put32(a,1000); put16(a+4,16); db[a+6]=vcd[i];
    }
    wl3_report[67]=0x70; link=0x20220000;
}
int main(void) {
    struct wl_bcm b={.name="SN7"};
    fixture(); assert(!wl_bcm_lookup(&b));
    assert(b.addr==0x50100 && b.unit==1000 && b.width==16 && b.vcd==3 && b.version==0x100);
    /* Header and aux offsets are independent, relative to byte 144. */
    memmove(db+176,db+144,6*24); memset(db+144,0,32); put16(10,32);
    assert(!wl_bcm_lookup(&b) && b.addr==0x50100);
    fixture();
    /* A valid eight-byte aux tuple can end exactly at the mapped bound. */
    put16(12,65535); put16(166,65385);
    memcpy(db+WL_CMDDB_BYTES-8,db+656,8);
    assert(!wl_bcm_lookup(&b) && b.unit==1000);
    fixture();
    /* Exact zero padding and duplicate-name rejection. */
    db[147]='x'; assert(wl_bcm_lookup(&b));
    fixture(); memcpy(db+168,db+144,24); assert(wl_bcm_lookup(&b));
    fixture(); put32(4,0); assert(wl_bcm_lookup(&b));
    fixture(); put32(0,2); assert(wl_bcm_lookup(&b));
    fixture(); put32(140,1); assert(wl_bcm_lookup(&b));
    fixture(); put16(14,65535); assert(wl_bcm_lookup(&b));
    fixture(); put16(12,65535); put16(166,65535); assert(wl_bcm_lookup(&b));
    fixture(); put16(164,7); assert(wl_bcm_lookup(&b));
    fixture(); put32(160,0x40100); assert(wl_bcm_lookup(&b));
    fixture(); put32(160,0x50101); assert(wl_bcm_lookup(&b));
    fixture(); put16(8,4); assert(wl_bcm_lookup(&b));
    fixture(); put32(656,0); assert(wl_bcm_lookup(&b));
    fixture(); put16(660,0); assert(wl_bcm_lookup(&b));
    /* Mask BCMs (Linux enable_mask) carry no unit/width; device ACV: width 0, VCD 12. */
    { struct wl_bcm m={.name="SN7",.mask=true}; fixture(); put16(660,0); db[662]=12;
      assert(!wl_bcm_lookup(&m) && m.width==0 && m.vcd==12); }
    fixture(); fault_read=WL_CMDDB+656; assert(wl_bcm_lookup(&b)); assert(fw_faults);
    /* A missing LAST dependency causes no partial vote. Same for aliases. */
    fixture(); db[144+5*24]=0; assert(wl_icc_step(2)); assert(!writes && !triggers);
    fixture(); put32(144+5*24+16,0x50100); assert(wl_icc_step(2)); assert(!writes);
    assert(wl_bcm_div(1,16)==1 && wl_bcm_div(31,16)==1 && wl_bcm_div(32,16)==2);
    assert(wl_bcm_encode(0,0,false)==0 && wl_bcm_encode(0,0,true)==0x40000000);
    assert(wl_bcm_encode(0,1,true)==0x60000001);
    assert(wl_bcm_encode(1,1,true)==0x60004001);
    assert(wl_bcm_encode(UINT64_MAX,UINT64_MAX,true)==0x6fffffff);
    b=(struct wl_bcm){.unit=2000,.width=4};
    wl_bcm_bandwidth(&b,127,127,16,4); assert(b.x==3 && b.y==15);
    wl_bcm_bandwidth(&b,0,1,16,4); assert(b.x==0 && b.y==1);
    fixture(); assert(!wl_icc_step(2)); assert(triggers==4 && wl_icc_acks==1 && nsent==6 && status_reads==6);
    /* One trigger per VCD; response on all, commit/wait on final only. */
    const unsigned order[]={3,4,1,2,5,0};
    const unsigned commit[]={0,1,0,1,1,1};
    const unsigned trigger[]={1,1,2,2,3,4}, index[]={0,1,0,1,0,0};
    for(unsigned i=0;i<6;i++) {
        assert(sent[i].addr==0x50100+order[i]*4);
        assert(!!(sent[i].data&(1u<<30))==commit[i]);
        assert(sent[i].msgid==0x10108);
        assert(sent[i].trigger==trigger[i] && sent[i].index==index[i]);
        assert(sent[i].wait==(trigger[i]<=2 ? 2 : 1));
        assert(sent[i].data&(1u<<29));
    }
    assert(wl_bcms[0].x==0 && wl_bcms[0].y==0x3fff); /* synthetic unit saturates */
    assert(wl_bcms[1].x==0 && wl_bcms[1].y==1);
    assert(wl_bcms[4].x==0 && wl_bcms[4].y==8);
    assert(wl_bcms[5].x==1 && wl_bcms[5].y==1);
    assert(rsc[0]==0xa5 && rsc[1]==0x80 && !rsc[0x2c/4] && !rsc[0x20/4]);
    assert(rsc[(0x2a0+0x2c)/4]==0xdead && !rh_poisoned);
    assert(strstr(logs,"M6i ICC MC0 addr") && strstr(logs,"ack yes"));
    assert(!wl_icc_step(12) && triggers==8 && wl_icc_acks==2);
    assert(strstr(logs,"link/download pcie-mem 0/1000000 cpu-pcie 0/1"));
    /* Runtime metadata determines numbers; optional KIWI rows aggregate
       sum average / max peak separately for the two path segments. */
    fixture(); for(unsigned i=0;i<6;i++) put32(656+i*8,1000000);
    assert(!wl_icc_step(2)); assert(wl_bcms[0].y==250 && wl_bcms[2].y==250 && wl_bcms[3].y==1000);
    bw_present=true; bw_arg=4; /* configure is once per session */
    wl_icc_bw=4;
    assert(!wl_icc_step(12));
    assert(wl_bcms[0].x==100 && wl_bcms[0].y==1600);
    assert(wl_bcms[2].x==25 && wl_bcms[2].y==2188);
    assert(wl_bcms[3].x==100 && wl_bcms[3].y==8755);
    fixture(); link=0x20230000; assert(!wl_icc_step(12));
    assert(strstr(logs,"pcie-mem 0/1969000"));
    fixture(); link=0; assert(wl_icc_step(12)); assert(!triggers);
    fixture(); link=0x20420000; assert(wl_icc_step(12)); assert(!triggers);
    fixture(); enabled_present=true; enabled_arg=0;
    assert(!wl_icc_step(2) && !wl_icc_step(12) && !writes);
    fixture(); enabled_present=true; enabled_arg=2; assert(wl_icc_step(2) && !writes);
    fixture(); bw_present=true; bw_arg=9; assert(wl_icc_step(2) && !writes);
    fixture(); bw_present=true; bw_arg=-1; assert(wl_icc_step(2) && !writes);
    for(unsigned row=0;row<9;row++) {
        fixture(); bw_present=true; bw_arg=row;
        assert(!wl_icc_step(2) && !wl_icc_step(12));
        assert((wl_bcms[0].x != 0)==(row != 0));
    }
    /* BCM requests complete on ISSUED|COMPLETED (last cmd of a VCD waits)
       with the TCS idle; the device leaves IRQ_STATUS 0 for them. */
    const uint32_t statuses[]={0,0x100,0x10000,0x10100,0xdeadbeef};
    for(unsigned i=0;i<sizeof(statuses)/sizeof(statuses[0]);i++) {
        fixture(); fail_irq=1; cmd_status=statuses[i];
        bool ok=(statuses[i]&0x10100)==0x10100;
        assert(!wl_icc_step(2)==ok);
        assert(ok ? triggers==4 && !rh_poisoned : triggers==1 && rh_poisoned);
    }
    /* A missing fresh IRQ stops at each possible group, logs every command
       in that group exactly once, and never triggers a later group. */
    for(unsigned group=1;group<=4;group++) {
        fixture(); fail_irq=1; fail_cstat=1; fail_trigger=group;
        assert(wl_icc_step(2));
        const unsigned counts[]={0,2,2,1,1}, totals[]={0,2,4,5,6};
        assert(triggers==group && nsent==totals[group] && delays==100000);
        assert(rh_poisoned && wl_icc_failed && !wl_icc_acks);
        assert(status_reads==totals[group]-counts[group]+100001*counts[group]);
        const char *at=logs;
        for(unsigned i=0;i<counts[group];i++) {
            const struct sent *c=&sent[totals[group]-counts[group]+i];
            char expected[256];
            snprintf(expected,sizeof(expected),
                "M6j RPMh timeout TCS 0 cmd %u addr %#x data %#x MSGID 0x10108 CMD_STATUS 0x100 IRQ_STATUS 0x80 CONTROL 0x1010000 IDLE 0x1 faults 0",
                i,c->addr,c->data);
            at=strstr(at,expected); assert(at); at++;
        }
        assert(!strstr(at,"M6j RPMh timeout"));
        char summary[160];
        snprintf(summary,sizeof(summary),
            "M6j ICC VCD %u cmds %u ack unconfirmed error 0x2 acked_groups %u",
            group-1,counts[group],group-1);
        assert(strstr(logs,summary));
        unsigned acked=0;
        for(at=logs;(at=strstr(at,"M6j ICC VCD"));at++) {
            if(strstr(at,"ack yes") && strstr(at,"ack yes")<strchr(at,'\n')) acked++;
        }
        assert(acked==group-1);
        unsigned n=writes; assert(rh_send(0,0x40000,1));
        assert(wl_icc_step(12) && writes==n && triggers==group);
    }
    fixture(); idle_busy=1; assert(wl_icc_step(2)); assert(rh_poisoned && !wl_icc_acks);
    assert(strstr(logs,"IRQ_STATUS 0x81 CONTROL 0x1010000 IDLE 0 faults 0"));
    fixture(); bad_cleanup=1; assert(wl_icc_step(2)); assert(rh_poisoned && !wl_icc_acks);
    fixture(); rsc[0x2c/4]=1; assert(wl_icc_step(2)); assert(!writes);
    fixture(); rsc[1]|=1; assert(wl_icc_step(2)); assert(!writes && !triggers);
    fixture(); cfg=(1u<<27)|(8u<<12); assert(wl_icc_step(2)); assert(!writes);
    fixture(); revision=2u<<16; assert(wl_icc_step(2)); assert(!writes);
    fixture(); drop_write=RH_TCS_PA+0x38; assert(wl_icc_step(2)); assert(!triggers && rh_poisoned);
    fixture(); drop_write=RH_TCS_PA+0x24;
    for(unsigned i=0;i<6;i++) rsc[(0x40+i*24)/4]=0x10100;
    assert(wl_icc_step(2)); assert(!triggers && rh_poisoned && !wl_icc_acks);
    fixture(); fault_write=RH_TCS_PA+0x3c; assert(wl_icc_step(2)); assert(!triggers && rh_poisoned);
    fixture(); fail_irq=1; fault_read=RH_TCS_PA+0x40;
    assert(wl_icc_step(2)); assert(triggers==1 && rh_poisoned && fw_faults);
    assert(strstr(logs,"CMD_STATUS 0xffffffff") && strstr(logs,"faults 2")); /* poll + log read */
    /* Regulator retains its exact one-command request and BOTH status bits. */
    fixture(); cmd_status=0x10100; assert(!rh_send(0,0x40000,1000));
    assert(nsent==1 && sent[0].addr==0x40000 && sent[0].data==1000);
    assert(sent[0].msgid==0x10108 && sent[0].wait==1 && status_reads==1);
    assert(writes==15 && rsc[0]==0xa5 && rsc[1]==0x80 && !rh_poisoned);
    for(unsigned i=0;i<3;i++) {
        fixture(); cmd_status=statuses[i]; assert(rh_send(0,0x40000,1000)==2);
        assert(delays==100000 && status_reads==100000 && rh_poisoned);
        assert(!strstr(logs,"M6j RPMh timeout"));
    }
    /* Observed VCD layout, with wholly invented addresses/data. */
    fixture(); db[656+4*8+6]=12; db[656+5*8+6]=5; put16(656+4*8+4,0);
    assert(!wl_icc_step(2) && triggers==5 && nsent==6);
    const unsigned observed[]={3,1,2,0,5,4};
    for(unsigned i=0;i<6;i++) assert(sent[i].addr==0x50100+observed[i]*4);
    assert(strstr(logs,"M6j ICC VCD 12 cmds 1 ack yes error 0 acked_groups 5"));
    /* VCD is a byte, not a bit mask or a hardcoded 0..9 range. */
    fixture(); db[662]=255; assert(!wl_icc_step(2));
    /* Even a larger single VCD remains indivisible, with one final wait. */
    fixture(); for(unsigned i=0;i<6;i++) db[656+i*8+6]=7;
    assert(!wl_icc_step(2) && triggers==1 && nsent==6);
    for(unsigned i=0;i<6;i++) {
        assert(sent[i].addr==0x50100+i*4 && sent[i].msgid==0x10108 && sent[i].wait==32);
        assert(!!(sent[i].data&(1u<<30))==(i==5));
    }
    /* Actual fw_run hooks: initial vote before step2 and all clocks, link
       update before endpoint setup; either failure prevents the BHI step. */
    fixture(); pdc_error=6; fw_run(22);
    assert(!triggers && !bhi && !nstages); /* policy failure precedes all power writes */
    pdc_error=0;
    fixture(); wl_variant_invalid=wl_seq_requested=true; fw_run(22);
    assert(!triggers && !bhi && !nstages); /* invalid M6o selector blocks before power */
    wl_variant_invalid=wl_seq_requested=false;
    fixture(); fw_run(22); assert(triggers==8 && bhi==1 && aborts==1 && wl_icc_acks==2);
    for(unsigned phase=0;phase<2;phase++) for(unsigned group=1;group<=4;group++) {
        fixture(); fail_irq=1; fail_cstat=1; fail_trigger=phase*4+group; fw_run(22);
        assert(triggers==fail_trigger && !bhi && wl_icc_failed && rh_poisoned);
        assert(wl_icc_acks==phase && wl5_report[2]==(phase ? 12 : 2));
        assert(nstages==(phase ? 11 : 1) && stages[nstages-1]==(phase ? 11 : 1));
    }
    fixture(); enabled_present=true; enabled_arg=0; wl_icc_test_expect_votes=false;
    fw_run(22); assert(bhi==1 && !triggers && !wl_icc_acks);
    /* Candidate B: step 19 takeover stops fw_run before the old W10 lane. */
    fixture(); wl_linux_takeover=true; fw_run(22);
    assert(bhi==0 && wl5_report[2]==19 && wl5_report[32+19]==0x1013);
    puts("M6i/M6j production cmd-db/encoding/VCD/TCS/ordering/failure tests passed");
    return 0;
}
'''


def main():
    driver = (ROOT / 'xnu/fuxi-wlan/fuxi_wlan.c').read_text()
    shim = (ROOT / 'shim/fuxi-probe/wlan.c').read_text()
    transport = driver.split('#define RH_STRIDE', 1)[1].split('#define FUXI_WLAN_XNU', 1)[0]
    transport = '#define RH_STRIDE' + transport
    accessors = '\n'.join(line for line in shim.splitlines()
                          if line.startswith(('static uint8_t wl_cdb8(', 'static uint32_t wl_cdb16(')))
    icc = driver.split('/* ---- milestone 6i:', 1)[1].split('/* ---- end milestone 6i ---- */', 1)[0]
    icc = '/* ---- milestone 6i:' + icc
    runner = 'static void wl17_clk_step(void) {} /* P17 SPMI sleep clock: kernel only */\nstatic void\nfw_run(' + driver.split('static void\nfw_run(', 1)[1].split('static void\nfw_thread(', 1)[0]
    source = PREAMBLE + transport + accessors + icc + runner + TESTS
    source = re.sub(r'__asm__ volatile\([^;]+;', '/* host barrier */;', source)
    with tempfile.TemporaryDirectory(prefix='fuxi-wlan-m6i-') as tmp:
        cfile, binary = Path(tmp) / 'm6i.c', Path(tmp) / 'm6i'
        cfile.write_text(source)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-g', '-Wall', '-Wextra',
                        '-Wno-unused-parameter', '-Wno-unused-function',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(cfile), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    main()
