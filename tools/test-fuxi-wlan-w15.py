#!/usr/bin/env python3
"""W15 production C + independent WLFW peer, entirely host-side.

Expected wire facts adapted from Linux ath12k v6.12 qmi.c/qmi.h, hw.c,
ce.c/ce.h, hal.c/hal.h, htc.h (BSD-3-Clause-Clear). Copyright (c) 2018-2021
The Linux Foundation; (c) 2021-2024 Qualcomm Innovation Center, Inc.
No firmware fixtures, Linux helpers or device access.
"""
from pathlib import Path
import importlib.util
import struct

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('w14', ROOT / 'tools/test-fuxi-wlan-w14.py')
w14 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w14)


def tlv(kind, value):
    return struct.pack('<BH', kind, len(value)) + value


def array(rows):
    return bytes([len(rows)]) + b''.join(struct.pack('<' + 'I' * len(row), *row) for row in rows)


def expected_requests():
    # qmi_wlanfw_ce_tgt_pipe_cfg_s_v01_ei sends five 32-bit words, omitting
    # ce_pipe_config.reserved. Independently describe hw.c's nine pipes.
    directions = [2, 1, 1, 2, 2, 1, 3, 4, 3]
    counts = [32, 32, 32, 32, 256, 32, 32, 0, 32]
    sizes = [2048, 2048, 2048, 2048, 256, 2048, 16384, 0, 16384]
    target = [(pipe, directions[pipe], counts[pipe], sizes[pipe],
               8 if pipe in (4, 7) else 0) for pipe in range(9)]
    # htc.h SVC(group,index): WMI VO/BK/BE/VI/control, RSVD, HTT.
    service = []
    for group, index, uplink, downlink in [(1, 4, 3, 2), (1, 2, 3, 2),
                                         (1, 1, 3, 2), (1, 3, 3, 2),
                                         (1, 0, 3, 2), (0, 1, 0, 2),
                                         (3, 0, 4, 1)]:
        service += [((group << 8) | index, 2, uplink),
                    ((group << 8) | index, 1, downlink)]
    # hal enum order and WCN7850 overrides: eight REO DST, exception,
    # one reinject, command/status, five TCL data, command/status,
    # WBM idle, one SW2WBM, eight WBM2SW. Destination rings use TP=HP+4.
    shadow = []
    for base, hp, count, stride, destination in [
            (0xa38000, 0x3048, 8, 8, True),
            (0xa38000, 0x3088, 1, 0, True),
            (0xa38000, 0x3028, 1, 0, False),
            (0xa38000, 0x3020, 1, 0, False),
            (0xa38000, 0x30a8, 1, 0, True),
            (0xa44000, 0x2000, 5, 8, False),
            (0xa44000, 0x2028, 1, 0, False),
            (0xa44000, 0x2048, 1, 0, True),
            (0xa34000, 0x30b8, 1, 0, False),
            (0xa34000, 0x3010, 1, 0, False),
            (0xa34000, 0x30c8, 8, 8, True)]:
        shadow += [base + hp + i * stride + (4 if destination else 0)
                   for i in range(count)]
    assert len(shadow) == 29
    # ce.c host entries (not target table): src 0/3/4, dst 1/2 only.
    for ce in range(9):
        if ce in (0, 3, 4):
            shadow.append(0x1b80000 + ce * 0x2000 + 0x400)
        if ce in (1, 2):
            shadow += [0x1b81000 + ce * 0x2000 + 0x400,
                       0x1b81000 + ce * 0x2000 + 0x408 + 4]
    assert len(shadow) == 36
    # Top-level QMI_STRING is TLV length delimited: no NUL or count byte.
    cfg = tlv(0x10, b'WIN') + tlv(0x11, array(target))
    cfg += tlv(0x12, array(service)) + tlv(0x17, array([(x,) for x in shadow]))
    bodies = [tlv(0x10, b'\x01'), cfg, tlv(1, struct.pack('<I', 0)) + tlv(0x10, b'\0')]
    return [struct.pack('<BHHH', 0, txn, msg, len(body)) + body
            for txn, msg, body in zip((13, 14, 15), (0x2f, 0x23, 0x22), bodies)]


