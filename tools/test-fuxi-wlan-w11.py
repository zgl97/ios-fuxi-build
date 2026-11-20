#!/usr/bin/env python3
"""W11 production C + synthetic MHI/QRTR/QMI peer, ASan/UBSan; host only."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('w10', ROOT / 'tools/test-fuxi-wlan-w10.py')
w10 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w10)

TESTS = r'''
/* Independent device indices/credits. Device does not use host bookkeeping or
 * host framing helpers. Literal wire bytes below also test the LE codecs. */
static unsigned dev_cmd, dev_tx, dev_rx, dev_er[2], dev_credit[2];
static unsigned commands, starts, stops, packets, cap_requests, resumes, mode;
static unsigned delay_ticks, peer_stage;
static bool dev_on, dev_busy;
static uint8_t saved_request[64];
static unsigned saved_len;
static void put16(uint8_t *p,unsigned v) {p[0]=v;p[1]=v>>8;}
static void put32(uint8_t *p,uint32_t v) {put16(p,v);put16(p+2,v>>16);}
static uint32_t get32(const uint8_t *p) {return p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static unsigned next(unsigned i,unsigned n) {return i+1==n?0:i+1;}
static void dev_event(unsigned ring,uint64_t ptr,uint32_t d0,uint32_t d1) {
    const unsigned off[2]={0x1a00,0x2000}, count[2]={32,256};
    /* RP may catch WP only after consuming the final available credit. */
    assert(dev_er[ring]!=dev_credit[ring]);
    struct wl_tre *p=(void *)(wl_contexts.va+off[ring]+16*dev_er[ring]);
    *p=(struct wl_tre){ptr,d0,d1};
    dev_er[ring]=next(dev_er[ring],count[ring]);
    struct wl_context *ec=(void *)(wl_contexts.va+0x1800+44*ring);
    ec->rp=wl_contexts.iova+off[ring]+16*dev_er[ring];
}
static void dev_rx_packet(const uint8_t *p,unsigned len) {
    assert(len<=4096);
    struct wl_context *c=(void *)(wl_contexts.va+21*44);
    uint64_t base=wl_ipcr.iova+0x100;
    assert(c->wp!=base+16*dev_rx); /* device may only consume a posted TRE */
    struct wl_tre *tre=(void *)(wl_ipcr.va+0x100+16*dev_rx);
    assert(tre->d0==4096 && tre->d1==0x20200);
    assert(tre->ptr>=wl_ipcr.iova+0x11000 && tre->ptr+4096<=wl_ipcr.iova+wl_ipcr.size);
    memcpy((void *)(wl_ipcr.va+tre->ptr-wl_ipcr.iova),p,len);
    dev_event(1,base+16*dev_rx,len|(2u<<24),0x15000000|0x220000);
    dev_rx=next(dev_rx,16); c->rp=base+16*dev_rx;
}
static void dev_qrtr(unsigned type,const uint8_t *data,unsigned len,bool compact,bool confirm) {
    uint8_t p[512]={0}; unsigned hdr=compact?16:32;
    if(compact) {
        p[0]=3;p[1]=type;p[2]=confirm;
        put32(p+4,len);put16(p+8,7);put16(p+10,type==1?42:0xffff);
        put16(p+12,1);put16(p+14,type==1?0x4000:0xffff);
    } else {
        put32(p,1);put32(p+4,type);put32(p+8,7);put32(p+12,type==1?42:0xfffffffeu);
        put32(p+16,confirm);put32(p+20,len);put32(p+24,1);put32(p+28,type==1?0x4000:0xfffffffeu);
    }
    memcpy(p+hdr,data,len); dev_rx_packet(p,hdr+((len+3)&~3u));
}
static void dev_control(unsigned type,bool compact) {
    uint8_t data[20]={0};put32(data,type);
    if(type==4 || type==5) {put32(data+4,0x45);put32(data+8,0x101);put32(data+12,7);put32(data+16,42);}
    dev_qrtr(type,data,20,compact,false);
}
/* Explicit fixture: response txn 1, CAP 0x24, result success; chip 2,
 * family 4, board 255, soc 0x1234, version 0x1020304, nested "date", build. */
static const uint8_t cap_reply[]={
    2,1,0,0x24,0,56,0,
    2,4,0,0,0,0,0,
    0x10,8,0,2,0,0,0,4,0,0,0,
    0x11,4,0,255,0,0,0,
    0x12,4,0,0x34,0x12,0,0,
    0x13,9,0,4,3,2,1,4,'d','a','t','e',
    0x14,9,0,'f','w','-','b','u','i','l','d','1'
};
static void dev_cap(void) {
    uint8_t reply[sizeof(cap_reply)];memcpy(reply,cap_reply,sizeof(reply));
    if(mode==10) reply[10]=1; /* QMI negative result */
    if(mode==11) reply[1]=2; /* unrelated transaction: should time out */
    if(mode==12) reply[8]=0xff; /* malformed result length */
    dev_qrtr(1,reply,sizeof(reply),mode==9,true);
}
static void dev_work(void) {
    if(!dev_on || dev_busy || !wl_ipcr.va) return;
    dev_busy=true;
    struct wl_context *cc=(void *)(wl_contexts.va+0x1900);
    uint64_t cb=wl_contexts.iova+0x3000;
    while(cc->wp!=cb+16*dev_cmd) {
        struct wl_tre *cmd=(void *)(wl_contexts.va+0x3000+16*dev_cmd);
        unsigned ch=cmd->d1>>24, op=(cmd->d1>>16)&255;
        assert(cmd->ptr==0 && cmd->d0==0 && (ch==20 || ch==21) && (op==18 || op==17));
        commands++; if(op==18) starts++;else stops++;
        if(mode!=1 && !(mode==22 && op==18 && ch==21) && !(mode==23 && op==17)) {
            uint64_t ptr=cb+16*dev_cmd;
            dev_event(0,mode==2?ptr+16:ptr,(mode==3?0x11u:1u)<<24,0x210000);
        }
        dev_cmd=next(dev_cmd,128);cc->rp=cb+16*dev_cmd;
    }
    struct wl_context *tx=(void *)(wl_contexts.va+20*44);
    uint64_t tb=wl_ipcr.iova;
    while(tx->wp && tx->wp!=tb+16*dev_tx) {
        struct wl_tre *tre=(void *)(wl_ipcr.va+16*dev_tx);
        assert(tre->d1==0x20200 && tre->d0>=32 && tre->d0<=256);
        assert(tre->ptr==wl_ipcr.iova+0x1000+4096*dev_tx);
        const uint8_t *p=(void *)(wl_ipcr.va+tre->ptr-wl_ipcr.iova);
        assert(get32(p)==1 && get32(p+8)==1 && get32(p+16)==0);
        unsigned type=get32(p+4), len=get32(p+20);packets++;
        assert(tre->d0==32+((len+3)&~3u));
        if(type==2) {assert(len==20 && get32(p+32)==2);}
        if(type==10) {assert(len==20 && get32(p+36)==0x45 && get32(p+40)==1);peer_stage=1;}
        if(type==7) {assert(len==20 && get32(p+36)==1 && get32(p+40)==0x4000);resumes++;}
        if(type==1) {
            assert(get32(p+24)==7 && get32(p+28)==42 && get32(p+12)==0x4000);
            assert(len==7 && !memcmp(p+32,"\0\1\0\x24\0\0\0",7));
            cap_requests++;memcpy(saved_request,p,tre->d0);saved_len=tre->d0;
        }
        if(mode!=13) dev_event(1,tb+16*dev_tx,(tre->d0|(2u<<24)),0x14000000|0x220000);
        dev_tx=next(dev_tx,16);tx->rp=tb+16*dev_tx;
        if(type==1 && mode!=6) {
            if(mode==7) dev_control(3,false);
            else if(mode==8) dev_control(5,false);
            else dev_cap();
        }
    }
    if(peer_stage==1 && mode!=4 && mode<15 && (mode!=14 || !delay_ticks)) {
        peer_stage=2;
        dev_control(2,mode==9);
        if(mode!=5) dev_control(4,mode==9);
    }
    dev_busy=false;
}
static void w11_write(uint64_t pa,uint32_t v) {
    if(!dev_on || !wl_w11_attempted) return;
    if((mode==18 && pa==WL_BAR0+0x70) || (mode==19 && pa==WL_BAR0+0x3a0) ||
       (mode==20 && pa==WL_BAR0+0x3a8) || (mode==21 && pa==WL_BAR0+0x700) ||
       (mode==24 && pa==WL_BAR0+0x6f8)) {fw_faults++;return;}
    /* Observe the final LOW doorbell write; assert high word/context ordering. */
    if(pa==WL_BAR0+0x700 || pa==WL_BAR0+0x708) {
        unsigned r=pa==WL_BAR0+0x708;uint64_t base=wl_contexts.iova+(r?0x2000:0x1a00);
        assert(regs_peek(pa+4)==0 && v>=base && v<base+(r?4096:512) && !((v-base)&15));
        dev_credit[r]=(v-base)/16;
        struct wl_context *ec=(void *)(wl_contexts.va+0x1800+44*r);assert(ec->wp==v);
    }
    if(pa==WL_BAR0+0x70) {
        assert(regs_peek(pa+4)==0 && ((struct wl_context *)(wl_contexts.va+0x1900))->wp==v);
        dev_work();
    }
    if(pa==WL_BAR0+0x3a0 || pa==WL_BAR0+0x3a8) {
        assert(regs_peek(pa+4)==0);
        assert(((struct wl_context *)(wl_contexts.va+44*(pa==WL_BAR0+0x3a0?20:21)))->wp==v);
        dev_work();
    }
}
static void w10_tick(unsigned ms) {
    if(ms!=10 || !dev_on) return;
    if(delay_ticks) delay_ticks--;
    if(mode==15) {setreg(WL_BAR0+0x128,3);return;}
    if(mode==16) {setreg(wl_cb_addr(wl_cb)+0x58,2);return;}
    if(mode==17) {fw_faults++;return;}
    dev_work();
}
static void start(unsigned behavior,bool keep) {
    dev_on=dev_busy=false;setup("cycle,parf,dbi,eq,hostirq,msi,por,prefix");
    stop22_arg=0;keep_arg=keep;qrtr_arg=1;probe_arg="civac";wl_probe_configure();
    dev_cmd=dev_tx=dev_rx=dev_er[0]=dev_er[1]=0;dev_credit[0]=31;dev_credit[1]=255;
    mode=behavior;commands=starts=stops=packets=cap_requests=resumes=peer_stage=0;
    delay_ticks=3;tx_behavior=0;noncontig=true;saved_len=0;file_size=WL_CHUNK+37;
    for(unsigned s=17;s<=27;s++) {int e=wl5_step(s);if(e)fprintf(stderr,"mode %u step %u: %u\n%s",behavior,s,e,logbuf);assert(!e);}
    assert(wl_ipcr.nc && wl_contexts.nc && !wl_amss_reached && !wl_chan_attempts);
    for(unsigned off=0;off<wl_ipcr.size;off+=4096)
        assert((walk(wl_ipcr.iova+off)&0xfffffffff000ull)==wl_phys(wl_ipcr.va+off));
    dev_on=true;
}
static int finish_step(void) {return wl5_step(28);}
static void contain(void) {
    unsigned before=ne;wl5_abort(28);
    assert(frees==allocations && !wl_ipcr.va && !wl_contexts.va);
    assert(!(regs_peek(WL_EPCFG+4)&4) && !(regs_peek(TS_TLMM+0x1000*94+4)&2));
    int reset=-1,perst=-1,last_free=-1;
    for(unsigned i=before;i<ne;i++) {
        if(events[i].kind=='W' && events[i].pa==WL_BAR0+0x38 && events[i].v==2)reset=i;
        if(events[i].kind=='W' && events[i].pa==TS_TLMM+0x1000*94+4 && !(events[i].v&2))perst=i;
        if(events[i].kind=='F')last_free=i;
    }
    assert(reset>=0 && perst>reset && last_free>perst);
    dev_on=false;
}
static void clear_error(void) {wl_w11_error=0;wl_w11_ok=false;}
int main(void) {
    assert(sizeof(cap_reply)==63);
    start(0,true);
    assert(!finish_step() && wl_w11_ok && starts==2 && cap_requests==1 && resumes==1);
    assert(strstr(logbuf,"W11 chan 20/21 started") && strstr(logbuf,"W11 QRTR hello"));
    assert(strstr(logbuf,"W11 WLFW server node 7 port 42"));
    assert(strstr(logbuf,"chip id 0x2 chip family 0x4 board id 0xff soc id 0x1234 firmware 0x1020304 timestamp date build fw-build1"));
    assert(saved_len==40 && saved_request[39]==0);
    unsigned before=ne;wl_finish(28);assert(ne==before && !frees); /* success keep */
    assert(wl_w11_run() && starts==2 && cap_requests==1); /* one shot */
    contain();assert(stops==2);

    /* fuxiwifilinux=2: the Linux stack owns MHI/CE/HTC through nw_wifi_start(),
     * so step 28 must not start the old conversation, and keep=1 must leave the
     * endpoint standing -- that keep is what hands it over. */
    start(0,true);wl_linux_stack=true;
    assert(!finish_step() && wl_amss_reached && !starts && !cap_requests);
    assert(strstr(logbuf,"W11-W15 skipped"));
    before=ne;wl_finish(28);
    assert(ne==before && !frees && !stops && wl_ipcr.va && wl_contexts.va);
    wl_linux_stack=false;contain();

    start(9,false);assert(!finish_step());wl_finish(28);assert(frees==allocations && stops==2);dev_on=false;
    start(14,true);assert(!finish_step() && sleeps>=30);contain();
    for(unsigned kind=1;kind<=24;kind++) {
        if(kind==9 || kind==14 || kind==23)continue;
        start(kind,true);unsigned t=sleeps;
        assert(finish_step() && !wl_w11_ok);
        assert(starts<=2 && cap_requests<=1 && sleeps-t<=6000);
        if(kind==1)assert(strstr(logbuf,"START_CHAN completion") && starts==1);
        if(kind==4 || kind==5)assert(strstr(logbuf,"QRTR HELLO/WLFW server"));
        if(kind==6 || kind==11)assert(strstr(logbuf,"WLFW capability reply"));
        if(kind==13)assert(strstr(logbuf,"TX completion drain"));
        /* Restore a healthy link for STOP; a timed-out command still forces reset. */
        mode=0;setreg(WL_BAR0+0x128,2);setreg(wl_cb_addr(wl_cb)+0x58,0);fw_faults=0;
        contain();
    }

    start(0,false);assert(!finish_step());mode=23;before=sleeps;
    wl_finish(28);assert(frees==allocations && stops==1 && sleeps-before>=2000);
    assert(strstr(logbuf,"STOP_CHAN completion") && strstr(logbuf,"MHI reset/PERST follows"));dev_on=false;

    /* Exhaust and wrap both transfer rings and both event rings. */
    start(0,true);assert(!finish_step());
    for(unsigned i=0;i<300;i++) {
        dev_event(0,0,2u<<24,0x200000);dev_control(2,false);assert(!wl_w11_events());
        uint8_t data[20]={2};assert(!wl_qrtr_send(2,7,0xfffffffeu,data,20));assert(!wl_w11_events());
    }
    assert(wl_tx_read==wl_tx_write && dev_rx==wl_rx_read);
    for(unsigned ch=0;ch<128;ch++) if(ch!=20 && ch!=21)assert(!wl_channel(ch)->cfg && !wl_channel(ch)->base);
    /* A full software TX queue fails without touching the producer/doorbell. */
    dev_on=false;wl_tx_read=next(wl_tx_write,16);before=ne;
    uint8_t packet[32]={0};assert(wl_ipcr_send(packet,32) && ne==before);
    wl_tx_read=wl_tx_write;dev_on=true;clear_error();contain();

    /* Corrupted event pointers, event types, TRE ownership and lengths fail
     * before indexing or parsing device-controlled memory. */
    for(unsigned kind=0;kind<8;kind++) {
        start(0,true);assert(!finish_step());
        struct wl_context *ec=(void *)(wl_contexts.va+WL_ECTX);
        if(kind==0)ec->rp=ec->base+1;
        if(kind==1)ec->rp=ec->base+ec->len;
        if(kind>=2) {
            uint64_t ptr=wl_ipcr.iova+0x100+16*wl_rx_read;
            uint32_t d0=52|(2u<<24),d1=0x15220000;
            if(kind==2)ptr+=16;
            if(kind==3)d0=4097|(2u<<24);
            if(kind==4)d0=52|(3u<<24);
            if(kind==5)d1=0x16220000;
            if(kind==6)d1=0x15990000;
            if(kind==7)wl_ipcr_ring(0x100)[wl_rx_read].ptr=wl_iova_end;
            dev_event(1,ptr,d0,d1);
        }
        assert(wl_w11_events());dev_on=false;wl_chan_attempts=0;contain();
    }

    /* QRTR malformed framing and QMI TLV edge cases use real production
     * decoders. Unknown TLVs, stale transactions, and other destinations are safe. */
    start(0,true);assert(!finish_step());dev_on=false;
    for(unsigned cut=0;cut<7;cut++) {clear_error();assert(wl_qmi_cap(cap_reply,cut));}
    for(unsigned kind=0;kind<8;kind++) {
        uint8_t p[128];memcpy(p,cap_reply,sizeof(cap_reply));unsigned len=sizeof(cap_reply);
        if(kind==0)p[5]++;
        if(kind==1)p[8]=255;
        if(kind==2)p[46]=33; /* nested string length */
        if(kind==3){p[7]=0x15;} /* missing mandatory result */
        if(kind==4){memcpy(p+len,p+7,7);len+=7;put16(p+5,len-7);} /* duplicate result */
        if(kind==5){p[len++]=0x66;put16(p+5,len-7);} /* truncated TLV header */
        if(kind==6){p[11]=1;} /* result error */
        if(kind==7){p[53]=129;} /* oversized top-level build length */
        clear_error();assert(wl_qmi_cap(p,len));
    }
    uint8_t p[128];memcpy(p,cap_reply,sizeof(cap_reply));
    p[1]=2;clear_error();wl_cap_done=false;assert(!wl_qmi_cap(p,sizeof(cap_reply)) && !wl_cap_done);
    memcpy(p,cap_reply,sizeof(cap_reply));unsigned len=sizeof(cap_reply);p[len++]=0x70;p[len++]=1;p[len++]=0;p[len++]=7;put16(p+5,len-7);
    clear_error();assert(!wl_qmi_cap(p,len));
    uint8_t bad[64]={0};
    for(unsigned kind=0;kind<7;kind++) {
        memset(bad,0,sizeof(bad));put32(bad,1);put32(bad+4,2);put32(bad+8,7);
        put32(bad+12,0xfffffffeu);put32(bad+20,20);put32(bad+24,1);put32(bad+28,0xfffffffeu);put32(bad+32,2);
        if(kind==0)bad[0]=2;
        if(kind==1)put32(bad+20,~0u);
        if(kind==2)put32(bad+16,2);
        if(kind==3)put32(bad+32,3);
        if(kind==4)put32(bad+8,1);
        if(kind==6){bad[0]=3;bad[1]=2;bad[3]=1;}
        clear_error();assert(wl_qrtr_receive(bad,kind==5?51:52));
    }
    /* Other destinations and unsupported services never select a WLFW server. */
    put32(bad,1);put32(bad+4,2);put32(bad+8,7);put32(bad+12,0xfffffffeu);
    put32(bad+16,0);put32(bad+20,20);put32(bad+24,88);put32(bad+28,0xfffffffeu);put32(bad+32,2);
    clear_error();assert(!wl_qrtr_receive(bad,52));
    /* The capability reply cannot satisfy discovery before a request is sent. */
    wl_cap_sent=wl_cap_done=false;memset(bad,0,sizeof(bad));
    uint8_t early[128]={0};put32(early,1);put32(early+4,1);put32(early+8,7);put32(early+12,42);
    put32(early+20,sizeof(cap_reply));put32(early+24,1);put32(early+28,0x4000);memcpy(early+32,cap_reply,sizeof(cap_reply));
    assert(!wl_qrtr_receive(early,96) && !wl_cap_done);
    dev_on=true;contain();

    /* Bad preflight, missing map, allocation/NC conversion, partial stop,
     * no opt-in, integer exactness and failed-containment quarantine. */
    for(unsigned kind=0;kind<6;kind++) {
        start(0,true);
        if(kind==0)setreg(WL_BAR0+0x18,0x701);
        if(kind==1)setreg(WL_BAR0+0x58,0);
        if(kind==2){uint64_t addr=wl_ipcr.iova;unsigned n=(addr-wl_iova_base)>>21;((uint64_t *)(wl_tables.va+(2+n)*4096))[(addr>>12)&511]=0;}
        if(kind==3)((struct wl_context *)(wl_contexts.va+20*44))->cfg=1;
        if(kind==4)((struct wl_context *)(wl_contexts.va+0x1900))->len=0;
        if(kind==5)((struct wl_context *)(wl_contexts.va+0x1800))->len=0;
        assert(finish_step() && !starts);contain();
    }
    dev_on=false;setup(NULL);qrtr_arg=6;wl_probe_configure();assert(!wl_qrtr);
    setup(NULL);stop22_arg=0;qrtr_arg=1;wl_probe_configure();cache_drop=true;assert(run(20)==20);cache_drop=false;cleanup(20);
    setup(NULL);stop22_arg=0;qrtr_arg=1;wl_probe_configure();fail_nth_alloc=4;assert(run(20)==20);fail_nth_alloc=0;cleanup(20);
    start(0,true);wl_finish(27);assert(frees==allocations && !starts);dev_on=false;
    start(6,true);assert(finish_step());fail_bm=fail_perst=true;wl5_abort(28);
    assert(!frees && wl_ipcr.va && wl_contexts.va);fail_bm=fail_perst=false;contain();
    puts("W11 production MHI commands/events/credits/wrap, QRTR v1/compact, WLFW capability TLVs, bounded failures and containment passed");
    return 0;
}
'''


def main():
    assert 'python3 tools/test-fuxi-wlan-w11.py' in (ROOT / 'tools/ci/build.sh').read_text()
    w10.TESTS = TESTS
    preamble = w10.m6o.m6.PREAMBLE.replace('logbuf[131072]', 'logbuf[1048576]')
    preamble = preamble.replace('static void raw_pwr(', 'static void w11_write(uint64_t,uint32_t);\nstatic void raw_pwr(')
    # Base preamble's MMIO sink is raw_pwr, called by every production access.
    assert 'static void raw_pwr(' in preamble
    preamble = preamble.replace('    setreg(pa,v);\n    if (inject_fault', '    setreg(pa,v); w11_write(pa,v);\n    if (inject_fault')
    w10.m6o.m6.PREAMBLE = preamble
    w10.main()


if __name__ == '__main__':
    main()
