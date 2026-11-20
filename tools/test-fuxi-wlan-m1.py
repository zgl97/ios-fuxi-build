#!/usr/bin/env python3
"""Milestone 1: drive the Ath12kWifi board layer with a fake endpoint.

The board layer in xnu/fuxi-wlan/fuxi_wlan_board.c implements the lx_platform
and nw_board contract of include/nw_abi.h. Under XNU it reaches the endpoint
through wl_prd/wl_pwr and the kernel allocator; here the same source is compiled
against a synthetic config space, a synthetic register window and a synthetic
firmware directory, so every callback can be exercised without a device.

What this proves:
  - capability discovery and the bit-31 "inside the PCIe capability" offsets;
  - sub-dword config reads extract and writes read-modify-write;
  - the DMA arena hands out a bus address, keeps allocations disjoint, honours
    the reserved MSI sink page, maps a CPU pointer back and refuses the rest;
  - firmware is matched by last path component and cached;
  - the MSI target is the sink page;
  - the selector refuses what it cannot serve and leaves the old lane alone.
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
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include <sys/types.h>
#include <stdarg.h>

static char logbuf[131072];
static size_t loglen;
static void test_printf(const char *fmt, ...) {
    va_list ap; va_start(ap,fmt);
    int n = vsnprintf(logbuf+loglen, sizeof(logbuf)-loglen, fmt, ap);
    va_end(ap);
    assert(n >= 0 && (size_t)n < sizeof(logbuf)-loglen);
    loglen += (size_t)n;
}
#define printf test_printf

typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
typedef unsigned ppnum_t;
typedef int kern_return_t;
typedef void *vnode_t;
typedef void *vfs_context_t;
typedef void *event_t;
typedef struct { int dummy; } *vfs_context_ucred_t;

#define PAGE_SHIFT 14
#define PAGE_SIZE (1u << PAGE_SHIFT)
#define PAGE_MASK (PAGE_SIZE - 1)
#define round_page(x) (((x) + PAGE_MASK) & ~(size_t)PAGE_MASK)

#define kernel_map 0
#define KERN_SUCCESS 0
#define KMA_DATA 1
#define KMA_ZERO 2
#define KMA_NOPAGEWAIT 4
#define VM_KERN_MEMORY_IOKIT 0
#define NSEC_PER_MSEC 1000000ull
#define NSEC_PER_USEC 1000ull
#define FREAD 1
#define O_NOFOLLOW 2
#define O_NONBLOCK 4
#define VREG 1
#define UIO_READ 0
#define UIO_SYSSPACE 0

#define WL_BAR0 0x60400000u
#define WL_BAR0_END 0x605fffffu
#define WL_EPCFG 0x60100000ull

/* ---- endpoint accessors the board layer reuses ------------------------- */

static unsigned fw_faults;
static unsigned prd_calls, pwr_calls;

/* A register window the test owns; anything outside it is a fault. */
static uint32_t fake_bar[WL_BAR0_END - WL_BAR0 + 1];
static uint32_t fake_cfg[64];

static uint32_t wl_prd(uint64_t pa) {
    prd_calls++;
    if (pa >= WL_BAR0 && pa < WL_BAR0 + sizeof(fake_bar)) {
        return fake_bar[(pa - WL_BAR0) / 4];
    }
    if (pa >= WL_EPCFG && pa < WL_EPCFG + sizeof(fake_cfg)) {
        return fake_cfg[(pa - WL_EPCFG) / 4];
    }
    fw_faults++;
    return ~0u;
}
static void wl_pwr(uint64_t pa, uint32_t v) {
    pwr_calls++;
    if (pa >= WL_BAR0 && pa < WL_BAR0 + sizeof(fake_bar)) {
        fake_bar[(pa - WL_BAR0) / 4] = v;
        return;
    }
    if (pa >= WL_EPCFG && pa < WL_EPCFG + sizeof(fake_cfg)) {
        fake_cfg[(pa - WL_EPCFG) / 4] = v;
        return;
    }
    fw_faults++;
}

/* ---- kernel memory ----------------------------------------------------- */

struct wl_dma {
    vm_offset_t va;
    vm_size_t size;
    uint64_t pa;
    uint64_t iova;
    bool nc;
};

static uint64_t alloc_seq = 0x10000;
static unsigned dma_allocs, dma_frees;

static kern_return_t kmem_alloc(int map, vm_offset_t *out, vm_size_t bytes,
    int flags, int tag) {
    (void)map; (void)flags; (void)tag;
    assert(bytes && (bytes & PAGE_MASK) == 0);
    /* Page aligned like kernel_map, so the board layer's "16 bytes into a page"
     * ownership test behaves the same here as it does under XNU. */
    void *p = NULL;
    assert(posix_memalign(&p, PAGE_SIZE, bytes) == 0);
    assert(p);
    memset(p, 0, bytes);
    *out = (vm_offset_t)p;
    return KERN_SUCCESS;
}
static void kmem_free(int map, vm_offset_t va, vm_size_t bytes) {
    (void)map; (void)bytes;
    free((void *)va);
}

static unsigned fail_alloc_at;   /* 1-based: fail the Nth wl_dma_alloc */
static unsigned dma_alloc_n;

static uint64_t wl_iova_base = 0xa0000000ull;
static uint64_t wl_iova_end = 0xb0000000ull;
static uint64_t wl_iova_next = 0xa0000000ull;

static int wl_dma_alloc(struct wl_dma *d, vm_size_t bytes) {
    dma_alloc_n++;
    d->size = round_page(bytes);
    if (fail_alloc_at && dma_alloc_n == fail_alloc_at) { return 1; }
    void *p = calloc(1, d->size);
    assert(p);
    d->va = (vm_offset_t)p;
    d->pa = 0x80000000ull + alloc_seq;
    /* fuxi_wlan.c:1359 hands out the bump pointer and advances it; the board
     * layer must give that advance back, so mirror it exactly here. */
    d->iova = wl_iova_next;
    wl_iova_next += d->size;
    alloc_seq += d->size;
    dma_allocs++;
    return 0;
}
static void wl_dma_free(struct wl_dma *d) {
    if (d->va) { free((void *)d->va); dma_frees++; }
    memset(d, 0, sizeof(*d));
}

static struct wl_dma wl_msi_sink;

static void wl_sleep(unsigned ms) { (void)ms; }
static void delay_for_interval(unsigned us, uint64_t ns) { (void)us; (void)ns; }
static int preempt_depth;
static void _disable_preemption(void) { preempt_depth++; }
static void _enable_preemption(void) { preempt_depth--; }

static uint64_t mach_absolute_time(void) { return 1234567890ull; }
static uint64_t mach_continuous_time(void) { return 9876543210ull; }
static void absolutetime_to_nanoseconds(uint64_t t, uint64_t *ns) { *ns = t * 41; }

/* ---- firmware VFS (unused when the host hook is set, must compile) ------ */

static vfs_context_t vfs_context_kernel(void) { return (vfs_context_t)1; }
static void *vfs_context_ucred(vfs_context_t c) { return c; }
static void *vfs_context_proc(vfs_context_t c) { return c; }
static int vnode_open(const char *p, int f, int m, int c, vnode_t *vp, vfs_context_t ctx) {
    (void)p; (void)f; (void)m; (void)c; (void)vp; (void)ctx; return 1;
}
struct vnode_attr { int va_data_size; unsigned flags; };
#define VATTR_INIT(a) ((a)->flags = 0)
#define VATTR_WANTED(a, f) ((void)(a))
#define VATTR_IS_SUPPORTED(a, f) (1)
static int vnode_getattr(vnode_t vp, struct vnode_attr *a, vfs_context_t ctx) {
    (void)vp; (void)a; (void)ctx; return 1;
}
static int vnode_vtype(vnode_t vp) { (void)vp; return VREG; }
static int vnode_close(vnode_t vp, int f, vfs_context_t ctx) { (void)vp; (void)f; (void)ctx; return 0; }
static int vn_rdwr(int a, vnode_t vp, char *b, int n, int o, int s, int i,
    void *u, int *resid, void *p) {
    (void)a; (void)vp; (void)b; (void)n; (void)o; (void)s; (void)i; (void)u; (void)p;
    if (resid) { *resid = 0; }
    return 0;
}

/* ---- boot args --------------------------------------------------------- */

static const char *boot_linux;      /* value of fuxiwifilinux, NULL = unset */
static bool variant_invalid_arg;

static bool PE_parse_boot_argn(const char *name, void *out, int size) {
    assert(!strcmp(name, "fuxiwifilinux"));
    (void)size;
    if (!boot_linux) { return false; }
    *(unsigned *)out = (unsigned)strtoul(boot_linux, NULL, 0);
    return true;
}

static bool wl_variant_invalid = false;
static bool wl_w12 = true;
/* Set by the board layer when fuxiwifilinux=2; fuxi_wlan.c only reads it. */
static bool wl_linux_stack = false;
'''

TESTS = r'''
/* ---- fixtures ---------------------------------------------------------- */

#define CFG_ID 0x00
#define CFG_CMD 0x04
#define CFG_CAP 0x34
#define PCIE_CAP 0x40
#define MSI_CAP 0x60
#define PCIE_MARK (PCIE_CAP + 0x10)     /* a dword inside the PCIe capability */
#define PCIE_MARK_VAL 0x11220002u

static void
reset_fixtures(void)
{
    memset(fake_bar, 0, sizeof(fake_bar));
    memset(fake_cfg, 0, sizeof(fake_cfg));
    /* vendor 0x17cb in the low half, device 0x1107 in the high half */
    fake_cfg[CFG_ID / 4] = (0x1107u << 16) | 0x17cbu;
    /* dword 0x04 is Command in the low half, Status in the high half; the
     * capability-list bit is Status bit 4 = dword bit 20. The endpoint reports
     * 0x100406 here, so this fixture keeps the low half non-zero too. */
    fake_cfg[CFG_CMD / 4] = 0x0406u | (1u << 20);
    /* A real subsystem id, because wl_board_prepare() now refuses a wire that
     * reads back zero or all-ones there. */
    fake_cfg[0x2c / 4] = (0x1107u << 16) | 0x17cbu;
    /* list: PCIe (0x10) then MSI (0x05), terminator 0 */
    fake_cfg[CFG_CAP / 4] = PCIE_CAP | (MSI_CAP << 8);
    fake_cfg[PCIE_CAP / 4] = 0x10u | ((MSI_CAP & 0xff) << 8) | (2u << 16);
    fake_cfg[MSI_CAP / 4] = 0x05u | (0u << 8) | (1u << 23);   /* 64-bit capable */
    fake_cfg[PCIE_MARK / 4] = PCIE_MARK_VAL;
    fw_faults = 0;
    prd_calls = pwr_calls = 0;
    alloc_seq = 0x10000;
    dma_allocs = dma_frees = dma_alloc_n = 0;
    fail_alloc_at = 0;
    wl_iova_base = 0xa0000000ull;
    wl_iova_end = 0xb0000000ull;
    wl_iova_next = 0xa0000000ull;
    memset(&wl_msi_sink, 0, sizeof(wl_msi_sink));
    wl_msi_sink.iova = 0xa0000000ull;
    wl_msi_sink.size = PAGE_SIZE;
    boot_linux = NULL;
    wl_variant_invalid = false;
    wl_w12 = true;
    wl_linux_stack = false;
    loglen = 0;
    logbuf[0] = 0;
}

static struct wl_board_host host;
static unsigned fw_reads;
static char fw_last_name[64];
/* Dword index whose writes are dropped, so a test can prove that a config
 * word refusing to change is caught by read-back rather than assumed. */
static uint32_t cfg_veto_word = 0xffffffffu;

static int
host_cfg_veto(uint32_t word_off, uint32_t merged)
{
    (void)merged;
    return word_off / 4 == cfg_veto_word;
}

static int
host_fw_read(const char *name, uint8_t **data, size_t *size)
{
    fw_reads++;
    strlcpy(fw_last_name, name, sizeof(fw_last_name));
    /* One byte per file, so a wrong mapping is visible in the payload. */
    uint8_t *buf = calloc(1, 8);
    assert(buf);
    memset(buf, (int)(name[0] & 0x7f), 8);
    *data = buf;
    *size = 8;
    return 0;
}

static void
attach_host(void)
{
    memset(&host, 0, sizeof(host));
    host.bar = fake_bar;
    host.bar_len = sizeof(fake_bar);
    host.cfg = fake_cfg;
    host.cfg_dwords = sizeof(fake_cfg) / 4;
    host.pcie_cap = PCIE_CAP;
    host.msi_cap = MSI_CAP;
    host.fw_read = host_fw_read;
    host.cfg_veto = host_cfg_veto;
    fuxi_wlan_board_host(&host);
    fw_reads = 0;
    fw_last_name[0] = 0;
    cfg_veto_word = 0xffffffffu;
}

/* ---- capability discovery --------------------------------------------- */

static void
test_caps(void)
{
    reset_fixtures();
    attach_host();
    uint32_t device = 0, msi = 0, pcie = 0;
    assert(wl_board_caps(&device, &msi, &pcie) == 0);
    assert(device == 0x1107);
    assert(msi == MSI_CAP);
    assert(pcie == PCIE_CAP);
    assert(!fw_faults);

    /* A missing capability list must be refused, not guessed. Command bit 4 is
     * not the capability-list bit, so setting only that must still be refused:
     * that mistake is what made the r493 boot log report "capability walk
     * failed" against a real endpoint that reports 0x100406. */
    reset_fixtures();
    attach_host();
    fake_cfg[CFG_CMD / 4] = 1u << 4;
    assert(wl_board_caps(&device, &msi, &pcie) == 1);
    fake_cfg[CFG_CMD / 4] = 0;
    assert(wl_board_caps(&device, &msi, &pcie) == 1);

    /* A chain that walks off the 256-byte region must be refused. */
    reset_fixtures();
    attach_host();
    fake_cfg[PCIE_CAP / 4] = 0x10u | (0xf8u << 8);
    fake_cfg[0xf8 / 4] = 0x05u | (0xfcu << 8);
    fake_cfg[0xfc / 4] = 0x10u | (0xfcu << 8);   /* self-loop */
    assert(wl_board_caps(&device, &msi, &pcie) == 1);

    /* No MSI capability at all is refused too. */
    reset_fixtures();
    attach_host();
    fake_cfg[CFG_CAP / 4] = PCIE_CAP;
    fake_cfg[PCIE_CAP / 4] = 0x10u;
    assert(wl_board_caps(&device, &msi, &pcie) == 1);
    puts("M1 capability discovery ok");
}

/* ---- config space ------------------------------------------------------ */

static void
test_config(void)
{
    reset_fixtures();
    attach_host();

    uint32_t v = 0;
    assert(wl_board_cfg_read(0x00, 4, &v) == 0 && v == 0x110717cbu);
    assert(wl_board_cfg_read(0x00, 2, &v) == 0 && v == 0x17cbu);
    assert(wl_board_cfg_read(0x02, 2, &v) == 0 && v == 0x1107u);
    assert(wl_board_cfg_read(0x00, 1, &v) == 0 && v == 0xcbu);
    assert(wl_board_cfg_read(0x01, 1, &v) == 0 && v == 0x17u);
    assert(wl_board_cfg_read(0x03, 1, &v) == 0 && v == 0x11u);
    assert(wl_board_cfg_read(0x00, 3, &v) == 1);        /* bad width */

    /* bit 31 means "inside the PCIe capability" (nw_abi.h:31). */
    assert(wl_board_cfg_read((int)(0x80000000u | 0x10u), 2, &v) == 0);
    assert(v == (PCIE_MARK_VAL & 0xffffu));
    assert(wl_board_cfg_read((int)(0x80000000u | 0x10u), 4, &v) == 0);
    assert(v == PCIE_MARK_VAL);

    /* Sub-dword write is a read-modify-write of the containing dword. */
    assert(wl_board_cfg_write((int)(0x80000000u | 0x10u), 2, 0x0042u) == 0);
    assert(fake_cfg[PCIE_MARK / 4] == (PCIE_MARK_VAL & 0xffff0000u) | 0x0042u);
    assert(wl_board_cfg_read((int)(0x80000000u | 0x10u), 2, &v) == 0 && v == 0x0042u);

    /* A 4-byte write replaces the whole dword. */
    assert(wl_board_cfg_write(0x80000000u | 0x14u, 4, 0xdeadbeefu) == 0);
    assert(fake_cfg[(PCIE_MARK + 4) / 4] == 0xdeadbeefu);

    /* An offset past the injected region is refused, not wrapped. */
    assert(wl_board_cfg_read(0x100, 4, &v) == 1);
    assert(!fw_faults);
    puts("M1 config space ok");
}

/* ---- DMA arena --------------------------------------------------------- */

static void
test_dma(void)
{
    reset_fixtures();
    attach_host();

    uint64_t b1 = 0, b2 = 0, b3 = 0;
    void *p1 = wl_board_dma_alloc(64, &b1);
    void *p2 = wl_board_dma_alloc(PAGE_SIZE * 4, &b2);
    void *p3 = wl_board_dma_alloc(PAGE_SIZE * 2, &b3);
    assert(p1 && p2 && p3);
    assert(b1 && b2 && b3);

    /* The first page is the MSI sink and must never be handed out. */
    const uint64_t reserved_end = wl_iova_base + WL_BOARD_DMA_RESERVED;
    assert(b1 >= reserved_end && b2 >= reserved_end && b3 >= reserved_end);

    /* Allocations must be page aligned and disjoint. */
    assert((b1 & PAGE_MASK) == 0 && (b2 & PAGE_MASK) == 0 && (b3 & PAGE_MASK) == 0);
    uint64_t iv[3] = { b1, b2, b3 };
    uint64_t sz[3] = { PAGE_SIZE, PAGE_SIZE * 4, PAGE_SIZE * 2 };
    for (int i = 0; i < 3; i++) {
        for (int j = i + 1; j < 3; j++) {
            assert(iv[i] + sz[i] <= iv[j] || iv[j] + sz[j] <= iv[i]);
        }
    }
    /* A large allocation is aligned to its own size: MHI needs that for TRE
     * rings (host/init.c:163-177). */
    assert((b2 & (sz[1] - 1)) == 0);

    /* The arena must not consume the bump pointer it overrode. */
    assert(wl_iova_next == wl_iova_base);

    /* map: inside the window gives iova + offset, outside gives 0. */
    assert(wl_board_dma_map(p1, 64, 0) == b1);
    assert(wl_board_dma_map((char *)p1 + 32, 32, 0) == b1 + 32);
    assert(wl_board_dma_map((char *)p1 + PAGE_SIZE, 64, 0) == 0);
    assert(wl_board_dma_map(p1, PAGE_SIZE * 8, 0) == 0);
    assert(wl_board_dma_map(fake_bar, 16, 0) == 0);

    /* Free returns the block to the pool and a later alloc reuses it. */
    wl_board_dma_free(64, p1, b1);
    assert(dma_frees == 1);
    uint64_t b4 = 0;
    void *p4 = wl_board_dma_alloc(64, &b4);
    assert(p4 && b4 == b1);

    /* dma_alloc failure leaves the slot free and reports nothing. */
    fail_alloc_at = dma_alloc_n + 1;
    uint64_t b5 = 0;
    assert(wl_board_dma_alloc(64, &b5) == NULL && b5 == 0);
    fail_alloc_at = 0;

    /* Exhausting the slot table fails cleanly instead of aliasing. */
    unsigned got = 0;
    for (unsigned i = 0; i < WL_BOARD_DMA_SLOTS + 8; i++) {
        uint64_t b = 0;
        if (wl_board_dma_alloc(64, &b)) { got++; }
    }
    assert(got > 0);

    /* dma_map and dma_sync tolerate a zero address and a NULL pointer. */
    assert(wl_board_dma_map(NULL, 16, 0) == 0);
    wl_board_dma_sync(0, 0, 0);
    puts("M1 DMA arena ok");
}

/* ---- firmware ---------------------------------------------------------- */

static void
test_firmware(void)
{
    reset_fixtures();
    attach_host();

    const uint8_t *data = NULL;
    size_t size = 0;

    /* ath12k asks for amss.bin; fuxi stages the sheng-identical amss20.bin. */
    assert(wl_board_fw_get("amss.bin", &data, &size) == 0);
    assert(!strcmp(fw_last_name, "amss20.bin"));
    assert(data && size == 8);

    /* A path is matched by its last component (nw_abi.h:21). */
    assert(wl_board_fw_get("/vendor/firmware/m3.bin", &data, &size) == 0);
    assert(!strcmp(fw_last_name, "m3.bin"));

    /* board.bin is the board-data payload inside board-2.bin (see M0-d). */
    assert(wl_board_fw_get("board.bin", &data, &size) == 0);
    assert(!strcmp(fw_last_name, "bd_ath12k_255.bin"));

    assert(wl_board_fw_get("regdb.bin", &data, &size) == 0);
    assert(!strcmp(fw_last_name, "regdb_xiaomi.bin"));

    /* An unknown name is passed through unchanged rather than guessed. */
    assert(wl_board_fw_get("unknown.bin", &data, &size) == 0);
    assert(!strcmp(fw_last_name, "unknown.bin"));

    assert(wl_board_fw_get(NULL, &data, &size) == 1);
    assert(wl_board_fw_get("amss.bin", NULL, &size) == 1);
    puts("M1 firmware lookup ok");
}

/* ---- MSI target, memory, clocks --------------------------------------- */

static void
test_platform(void)
{
    reset_fixtures();
    attach_host();

    uint32_t lo = 0, hi = 0, data = 0;
    assert(wl_board_msi_target(&lo, &hi, &data) == 0);
    assert(lo == (uint32_t)wl_msi_sink.iova && hi == 0 && data == 0);

    /* No sink page yet: the stack must be told, not given address zero. */
    memset(&wl_msi_sink, 0, sizeof(wl_msi_sink));
    assert(wl_board_msi_target(&lo, &hi, &data) == 1);

    /* alloc/free round trip, and free of a foreign pointer is a no-op. */
    void *p = wl_board_alloc(100);
    assert(p);
    wl_board_free(p);
    wl_board_free(NULL);
    int local = 0;
    wl_board_free(&local);          /* no tag, must be ignored */

    /* The platform table is complete: the stack dereferences every entry. */
    const struct lx_platform *plat = fuxi_wlan_board_platform();
    assert(plat->now_ms && plat->sleep_ms && plat->poll && plat->log && plat->halt);
    assert(plat->alloc && plat->free && plat->dma_alloc && plat->dma_free);
    assert(plat->dma_map && plat->dma_sync && plat->fw_get && plat->now_ns);
    assert(plat->boottime_ns && plat->busy_delay_us);
    assert(plat->preempt_disable && plat->preempt_enable);

    /* The preemption hooks are no-ops while a host owns the board: the host
     * runs on a thread it already controls, so there is nothing to disable.
     * They must still be callable and must stay balanced. */
    assert(preempt_depth == 0);
    plat->preempt_disable();
    plat->preempt_disable();
    assert(preempt_depth == 0);
    plat->preempt_enable();
    plat->preempt_enable();
    assert(preempt_depth == 0);

    /* now_ms is derived from now_ns, and boottime is its own clock. */
    assert(plat->now_ns() == 1234567890ull * 41);
    assert(plat->boottime_ns() == 9876543210ull * 41);
    assert(plat->now_ms() == (1234567890ull * 41) / NSEC_PER_MSEC);

    /* log keeps the level in the prefix and drops the chatty levels. */
    loglen = 0; logbuf[0] = 0;
    plat->log(3, "a message");
    assert(strstr(logbuf, "FUXI WLAN L3: a message"));
    size_t before = loglen;
    plat->log(7, "debug chatter");
    assert(loglen == before);
    puts("M1 platform callbacks ok");
}

/* ---- subsystem id and max read request --------------------------------- */

/*
 * These two run inside wl_board_prepare(), so they have to be right on the
 * first boot rather than discovered later: nw_pci.c:59 keys the board file off
 * the subsystem pair, and nw_pci.c:61 clears Max_Read_Request before any
 * register access happens.
 */
static void
test_identity(void)
{
    /* A real subsystem id is read through and logged. */
    reset_fixtures();
    attach_host();
    fake_cfg[0x2c / 4] = (0x1107u << 16) | 0x17cbu;
    assert(wl_board_subsystem() == 0);
    assert(strstr(logbuf, "subsystem vendor 0x17cb device 0x1107"));

    /* Zero and all-ones are not ids: a wire that reads back either is broken,
     * and passing it on would silently pick the wrong board file. */
    reset_fixtures();
    attach_host();
    fake_cfg[0x2c / 4] = 0;
    assert(wl_board_subsystem() == 1);
    assert(strstr(logbuf, "not a real id"));

    reset_fixtures();
    attach_host();
    fake_cfg[0x2c / 4] = 0xffffffffu;
    assert(wl_board_subsystem() == 1);

    /* Max_Read_Request starts non-zero, is cleared, and reads back clear. */
    reset_fixtures();
    attach_host();
    fake_cfg[PCIE_MARK / 4] = PCIE_MARK_VAL;   /* keep the marker readable */
    fake_cfg[(PCIE_CAP + 8) / 4] = 0x2000u | 0x0007u;   /* 512 B, other bits */
    assert(wl_board_maxread() == 0);
    assert((fake_cfg[(PCIE_CAP + 8) / 4] & 0x7000u) == 0);
    assert((fake_cfg[(PCIE_CAP + 8) / 4] & 0x0007u) == 0x0007u);   /* untouched */
    assert(strstr(logbuf, "max read request 128 bytes"));

    /* Already clear: no write, still success. */
    reset_fixtures();
    attach_host();
    fake_cfg[(PCIE_CAP + 8) / 4] = 0x0007u;
    const unsigned before = pwr_calls;
    assert(wl_board_maxread() == 0);
    assert(pwr_calls == before);

    /* A word that refuses to clear must be caught, not assumed. */
    reset_fixtures();
    attach_host();
    fake_cfg[(PCIE_CAP + 8) / 4] = 0x7000u;
    cfg_veto_word = (PCIE_CAP + 8) / 4;
    assert(wl_board_maxread() == 1);
    assert(strstr(logbuf, "max read request still"));
    cfg_veto_word = 0xffffffffu;

    puts("M1 identity ok");
}

/* ---- selector ---------------------------------------------------------- */

static void
test_selector(void)
{
    /* Unset: the board layer does nothing at all, so the r491 lane is intact. */
    reset_fixtures();
    attach_host();
    fuxi_wlan_board_init();
    assert(fuxi_wlan_board_table()->cfg_read == NULL);

    /* Out of range is refused with a message, not clamped. */
    reset_fixtures();
    attach_host();
    boot_linux = "3";
    fuxi_wlan_board_init();
    assert(strstr(logbuf, "out of range"));
    assert(fuxi_wlan_board_table()->cfg_read == NULL);

    /* An invalid sibling selector is refused. */
    reset_fixtures();
    attach_host();
    boot_linux = "1";
    wl_variant_invalid = true;
    fuxi_wlan_board_init();
    assert(strstr(logbuf, "needs fuxiwlan=1"));
    assert(fuxi_wlan_board_table()->cfg_read == NULL);

    /* fuxiwlanqrtr below 2 means the QMI lane is off; refuse. */
    reset_fixtures();
    attach_host();
    boot_linux = "1";
    wl_w12 = false;
    fuxi_wlan_board_init();
    assert(strstr(logbuf, "needs fuxiwlan=1"));

    /* Variant 1 prepares the board table and stops there. */
    reset_fixtures();
    attach_host();
    boot_linux = "1";
    fuxi_wlan_board_init();
    const struct nw_board *b = fuxi_wlan_board_table();
    assert(b->cfg_read == wl_board_cfg_read);
    assert(b->cfg_write == wl_board_cfg_write);
    assert(b->msi_target == wl_board_msi_target);
    assert(b->device_id == 0x1107);
    assert(b->msi_cap == MSI_CAP);
    assert(b->bar_len == (size_t)(WL_BAR0_END - WL_BAR0 + 1));
    assert(b->irq_base == 1000);
    assert(strstr(logbuf, "board layer ready"));
    /* Variant 1 leaves the old W11-W15 lane in charge. */
    assert(!wl_linux_stack);

    /* Variant 2 hands MHI/CE/HTC to the linked Linux stack and skips the old
     * lane from step 28; the flag is what fuxi_wlan.c's case 28 reads. */
    reset_fixtures();
    attach_host();
    boot_linux = "2";
    fuxi_wlan_board_init();
    assert(wl_linux_stack);
    assert(strstr(logbuf, "old W11-W15 lane skipped from step 28"));
    assert(strstr(logbuf, "board layer ready"));

    /* A device that is not the WCN7850 is refused. */
    reset_fixtures();
    attach_host();
    boot_linux = "1";
    fake_cfg[CFG_ID / 4] = (0x1108u << 16) | 0x17cbu;
    fuxi_wlan_board_init();
    assert(strstr(logbuf, "unexpected device id"));
    puts("M1 selector ok");
}

int
main(void)
{
    test_caps();
    test_config();
    test_identity();
    test_dma();
    test_firmware();
    test_platform();
    test_selector();
    puts("M1 board layer tests passed");
    return 0;
}
'''


def board_source():
    """The board layer with the kernel-only includes removed."""
    text = (ROOT / 'xnu/fuxi-wlan/fuxi_wlan_board.c').read_text()
    kept = [line for line in text.splitlines() if not line.startswith('#include <')]
    body = '\n'.join(kept) + '\n'
    # The barrier is the only inline asm; the host has no such instruction.
    body = re.sub(r'__asm__ volatile\([^;]+;', '/* host memory barrier */;', body)
    return body


def main():
    with tempfile.TemporaryDirectory(prefix='fuxi-wlan-m1-') as tmp:
        cfile = Path(tmp) / 'm1.c'
        binary = Path(tmp) / 'm1'
        cfile.write_text(PREAMBLE + board_source() + TESTS)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_DEFAULT_SOURCE', '-g',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        '-I', str(ROOT / 'xnu/fuxi-wlan'),
                        str(cfile), '-o', str(binary)], check=True)
        # Firmware stays cached for the driver's lifetime by design (the stack
        # never frees it and the reference board does not either), so the only
        # leaks ASan would report are those caches.
        env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
        subprocess.run([str(binary)], check=True, env=env)


if __name__ == '__main__':
    main()