def fixture_declarations():
    return '\n'.join('static const uint8_t golden%d[]={%s};' %
                     (i, ','.join(str(b) for b in request))
                     for i, request in enumerate(expected_requests()))


PEER = w14.PEER.replace('qrtr_arg=4;', 'qrtr_arg=5;')
PEER = PEER.replace('static unsigned trial, responses, snapshot_start, dl[2], m3_requests;', r'''
static unsigned trial, responses, snapshot_start, dl[2], m3_requests;
static unsigned start_case, seen15, mode_ticks, observe_ticks, late_indications;
static unsigned start_allocations, start_event, fwready_ticks;
''' + fixture_declarations())
PEER = PEER.replace('    if(msg==0x25) {', r'''
    if(msg==0x2f || msg==0x23 || msg==0x22) {
        const uint8_t *golden[]={golden0,golden1,golden2};
        const unsigned lengths[]={sizeof(golden0),sizeof(golden1),sizeof(golden2)};
        unsigned step=seen15++;
        assert(step<3 && wl14_ready && m3_requests==1);
        assert(len==lengths[step] && !memcmp(q,golden[step],len));
        if(step==0){start_allocations=allocations;start_event=ne;fwready_ticks=sleeps;}
        if(step==1) {
            /* Independently decode every CFG TLV, count and packed stride. */
            unsigned off=7;const unsigned ids[]={0x10,0x11,0x12,0x17};
            const unsigned counts[]={0,9,14,36}, strides[]={0,20,12,4};
            for(unsigned i=0;i<4;i++) {
                assert(q[off]==ids[i]);unsigned n=q[off+1]|q[off+2]<<8;off+=3;
                assert(n==(!i?3:1+counts[i]*strides[i]));
                if(i)assert(q[off]==counts[i]);else assert(!memcmp(q+off,"WIN",3));
                off+=n;
            }
            assert(off==len);
        }
        unsigned kind=start_case%10, fail_step=start_case/10;
        if(start_case && step==fail_step) {
            if(kind==1){reply(txn,msg,true);return;}
            if(kind==2)return; /* no response: exactly one 10-second wait */
            uint8_t p[]={2,0,0,0,0,7,0,2,4,0,0,0,0,0};
            put16(p+1,txn);put16(p+3,msg);
            if(kind==3)p[7]=0x71; /* missing result */
            if(kind==4)p[8]=5; /* truncated result value */
            if(kind==5){p[12]=90;} /* error with success result */
            if(kind==6) {
                uint8_t duplicate[21];memcpy(duplicate,p,14);memcpy(duplicate+14,p+7,7);
                put16(duplicate+5,14);dev_qrtr(1,duplicate,21,false,false);return;
            }
            if(kind==7) { /* unrelated txn/message must not advance */
                reply(txn-1,msg,false);reply(txn,msg+1,false);
                ind(0x7777,NULL,0);return;
            }
            if(kind>=3 && kind<=5){dev_qrtr(1,p,14,false,false);return;}
        }
        ind(0x7777,NULL,0); /* indication before response never completes it */
        reply(txn,msg,false);
        if(step==2)mode_ticks=sleeps;
        return;
    }
    if(msg==0x25) {''')
# Inject indications at 10 ms, 1 s and precisely 2 s after MODE (including
# duplicate provisioning/readiness); no follow-up request is permitted.
PEER = PEER.replace('    dev_work();\n}\nstatic void start(', r'''
    if(seen15==3 && wl12_stage==WL15_OBSERVE) {
        observe_ticks++;
        if(observe_ticks==1 || observe_ticks==100 || observe_ticks==200) {
            ind(observe_ticks==1?0x35:observe_ticks==100?0x38:0x7777,NULL,0);
            late_indications++;
        }
        if(start_case==30 && observe_ticks==3)crash_regs(3,0xff05);
        if(start_case==40 && observe_ticks==3)crash_regs(2,0xff05);
    }
    dev_work();
}
static void start(''')

TESTS = PEER + r'''
static void boot15(unsigned c,bool keep) {
    io_case=0;trial=0;responses=dl[0]=dl[1]=m3_requests=0;
    start_case=c;seen15=observe_ticks=late_indications=0;mode_ticks=0;
    begin(0,keep);assert(wl_w15 && wl_w14 && wl_w13 && wl_w12 && wl_qrtr);
    target_cursor=wl_iova_next;
}
static void check_no_ce(void) {
    assert(allocations==start_allocations);
    /* No CE/non-CE ring register programming or memory freeing after INI.
     * The only writes are normal MHI IPCR doorbells and event credits. */
    for(unsigned i=start_event;i<ne;i++) {
        assert(events[i].kind!='A' && events[i].kind!='F');
        if(events[i].kind!='W')continue;
        uint64_t p=events[i].pa;
        assert(p==WL_BAR0+0x3a0 || p==WL_BAR0+0x3a4 ||
               p==WL_BAR0+0x3a8 || p==WL_BAR0+0x3ac ||
               p==WL_BAR0+0x700 || p==WL_BAR0+0x704 ||
               p==WL_BAR0+0x708 || p==WL_BAR0+0x70c);
    }
}
int main(void) {
    /* Selector lattice: legacy values never enable W15. Their complete
     * wire/stop/containment behavior is also exercised by W10..W14 suites. */
    for(int arg=0;arg<=6;arg++) {
        qrtr_arg=arg;wl_probe_configure();
        assert(wl_w15==(arg==5));assert(wl_w14==(arg==4||arg==5));
        assert(wl_w13==(arg>=3&&arg<=5));assert(wl_w12==(arg>=2&&arg<=5));
        assert(wl_qrtr==(arg>=1&&arg<=5));
    }
    for(unsigned keep=0;keep<2;keep++) {
        boot15(0,keep);int error=finish_step();if(error)fprintf(stderr,"%s",logbuf);
        assert(!error && wl_w11_ok && wl12_txn==15 && seen15==3);
        assert(observe_ticks==200 && late_indications==3 && sleeps-mode_ticks==2000);
        assert(strstr(logbuf,"W15 wlan ini ok") && strstr(logbuf,"W15 wlan cfg ok"));
        assert(strstr(logbuf,"W15 wlan mode normal ok; stop before CE/HTC"));
        assert(strstr(logbuf,"W15 indication msg 0x35") && strstr(logbuf,"W15 indication msg 0x38"));
        assert(strstr(logbuf,"W15 WLAN start diagnostic BHI_EXECENV 0x2"));
        assert(!strstr(logbuf,"stop before WLAN config/mode"));
        check_no_ce();
        /* Shared diagnostic itself is six reads and absolutely no writes. */
        unsigned before=ne;track_reads=true;nreads=0;wl_wlfw_diagnostics("W15 test");track_reads=false;
        assert(ne==before && nreads==6);
        const unsigned offsets[]={0x128,0x48,0x130,0x134,0x138,0x13c};
        for(unsigned i=0;i<6;i++)assert(reads[i]==WL_BAR0+offsets[i]);
        assert(strstr(logbuf,"ms since FW_READY: INI send"));
        assert(strstr(logbuf,"ms since FW_READY: CFG reply"));
        assert(strstr(logbuf,"ms since FW_READY: MODE reply"));
        /* Queue a reply and indication, but never consume/credit them here. */
        wl12_stage=WL15_MODE;
        reply(wl12_txn,0x22,false);ind(0x7777,NULL,0);
        uint8_t saved_contexts[16384];memcpy(saved_contexts,(void *)wl_contexts.va,16384);
        uint8_t *saved_ipcr=malloc(wl_ipcr.size);assert(saved_ipcr);
        memcpy(saved_ipcr,(void *)wl_ipcr.va,wl_ipcr.size);
        before=ne;track_reads=true;nreads=0;wl15_timeout_snapshot();track_reads=false;
        assert(ne==before && nreads==9);
        assert(!memcmp(saved_contexts,(void *)wl_contexts.va,16384));
        assert(!memcmp(saved_ipcr,(void *)wl_ipcr.va,wl_ipcr.size));free(saved_ipcr);
        assert(strstr(logbuf,"pending RX QMI type 0x2 txn 15 msg 0x22 bytes 14"));
        assert(strstr(logbuf,"pending RX QMI type 0x4 txn 0 msg 0x7777 bytes 7"));
        assert(strstr(logbuf,"pending RX QMI headers 2 (snapshot only)"));
        assert(strstr(logbuf,"BHIE_TXVECSTATUS") && strstr(logbuf,"BHIE_RXVECSTATUS"));
        for(unsigned i=0;i<6;i++)assert(reads[i]==WL_BAR0+offsets[i]);
        assert(reads[6]==WL_BAR0+0x38 && reads[7]==WL_BAR0+wl_bhie+0x44 && reads[8]==WL_BAR0+wl_bhie+0x78);
        wl12_stage=WL15_OBSERVE;
        assert(!wl_w11_events());before=ne;
        if(keep){wl_finish(28);assert(ne==before && !frees);contain();}
        else {wl_finish(28);assert(frees==allocations);dev_on=false;}
    }
    /* Supplied device shape: only ath12k ready 0x38, and omitted board ID.
     * Failed M3 or ath11k 0x21 alone must never reach WLAN_INI. */
    const unsigned inherited[]={9,10,11,12,21};
    for(unsigned i=0;i<5;i++) {
        boot15(0,true);io_case=inherited[i];int error=finish_step();
        if(inherited[i]==12 || inherited[i]==21) {
            assert(!error && wl_w11_ok && seen15==3);check_no_ce();
            if(inherited[i]==12)assert(!wl14_init_done);
            else assert(!wl14_board_valid);
        } else {assert(error && !wl_w11_ok && !seen15);}
        contain();
    }
    for(unsigned step=0;step<3;step++)for(unsigned kind=1;kind<=7;kind++) {
        boot15(10*step+kind,true);assert(finish_step() && !wl_w11_ok);
        assert(seen15==step+1 && wl12_txn==13+step && !observe_ticks);
        assert(strstr(logbuf,"W15 failure:") && strstr(logbuf,"contain, no retry"));
        assert(strstr(logbuf,"W15 WLAN start diagnostic"));
        if(kind==2||kind==7) {
            assert(sleeps-fwready_ticks==10000 && strstr(logbuf,"(10000 ms)"));
            if(step==2) {
                assert(strstr(logbuf,"W15 elapsed 10000 ms since FW_READY: MODE timeout"));
                assert(strstr(logbuf,"pending QMI txn 15 msg 0x22 reply 0 cmd_pending 0"));
                assert(strstr(logbuf,"pending RX QMI headers 0 (snapshot only)"));
            } else assert(!strstr(logbuf,"BHIE_TXVECSTATUS"));
        }
        if(kind==1||kind==5)assert(strstr(logbuf,"error 90 (INCOMPATIBLE_STATE)"));
        check_no_ce();contain();
    }
    /* Crashes during observation are captured before containment. */
    for(unsigned c=30;c<=40;c+=10) {
        boot15(c,true);assert(finish_step() && !wl_w11_ok && seen15==3);
        assert(strstr(logbuf,"BHI_ERRCODE 0xcafecace BHI_ERRDBG1 0xcafecace"));
        assert(strstr(logbuf,"BHI_ERRDBG2 0x14feeb3 BHI_ERRDBG3 0x496"));
        if(c==30)assert(strstr(logbuf,"W15 RDDM observed; dump not collected; contain"));
        check_no_ce();contain();
    }
    /* Keep never bypasses quarantine if containment itself fails. */
    boot15(21,true);assert(finish_step());fail_bm=fail_perst=true;
    wl5_abort(28);assert(!frees && wl14_m3.va && wl_target[0].va);
    fail_bm=fail_perst=false;contain();
    puts("W15 INI/CFG/MODE exact EI bytes/counts, 36 computed shadows, legacy selectors, 21 failures, 2 s indications, read-only diagnostics and quarantine passed");
    return 0;
}
'''


def main():
    assert 'python3 tools/test-fuxi-wlan-w15.py' in (ROOT / 'tools/ci/build.sh').read_text()
    m6 = w14.w12.w11.w10.m6o.m6
    m6.PREAMBLE = m6.PREAMBLE.replace('return 0x12345; }', 'return (uint64_t)sleeps*1000000ull; }')
    w14.TESTS = TESTS
    w14.main()


if __name__ == '__main__':
    main()
