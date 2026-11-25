/*
 * FUXI WLAN board layer (milestone 1): the lx_platform + nw_board contract that
 * the Ath12kWifi Linux stack (ath12k + mac80211 + cfg80211) calls into.
 *
 * Gated by boot-arg fuxiwifilinux (default 0 = do nothing). The existing
 * fuxi_wlan.c bring-up keeps fuxiwlan and is untouched: this file never writes
 * a register, never allocates DMA and never starts a thread unless
 * fuxiwifilinux is set. That keeps the r491 baseline reproducible while the
 * new stack is developed.
 *
 * What this file is, and is not:
 *   - it implements all 17 lx_platform callbacks and the 9 nw_board fields of
 *     include/nw_abi.h (vendored as nw_abi.h, byte-identical to
 *     src/Ath12kWifi/include/nw_abi.h);
 *   - it does NOT contain the ath12k stack. That stack is 125 objects built by
 *     tools/build-ath12k.sh, which needs the Xcode clang, so the stack is
 *     linked in by a separate CI target. This file only has to compile inside
 *     XNU and to be exercised by host tests;
 *   - every callback is written so the host test harness in
 *     tools/test-fuxi-wlan-m1.py can drive it without a device: the register
 *     window, config space and firmware directory are all injectable through
 *     fuxi_wlan_board_host().
 *
 * Contract facts taken from nw_abi.h (never re-derived here):
 *   - dma_alloc returns zeroed memory the endpoint reaches at *bus;
 *   - dma_map returns the bus address of memory inside the DMA window, 0 if
 *     the memory does not lie in it (nw_abi.h:19);
 *   - fw_get matches by the LAST path component only (nw_abi.h:21);
 *   - cfg_read/cfg_write take offsets whose bit 31 set means "inside the PCIe
 *     capability" (nw_abi.h:31).
 *
 * What the stack does with the callbacks, from src/Ath12kWifi:
 *   nw_pci.c:14-15   cfg_read/cfg_write forwarded verbatim;
 *   nw_pci.c:26,28   pci_iomap / resource_len come from bar / bar_len;
 *   nw_pci.c:32-46   msi_target gives vector 0's target; msi_cap is the MSI
 *                    capability offset the stack programs;
 *   nw_pci.c:59      cfg_read(0x2c) picks the board file, so subsystem id must
 *                    be readable — fuxi_wlan.c never read it;
 *   nw_pci.c:61      device control 0x7000 is cleared to cap Max_Read_Request;
 *   nw_pci.c:56      vendor is fixed 0x17cb, device comes from device_id.
 *
 * Register and config access reuse the fault-contained accessors of
 * fuxi_wlan.c (wl_prd/wl_pwr for the endpoint, fw_access for anything that can
 * abort); this file is compiled after it and shares its statics.
 */

#include <sys/param.h>
#include <sys/systm.h>
#include <sys/fcntl.h>
#include <sys/vnode.h>
#include <sys/uio.h>
#include <string.h>
#include <kern/clock.h>
#include <kern/thread.h>
#include <kern/sched_prim.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <vm/vm_kern.h>
#include <vm/pmap.h>
#if FUXI_WLAN_XNU
#include <sys/kern_control.h>
#include <sys/kpi_mbuf.h>
#include <sys/errno.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOLocks.h>
#endif

/*
 * The board layer's exported entry points. fuxi_wlan.c carries its own
 * prototype for fuxi_wlan_board_init (step 19 calls it long before this
 * definition is reached); the rest are here so the compiler sees a prototype
 * for every non-static function in this translation unit.
 */
void fuxi_wlan_board_init(void);
const struct lx_platform *fuxi_wlan_board_platform(void);
const struct nw_board *fuxi_wlan_board_table(void);
int fuxi_wlan_board_start(void);
int fuxi_wlan_board_step(void);
void fuxi_wlan_board_stop(void);
unsigned fuxi_wlan_board_faults(void);
#if FUXI_WLAN_XNU
static void wl_l1_wifi_note(unsigned ms);
#endif

#include "nw_abi.h"

/* ---- selector ---------------------------------------------------------- */

/* fuxiwifilinux: 0 off (default), 1 board layer only, 2 board layer plus the
 * stack start attempt. Kept separate from fuxiwlan so both lanes can be
 * bisected independently. */
static unsigned wl_linux_variant;

/* Set once nw_wifi_start() returned 0: the stack owns the endpoint from then
 * on, so the pump and the abort path know whether there is anything to stop. */
static bool wl_linux_started;

/* Set while nw_wifi_start() runs: the pump samples the endpoint's state once a
 * second so a device stuck at PBL is visible in the log while it waits. */
static bool wl_linux_starting;
static uint64_t wl_linux_start_ms, wl_linux_sample_ms;

#if FUXI_WLAN_XNU
/*
 * The control channel's native transport.  wpa_supplicant talks to
 * org.unixnu.wifi.control through XNU's kern-control socket; this is the
 * counterpart of sheng's WifiProbe controlStart/controlSend path.  The
 * transport was previously a closed stub, which made every supplicant
 * connect fail with ENOENT before the first nl80211 request could arrive.
 */
struct wl_control_client {
	uint64_t session;
	uint32_t unit;
};

static kern_ctl_ref wl_ctl_ref;
static IOLock *wl_ctl_lock;
static struct wl_control_client *wl_ctl_client;

/* Values from include/nw_control.h; nw_abi.h deliberately carries only the
 * transport enum because this file is compiled without the userspace header. */
#define WL_NW_CONTROL_OK		0
#define WL_NW_CONTROL_INVALID		1
#define WL_NW_CONTROL_BUSY		2
#define WL_NW_CONTROL_NOT_READY		3
#define WL_NW_CONTROL_AUTH_FAILED	4
#define WL_NW_CONTROL_TIMED_OUT		5
#define WL_NW_CONTROL_UNSUPPORTED	6
#define WL_NW_CONTROL_NO_MEMORY		7
#define WL_NW_CONTROL_NOT_FOUND		8
#define WL_NW_CONTROL_DRIVER_FAILED	9
#define WL_NW_CONTROL_STALE		10

#define WL_NW_CONTROL_GET_SESSION	1

/* These live in the Ath12kWifi endpoint (nw_control.c); the kernel side is
 * linked with that stack and only needs the prototypes. */
extern int nw_wifi_control_attach(uint64_t *session);
extern int nw_wifi_control_receive(uint64_t session, size_t length,
    int (*reader)(void *, void *, size_t), void *context);
extern void nw_wifi_control_writable(uint64_t session);
extern void nw_wifi_control_detach(uint64_t session);

static errno_t
wl_control_error(int status)
{
	switch (status) {
	case WL_NW_CONTROL_OK: return 0;
	case WL_NW_CONTROL_INVALID: return EINVAL;
	case WL_NW_CONTROL_BUSY: return EWOULDBLOCK;
	case WL_NW_CONTROL_NOT_READY: return ENXIO;
	case WL_NW_CONTROL_AUTH_FAILED: return EACCES;
	case WL_NW_CONTROL_TIMED_OUT: return ETIMEDOUT;
	case WL_NW_CONTROL_UNSUPPORTED: return EOPNOTSUPP;
	case WL_NW_CONTROL_NO_MEMORY: return ENOBUFS;
	case WL_NW_CONTROL_NOT_FOUND: return ENOENT;
	case WL_NW_CONTROL_STALE: return ESTALE;
	default: return EIO;
	}
}

static errno_t
wl_control_connect(kern_ctl_ref kctl, struct sockaddr_ctl *address, void **information)
{
	(void)kctl;
	struct wl_control_client *owner = IOMalloc(sizeof(*owner));
	if (!owner) return ENOMEM;
	int status = nw_wifi_control_attach(&owner->session);
	if (status) {
		IOFree(owner, sizeof(*owner));
		return wl_control_error(status);
	}
	owner->unit = address->sc_unit;
	IOLockLock(wl_ctl_lock);
	wl_ctl_client = owner;
	IOLockUnlock(wl_ctl_lock);
	*information = owner;
	return 0;
}

static errno_t
wl_control_disconnect(kern_ctl_ref kctl, uint32_t unit, void *information)
{
	(void)kctl; (void)unit;
	/* XNU can call this after a failed connect with no unit info. */
	if (!information) return 0;
	struct wl_control_client *owner = information;
	nw_wifi_control_detach(owner->session);
	IOLockLock(wl_ctl_lock);
	if (wl_ctl_client == owner) wl_ctl_client = NULL;
	IOLockUnlock(wl_ctl_lock);
	IOFree(owner, sizeof(*owner));
	return 0;
}

static int
wl_control_read_mbuf(void *source, void *destination, size_t length)
{
	return mbuf_copydata((mbuf_t)source, 0, length, destination);
}

static errno_t
wl_control_receive(kern_ctl_ref kctl, uint32_t unit, void *information,
    mbuf_t packet, int flags)
{
	(void)kctl; (void)unit; (void)flags;
	struct wl_control_client *owner = information;
	int status = nw_wifi_control_receive(owner->session,
	    mbuf_pkthdr_len(packet), wl_control_read_mbuf, packet);
	mbuf_freem(packet);
	return wl_control_error(status);
}

#ifdef KERNEL_PRIVATE
static void
wl_control_rcvd(kern_ctl_ref kctl, uint32_t unit, void *information, int flags)
{
	(void)kctl; (void)unit; (void)flags;
	nw_wifi_control_writable(((struct wl_control_client *)information)->session);
}
#endif /* KERNEL_PRIVATE */

static errno_t
wl_control_getopt(kern_ctl_ref kctl, uint32_t unit, void *information,
    int option, void *bytes, size_t *length)
{
	(void)kctl; (void)unit;
	if (option != WL_NW_CONTROL_GET_SESSION) return ENOPROTOOPT;
	if (!length) return EINVAL;
	if (!bytes || *length < sizeof(uint64_t)) {
		*length = sizeof(uint64_t);
		return EMSGSIZE;
	}
	uint64_t session = ((struct wl_control_client *)information)->session;
	memcpy(bytes, &session, sizeof(session));
	*length = sizeof(session);
	return 0;
}

static int
wl_control_send(void *context, uint64_t session, const void *bytes, size_t length)
{
	(void)context;
	IOLockLock(wl_ctl_lock);
	bool active = wl_ctl_client && wl_ctl_client->session == session;
	uint32_t unit = active ? wl_ctl_client->unit : 0;
	IOLockUnlock(wl_ctl_lock);
	if (!active) return NW_CONTROL_SEND_CLOSED;
	/* Do not hold our lock while the socket layer takes its own. */
	errno_t result = ctl_enqueuedata(wl_ctl_ref, unit, (void *)bytes, length,
	    CTL_DATA_EOR);
	if (!result) return NW_CONTROL_SEND_OK;
	if (result == ENOBUFS || result == EWOULDBLOCK) return NW_CONTROL_SEND_AGAIN;
	return NW_CONTROL_SEND_CLOSED;
}

static void
wl_control_wake(void *context)
{
	(void)context;
}

static int
wl_control_start(void)
{
	if (wl_ctl_ref) return 0;
	if (!wl_ctl_lock) wl_ctl_lock = IOLockAlloc();
	if (!wl_ctl_lock) return ENOMEM;
	struct kern_ctl_reg registration = {};
	snprintf(registration.ctl_name, sizeof(registration.ctl_name), "%s",
	    "org.unixnu.wifi.control");
#ifdef KERNEL_PRIVATE
	registration.ctl_flags = CTL_FLAG_PRIVILEGED | CTL_FLAG_REG_EXTENDED;
#else
	registration.ctl_flags = CTL_FLAG_PRIVILEGED;
#endif
	registration.ctl_sendsize = 256 * 1024;
	registration.ctl_recvsize = 512 * 1024;
	registration.ctl_connect = wl_control_connect;
	registration.ctl_disconnect = wl_control_disconnect;
	registration.ctl_send = wl_control_receive;
	registration.ctl_getopt = wl_control_getopt;
#ifdef KERNEL_PRIVATE
	registration.ctl_rcvd = wl_control_rcvd;
#endif
	errno_t result = ctl_register(&registration, &wl_ctl_ref);
	if (result) return result;
	const struct nw_control_transport transport = {
		.context = NULL,
		.send = wl_control_send,
		.wake = wl_control_wake,
	};
	int status = nw_wifi_control_transport_set(&transport);
	if (status) {
		ctl_deregister(wl_ctl_ref);
		wl_ctl_ref = NULL;
		return wl_control_error(status);
	}
	return 0;
}

static void
wl_control_stop(void)
{
	if (!wl_ctl_ref) return;
	ctl_deregister(wl_ctl_ref);
	wl_ctl_ref = NULL;
}

#else /* !FUXI_WLAN_XNU: the host test has no kernel control socket. */
static int
wl_board_control_send(void *context, uint64_t session, const void *bytes, size_t length)
{
	(void)context; (void)session; (void)bytes; (void)length;
	return NW_CONTROL_SEND_CLOSED;
}

static void
wl_board_control_wake(void *context)
{
	(void)context;
}

static const struct nw_control_transport wl_board_control = {
	.context = NULL,
	.send = wl_board_control_send,
	.wake = wl_board_control_wake,
};

static int
wl_control_start(void)
{
	return nw_wifi_control_transport_set(&wl_board_control);
}

static void
wl_control_stop(void)
{
}
#endif /* FUXI_WLAN_XNU */

/* ---- host-test injection ----------------------------------------------- */

/*
 * The host harness has no kernel: it provides a plain array as the register
 * window, a config-space array and a firmware directory, then calls the same
 * functions XNU calls. Under XNU the hook stays NULL and every accessor takes
 * its normal path, so the injection costs one predictable branch.
 */
struct wl_board_host {
	void *bar;			/* register window (host only) */
	uint32_t bar_len;
	uint32_t *cfg;			/* 256 dwords of config space (host only) */
	uint32_t cfg_dwords;
	uint32_t pcie_cap;		/* offset of capability 0x10 */
	uint32_t msi_cap;		/* offset of capability 0x05 */
	int (*fw_read)(const char *name, uint8_t **data, size_t *size);
	uint64_t (*now_ns)(void);
	/*
	 * Optional write veto: returns non-zero to make the write a no-op, which
	 * is how the harness proves that a word which refuses to change is caught
	 * instead of assumed. NULL under XNU and in the ordinary host cases.
	 */
	int (*cfg_veto)(uint32_t word_off, uint32_t merged);
};
static struct wl_board_host *wl_board_host;

/* Discovered by wl_board_caps(); needed by the bit-31 config offsets. */
static uint32_t wl_board_pcie_cap;

void
fuxi_wlan_board_host(struct wl_board_host *h)
{
	wl_board_host = h;
	if (h) { wl_board_pcie_cap = h->pcie_cap; }
}

/* ---- config space ------------------------------------------------------ */

/*
 * The stack addresses config space two ways: a plain offset, or an offset with
 * bit 31 set meaning "inside the PCIe capability" (nw_abi.h:31, used by
 * nw_pci.c:17 for pcie_capability_clear_and_set_word). Both are widened to a
 * dword here, because the endpoint only decodes dword accesses; sub-dword
 * reads extract, sub-dword writes read-modify-write.
 */
static uint32_t
wl_board_cfg_dword(int off, uint32_t *word_off)
{
	uint32_t o = (uint32_t)off;
	if (o & 0x80000000u) {
		o = (o & 0x7fffffffu) + wl_board_pcie_cap;
	}
	*word_off = o & ~3u;
	return o;
}

static int
wl_board_cfg_read(int off, int size, uint32_t *v)
{
	if (size != 1 && size != 2 && size != 4) { return 1; }
	uint32_t word = 0;
	const uint32_t o = wl_board_cfg_dword(off, &word);
	if (wl_board_host) {
		if (word + 4 > wl_board_host->cfg_dwords * 4) { return 1; }
		word = wl_board_host->cfg[word / 4];
	} else {
		if (fw_faults) { return 1; }
		word = wl_prd(WL_EPCFG + word);
		if (word == ~0u || fw_faults) { return 1; }
	}
	const unsigned shift = (o & 3) * 8;
	if (size == 4) { *v = word; }
	else if (size == 2) { *v = (word >> shift) & 0xffffu; }
	else { *v = (word >> shift) & 0xffu; }
	return 0;
}

static int
wl_board_cfg_write(int off, int size, uint32_t v)
{
	if (size != 1 && size != 2 && size != 4) { return 1; }
	uint32_t at = 0;
	const uint32_t o = wl_board_cfg_dword(off, &at);
	const unsigned shift = (o & 3) * 8;
	const uint32_t mask = size == 4 ? ~0u : size == 2 ? (0xffffu << shift) : (0xffu << shift);
	const uint32_t value = size == 4 ? v : (v << shift) & mask;

	if (wl_board_host) {
		if (at + 4 > wl_board_host->cfg_dwords * 4) { return 1; }
		const uint32_t merged = (wl_board_host->cfg[at / 4] & ~mask) | value;
		/* A vetoed write is dropped but reported as done: that is the real
		 * failure mode of a config word that ignores its owner, and the caller
		 * has to catch it by reading back, not by trusting the return code. */
		if (wl_board_host->cfg_veto == NULL || !wl_board_host->cfg_veto(at, merged)) {
			wl_board_host->cfg[at / 4] = merged;
		}
		return 0;
	}
	uint32_t old = wl_prd(WL_EPCFG + at);
	if (old == ~0u || fw_faults) { return 1; }
	/* Bit 31 of a config write is the enable in the ATU window; never let a
	 * read-modify-write carry it back in as data. */
	old &= ~0x80000000u;
	const uint32_t merged = (old & ~mask) | value;
	wl_pwr(WL_EPCFG + at, merged);
	if (fw_faults || wl_prd(WL_EPCFG + at) != merged) { return 1; }
	return 0;
}

/* ---- MSI target -------------------------------------------------------- */

/*
 * Where the endpoint writes to raise vector 0. fuxi already reserves
 * wl_msi_sink for exactly this in the old lane (fuxi_wlan.c:2876 programs the
 * endpoint's MSI base address to wl_msi_sink.iova); reuse the same page so a
 * single sink serves both lanes and the RAM is already allocated.
 */
static int
wl_board_msi_target(uint32_t *lo, uint32_t *hi, uint32_t *data)
{
	if (!wl_msi_sink.iova) { return 1; }
	*lo = (uint32_t)wl_msi_sink.iova;
	*hi = (uint32_t)(wl_msi_sink.iova >> 32);
	*data = 0;
	return 0;
}

/* ---- lx_platform: clocks ----------------------------------------------- */

static uint64_t
wl_board_now_ns(void)
{
	if (wl_board_host && wl_board_host->now_ns) { return wl_board_host->now_ns(); }
	uint64_t ns = 0;
	absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
	return ns;
}

static uint64_t
wl_board_boottime_ns(void)
{
	if (wl_board_host && wl_board_host->now_ns) { return wl_board_host->now_ns(); }
	uint64_t ns = 0;
	absolutetime_to_nanoseconds(mach_continuous_time(), &ns);
	return ns;
}

static uint64_t
wl_board_now_ms(void)
{
	return wl_board_now_ns() / NSEC_PER_MSEC;
}

static void
wl_board_sleep_ms(unsigned ms)
{
	if (wl_board_host) { return; }
	wl_sleep(ms);
}

static void
wl_board_busy_delay_us(unsigned us)
{
	if (wl_board_host) { return; }
	delay_for_interval(us, NSEC_PER_USEC);
}

/* ---- lx_platform: memory ----------------------------------------------- */

/*
 * Plain host memory for the stack's own bookkeeping (skbs, ring metadata).
 * The 16-byte header keeps the size so free() needs no side table; it mirrors
 * the reference board's trick (WifiProbe.cpp:930) but uses kernel_map memory.
 */
#define WL_BOARD_ALLOC_TAG 0x574c4241u /* 'WLBA' */

static void *
wl_board_alloc(size_t n)
{
	if (!n) { return NULL; }
	const vm_size_t bytes = round_page(n + 16);
	vm_offset_t va = 0;
	if (kmem_alloc(kernel_map, &va, bytes, KMA_DATA | KMA_ZERO | KMA_NOPAGEWAIT,
	    VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) { return NULL; }
	*(uint64_t *)va = (uint64_t)bytes;
	*((uint32_t *)va + 2) = WL_BOARD_ALLOC_TAG;
	return (void *)(va + 16);
}

static void
wl_board_free(void *p)
{
	if (!p) { return; }
	/*
	 * alloc() returns a page-aligned block plus 16 bytes, so a pointer that is
	 * not 16 bytes into a page did not come from here. Checking that before
	 * touching the header keeps a stray free from reading memory it never
	 * allocated (the reference board reads the header unconditionally).
	 */
	if (((vm_offset_t)p & PAGE_MASK) != 16) { return; }
	const vm_offset_t va = (vm_offset_t)p - 16;
	if (*((uint32_t *)va + 2) != WL_BOARD_ALLOC_TAG) { return; }
	const vm_size_t bytes = (vm_size_t)*(uint64_t *)va;
	*((uint32_t *)va + 2) = 0;
	kmem_free(kernel_map, va, bytes);
}

/*
 * DMA window for the stack. Two things differ from the old lane's allocator:
 *   - the stack wants a bus address back (dma_alloc) and a p->bus translation
 *     for memory it allocated elsewhere (dma_map), so the window has to be a
 *     real contiguous IOVA range with a map of what lives where;
 *   - the stack frees and reallocates, so the window is a first-fit arena over
 *     a fixed IOVA range rather than a bump pointer.
 * The IOVA range is the old lane's CNSS pool (0xa0000000-0xb0000000), already
 * routed through SMMU CB 6 with 36-bit PAs, so no new SMMU programming is
 * needed and the two lanes cannot collide: the old lane never runs with
 * fuxiwifilinux set.
 */
#define WL_BOARD_DMA_SLOTS 4096

struct wl_board_dma_slot {
	uint64_t iova;
	vm_size_t size;
	vm_offset_t va;
	bool used;
};
static struct wl_board_dma_slot wl_board_dma[WL_BOARD_DMA_SLOTS];
static unsigned wl_board_dma_used;

/* First page of the window is the MSI sink: dma_alloc must never hand it out. */
#define WL_BOARD_DMA_RESERVED PAGE_SIZE

static struct wl_board_dma_slot *
wl_board_dma_slot(void)
{
	for (unsigned i = 0; i < WL_BOARD_DMA_SLOTS; i++) {
		if (!wl_board_dma[i].used) { return &wl_board_dma[i]; }
	}
	return NULL;
}

static void *
wl_board_dma_alloc(size_t n, uint64_t *bus)
{
	if (!n || !bus) { return NULL; }
	const vm_size_t bytes = round_page(n);
	if (bytes < n) { return NULL; }
	struct wl_board_dma_slot *slot = wl_board_dma_slot();
	if (!slot) { return NULL; }

	/* First fit over the reserved-then-used intervals, keeping IOVA aligned to
	 * the allocation size when that is a power of two: MHI requires its TRE
	 * rings aligned to their length (host/init.c:163-177). */
	uint64_t align = PAGE_SIZE;
	while (align < bytes && align < (1ull << 21)) { align <<= 1; }
	uint64_t at = wl_iova_base + WL_BOARD_DMA_RESERVED;
	for (;;) {
		at = (at + align - 1) & ~(align - 1);
		if (at + bytes > wl_iova_end) { return NULL; }
		bool clash = false;
		for (unsigned i = 0; i < WL_BOARD_DMA_SLOTS; i++) {
			if (!wl_board_dma[i].used) { continue; }
			const uint64_t s = wl_board_dma[i].iova, e = s + wl_board_dma[i].size;
			if (at < e && s < at + bytes) { at = e; clash = true; break; }
		}
		if (clash) { continue; }
		/* The slot table only knows the arena's own blocks, but the pool also
		 * holds this driver's fixed buffers, already walked. Step over them:
		 * the arena starts at the bottom of the pool while the fixed buffers
		 * sit in the middle of it, so the first fit lands inside the AMSS
		 * window and publish refuses every large request. */
		const uint64_t busy = wl_board_dma_occupied(at, bytes);
		if (!busy) { break; }
		at = busy + PAGE_SIZE;
	}

	struct wl_dma d;
	bzero(&d, sizeof(d));
	if (wl_dma_alloc(&d, bytes)) { return NULL; }
	/* dma_alloc_coherent hands out memory the device reads without a cache
	 * flush, and the ported MHI stack never calls dma_sync: it fills ring
	 * elements and firmware images with plain stores and then rings a doorbell.
	 * Keep the pages Normal-NC so those stores reach the endpoint; write-back
	 * pages are what left the device fetching a stale SBL image at READY. */
	if (wl_dma_nc(&d)) {
		wl_dma_free(&d);
		return NULL;
	}
	/* The arena owns the IOVA, not the bump pointer, and the pages need a leaf
	 * in the live tables before the endpoint can fetch from them. */
	wl_iova_next -= d.size;
	if (wl_board_dma_publish(&d, at)) {
		wl_dma_free(&d);
		return NULL;
	}
	slot->iova = at;
	slot->size = d.size;
	slot->va = d.va;
	slot->used = true;
	wl_board_dma_used++;
	*bus = at;
	return (void *)d.va;
}

static void
wl_board_dma_free(size_t n, void *p, uint64_t bus)
{
	(void)n;
	if (!p) { return; }
	for (unsigned i = 0; i < WL_BOARD_DMA_SLOTS; i++) {
		if (!wl_board_dma[i].used || wl_board_dma[i].va != (vm_offset_t)p) { continue; }
		if (bus && wl_board_dma[i].iova != bus) { continue; }
		struct wl_dma d;
		bzero(&d, sizeof(d));
		d.va = wl_board_dma[i].va;
		d.size = wl_board_dma[i].size;
		/* Retract the leaves first: the endpoint must stop walking these pages
		 * before the kernel reuses them. */
		wl_board_dma_unpublish(&d, wl_board_dma[i].iova);
		wl_dma_free(&d);
		bzero(&wl_board_dma[i], sizeof(wl_board_dma[i]));
		if (wl_board_dma_used) { wl_board_dma_used--; }
		return;
	}
}

/*
 * nw_abi.h:19 — the bus address of memory inside the window, 0 if it is not.
 * The stack calls this for buffers it allocated itself and then hands the
 * result to the endpoint, so a wrong answer here is a silent DMA to nowhere.
 */
static uint64_t
wl_board_dma_map(void *p, size_t n, int dir)
{
	(void)dir;
	if (!p || !n) { return 0; }
	const vm_offset_t va = (vm_offset_t)p;
	for (unsigned i = 0; i < WL_BOARD_DMA_SLOTS; i++) {
		if (!wl_board_dma[i].used) { continue; }
		const vm_offset_t base = wl_board_dma[i].va;
		if (va < base || va + n > base + wl_board_dma[i].size) { continue; }
		return wl_board_dma[i].iova + (va - base);
	}
	return 0;
}

/*
 * dma_sync: the window is Normal-NC (wl_dma_nc in the old lane, same CB
 * attributes here), so no cache maintenance is required for coherent buffers;
 * the stack also calls it for streaming buffers, where a barrier is enough
 * because the endpoint reads through the same non-cacheable alias.
 */
static void
wl_board_dma_sync(uint64_t bus, size_t n, int dir)
{
	(void)bus; (void)n; (void)dir;
	if (wl_board_host) { return; }
	__asm__ volatile("dsb sy" ::: "memory");
}

/* ---- lx_platform: firmware --------------------------------------------- */

/*
 * Firmware by last path component (nw_abi.h:21). The stack asks for the names
 * ath12k uses (hw.h:76-82: board-2.bin, board.bin, amss.bin, m3.bin,
 * regdb.bin), while fuxi keeps its files under /usr/local/share/fuxi/wlan with
 * the board-specific names the old lane already stages. Map one to the other
 * explicitly instead of guessing, and cache each file: the stack may ask twice
 * and the read is a VFS round trip.
 */
struct wl_board_fw {
	char name[32];
	uint8_t *data;
	size_t size;
	bool tried;
	bool found;
};
#define WL_BOARD_FW_SLOTS 12
static struct wl_board_fw wl_board_fw[WL_BOARD_FW_SLOTS];

struct wl_board_fw_name {
	const char *requested;	/* name the stack asks for */
	const char *staged;	/* file actually staged on the device */
};

static const struct wl_board_fw_name wl_board_fw_names[] = {
	{ "amss.bin", "amss20.bin" },		/* sheng-identical upstream image */
	{ "m3.bin", "m3.bin" },
	{ "board-2.bin", "board-2.bin" },
	{ "board.bin", "bd_ath12k_255.bin" },	/* board-data payload, see M0-d */
	{ "regdb.bin", "regdb_xiaomi.bin" },
	{ "regdb_xiaomi.bin", "regdb_xiaomi.bin" },
	{ "bd_ath12k_255.bin", "bd_ath12k_255.bin" },
	{ "amss20.bin", "amss20.bin" },
};

static const char *
wl_board_fw_staged(const char *name)
{
	for (unsigned i = 0; i < sizeof(wl_board_fw_names) / sizeof(wl_board_fw_names[0]); i++) {
		if (!strcmp(name, wl_board_fw_names[i].requested)) { return wl_board_fw_names[i].staged; }
	}
	return name;	/* already a staged name */
}

static const char *
wl_board_basename(const char *path)
{
	const char *base = path;
	for (const char *c = path; c && *c; c++) { if (*c == '/') { base = c + 1; } }
	return base;
}

static int
wl_board_fw_load(const char *staged, uint8_t **data, size_t *size)
{
	char path[128];
	snprintf(path, sizeof(path), "/usr/local/share/fuxi/wlan/%s", staged);

	vnode_t vp = NULL;
	vfs_context_t ctx = vfs_context_kernel();
	int e = vnode_open(path, FREAD | O_NOFOLLOW | O_NONBLOCK, 0, 0, &vp, ctx);
	if (e) {
		printf("FUXI WLAN: L1 firmware %s unreadable errno %d\n", path, e);
		return 1;
	}
	struct vnode_attr a;
	VATTR_INIT(&a); VATTR_WANTED(&a, va_data_size);
	e = vnode_getattr(vp, &a, ctx);
	if (e || vnode_vtype(vp) != VREG || !VATTR_IS_SUPPORTED(&a, va_data_size) ||
	    !a.va_data_size || a.va_data_size > (64u * 1024u * 1024u)) {
		printf("FUXI WLAN: L1 firmware %s bad size/type\n", path);
		(void)vnode_close(vp, FREAD, ctx);
		return 1;
	}
	const size_t bytes = (size_t)a.va_data_size;
	uint8_t *buf = wl_board_alloc(bytes);
	if (!buf) {
		(void)vnode_close(vp, FREAD, ctx);
		return 1;
	}
	int resid = 0;
	e = vn_rdwr(UIO_READ, vp, (caddr_t)buf, (int)bytes, 0, UIO_SYSSPACE, 0,
	    vfs_context_ucred(ctx), &resid, vfs_context_proc(ctx));
	if (vnode_close(vp, FREAD, ctx)) { e = 1; }
	if (e || resid) {
		printf("FUXI WLAN: L1 firmware %s read failed resid %d\n", path, resid);
		wl_board_free(buf);
		return 1;
	}
	printf("FUXI WLAN: L1 firmware %s bytes %lu\n", staged, (unsigned long)bytes);
	*data = buf;
	*size = bytes;
	return 0;
}

static int
wl_board_fw_get(const char *name, const uint8_t **data, size_t *size)
{
	if (!name || !data || !size) { return 1; }
	char want[32];
	strlcpy(want, wl_board_basename(name), sizeof(want));
	const char *staged = wl_board_fw_staged(want);

	if (wl_board_host && wl_board_host->fw_read) {
		uint8_t *buf = NULL;
		size_t bytes = 0;
		if (wl_board_host->fw_read(staged, &buf, &bytes) || !buf || !bytes) { return 1; }
		*data = buf;
		*size = bytes;
		return 0;
	}

	struct wl_board_fw *slot = NULL;
	for (unsigned i = 0; i < WL_BOARD_FW_SLOTS; i++) {
		if (wl_board_fw[i].tried && !strcmp(wl_board_fw[i].name, staged)) { slot = &wl_board_fw[i]; break; }
	}
	if (!slot) {
		for (unsigned i = 0; i < WL_BOARD_FW_SLOTS; i++) {
			if (!wl_board_fw[i].tried) {
				slot = &wl_board_fw[i];
				strlcpy(slot->name, staged, sizeof(slot->name));
				slot->tried = true;
				break;
			}
		}
	}
	if (!slot) { return 1; }
	if (!slot->found) {
		uint8_t *buf = NULL;
		size_t bytes = 0;
		if (wl_board_fw_load(staged, &buf, &bytes)) { return 1; }
		slot->data = buf;
		slot->size = bytes;
		slot->found = true;
	}
	*data = slot->data;
	*size = slot->size;
	return 0;
}

/* ---- lx_platform: the rest --------------------------------------------- */

static void
wl_board_log(int level, const char *line)
{
	if (!line) { return; }
	/* The stack is chatty at debug levels; keep the kernel log readable and
	 * let the level survive in the prefix so a boot log can be filtered. */
	if (level > 6) { return; }
	printf("FUXI WLAN L%d: %.180s\n", level, line);
}

static void
wl_board_halt(void)
{
	printf("FUXI WLAN: L1 stack halted itself\n");
	for (;;) { wl_board_sleep_ms(1000); }
}

/*
 * The stack calls poll() to gather device events. In this lane the endpoint's
 * MSIs land in the sink page and the stack reads its own status; the board only
 * has to let the worker yield. Under XNU with a real interrupt attached, the
 * delivery path is nw_wifi_irq_pending(); until then this is the polling shape
 * the old lane proved works.
 */
static void
wl_board_poll(void)
{
	if (wl_board_host) { return; }
	if (!wl_linux_starting) { return; }
	uint64_t now = wl_board_now_ms();
	if (now - wl_linux_sample_ms < 1000) { return; }
	wl_linux_sample_ms = now;
	wl_l1_state_sample((unsigned)(now - wl_linux_start_ms));
}

/*
 * Paired native hooks for the stack's control/status locks (nw_abi.h:24).
 * The sheng reference passes two extern symbols here and never defines them in
 * its own tree (WifiProbe.cpp:1193), so they come from the kernel.
 *
 * <kern/sched_prim.h> only publishes the plain names under MACH_KERNEL_PRIVATE;
 * what this kernel actually exports (checked in the r491 image's symbol table)
 * is _disable_preemption / _enable_preemption, so declare those here.
 */
extern void _disable_preemption(void);
extern void _enable_preemption(void);

static void
wl_board_preempt_disable(void)
{
	if (wl_board_host) { return; }
	_disable_preemption();
}

static void
wl_board_preempt_enable(void)
{
	if (wl_board_host) { return; }
	_enable_preemption();
}

/* ---- the two tables ---------------------------------------------------- */

static const struct lx_platform wl_board_platform = {
	.now_ms = wl_board_now_ms,
	.sleep_ms = wl_board_sleep_ms,
	.poll = wl_board_poll,
	.log = wl_board_log,
	.halt = wl_board_halt,
	.alloc = wl_board_alloc,
	.free = wl_board_free,
	.dma_alloc = wl_board_dma_alloc,
	.dma_free = wl_board_dma_free,
	.dma_map = wl_board_dma_map,
	.dma_sync = wl_board_dma_sync,
	.fw_get = wl_board_fw_get,
	.now_ns = wl_board_now_ns,
	.boottime_ns = wl_board_boottime_ns,
	.busy_delay_us = wl_board_busy_delay_us,
	.preempt_disable = wl_board_preempt_disable,
	.preempt_enable = wl_board_preempt_enable,
};

static struct nw_board wl_board;

/* ---- capability discovery ---------------------------------------------- */

/*
 * The stack needs the MSI capability offset and the device id before it can
 * probe (nw_pci.c:39,56). Both come from config space: id at 0x00, the
 * capability list head at 0x34. Walk it exactly as the endpoint's own decoder
 * does — dword reads, low byte is the id, next byte is the next pointer — and
 * refuse anything that walks off the 256-byte region.
 */
static int
wl_board_caps(uint32_t *device_id, uint32_t *msi_cap, uint32_t *pcie_cap)
{
	uint32_t id = 0;
	if (wl_board_cfg_read(0x00, 4, &id)) { return 1; }
	*device_id = id >> 16;

	uint32_t status = 0, head = 0;
	if (wl_board_cfg_read(0x04, 4, &status)) { return 1; }
	/* Dword 0x04 is Command in the low half and Status in the high half. The
	 * capability-list bit is Status bit 4, which is bit 20 of the dword; bit 4
	 * of the dword is Command bit 4 and says nothing about the list. The
	 * endpoint reports 0x100406 here, so testing bit 4 rejected every chain. */
	if (!(status & (1u << 20))) {
		printf("FUXI WLAN: L1 no capability list (cmd/status %#x); contain\n", status);
		return 1;
	}
	if (wl_board_cfg_read(0x34, 4, &head)) { return 1; }
	unsigned p = head & 0xffu;
	*msi_cap = 0;
	*pcie_cap = 0;
	unsigned guard = 0;
	for (; p && guard < 48; guard++) {
		if ((p & 3) || p < 0x40 || p > 0xfc) { return 1; }
		uint32_t v = 0;
		if (wl_board_cfg_read((int)p, 4, &v) || v == ~0u) { return 1; }
		if (!v) { break; }
		if ((v & 0xff) == 0x05) { *msi_cap = p; }
		if ((v & 0xff) == 0x10) { *pcie_cap = p; }
		p = (v >> 8) & 0xffu;
	}
	/* A chain that never reaches its terminator is broken, not merely long:
	 * walking it further would keep re-reading the same dwords. */
	if (p && guard >= 48) { return 1; }
	if (!*msi_cap || !*pcie_cap) { return 1; }
	return 0;
}

/*
 * Subsystem identity. nw_pci.c:59 reads 0x2c and fills subsystem_vendor /
 * subsystem_device, and the comment there says the board file is picked by
 * these — so this lane has to be able to read it, and has to report what the
 * endpoint actually says rather than assume. ath12k's board-2.bin entries are
 * keyed by exactly this pair (see WLAN-BDF-M0D.md), which is why the value is
 * logged: it is the input to the BDF choice, not decoration.
 */
static int
wl_board_subsystem(void)
{
	uint32_t sub = 0;
	if (wl_board_cfg_read(0x2c, 4, &sub)) {
		printf("FUXI WLAN: L1 subsystem id unreadable; contain\n");
		return 1;
	}
	if (sub == 0 || sub == ~0u) {
		printf("FUXI WLAN: L1 subsystem id %#x is not a real id; contain\n", sub);
		return 1;
	}
	printf("FUXI WLAN: L1 subsystem vendor %#x device %#x (board-2 key %#x:%#x)\n",
	    sub & 0xffffu, sub >> 16, sub & 0xffffu, sub >> 16);
	return 0;
}

/*
 * Cap Max_Read_Request at 128 bytes. nw_pci.c:61 does exactly this to the
 * device control word at PCIe capability offset 8 — it passes 0x80000008, whose
 * bit 31 means "inside the PCIe capability" (nw_abi.h:31), so the real offset
 * is pcie_cap + 8. Bits 14:12 are Max_Read_Request size; clearing them selects
 * 128 B. The endpoint is a Gen2 x1 link whose MHI doorbells are dword writes,
 * and the old lane never touched this, so it is logged rather than assumed.
 */
static int
wl_board_maxread(void)
{
	uint32_t ctl = 0;
	const int at = (int)(0x80000000u | 8u);
	if (wl_board_cfg_read(at, 2, &ctl)) {
		printf("FUXI WLAN: L1 device control unreadable; contain\n");
		return 1;
	}
	const uint32_t was = ctl;
	const uint32_t now = ctl & ~0x7000u;
	if (now != was && wl_board_cfg_write(at, 2, now)) {
		printf("FUXI WLAN: L1 device control write failed (%#x); contain\n", was);
		return 1;
	}
	uint32_t back = 0;
	if (wl_board_cfg_read(at, 2, &back) || (back & 0x7000u)) {
		printf("FUXI WLAN: L1 max read request still %#x (was %#x); contain\n", back, was);
		return 1;
	}
	printf("FUXI WLAN: L1 device control %#x -> %#x max read request 128 bytes\n", was, back);
	return 0;
}

/*
 * Map a physical address into the VA space the stack will dereference.
 *
 * The stack's readl/writel are plain pointer dereferences (shim/include/lx.h:420),
 * so whatever comes back from here is used directly at EL1. Returning the
 * physical address -- which is what a NULL map_phys did -- makes the first
 * window select in ath12k_pci_read32 write to 0x6040310c as if it were a VA and
 * the kernel reboots with no panic text (r497). fw_va() answers for the windows
 * fw_thread already mapped, and the whole 2 MiB BAR0 is one of them, so the
 * endpoint window needs no second mapping here. Anything outside those windows
 * gets NULL, which the stack reports as a failed map instead of faulting.
 */
static void *
wl_board_map_phys(uint64_t pa, size_t n)
{
	(void)n;
	if (wl_board_host) {
		/* The host harness has no kernel map; it owns a plain array. */
		if (pa >= WL_BAR0 && pa < (uint64_t)WL_BAR0 + wl_board_host->bar_len) {
			return (void *)((uintptr_t)wl_board_host->bar + (uintptr_t)(pa - WL_BAR0));
		}
		return NULL;
	}
	uintptr_t va = fw_va(pa);
	return va ? (void *)va : NULL;
}

/*
 * Prepare the board table. The register window is the endpoint's BAR0, which
 * the old lane already assigned and verified (fuxi_wlan.c step 15, WL_BAR0
 * 0x60400000, 2 MiB non-prefetchable); this lane never re-programs it, so the
 * two lanes cannot disagree about where the window is.
 */
static int
wl_board_prepare(void)
{
	uint32_t device = 0, msi = 0, pcie = 0;
	if (wl_board_caps(&device, &msi, &pcie)) {
		printf("FUXI WLAN: L1 capability walk failed; contain\n");
		return 1;
	}
	if (device != 0x1107) {
		printf("FUXI WLAN: L1 unexpected device id %#x; contain\n", device);
		return 1;
	}
	bzero(&wl_board, sizeof(wl_board));
	/*
	 * bar is what pci_iomap() hands ath12k as ab->mem, and every register
	 * access is a dereference of it: it has to be the kernel VA the old lane
	 * mapped, never the physical address. wl_board_map_phys answers both the
	 * XNU case (fw_va over the 2 MiB BAR0 window) and the host harness.
	 */
	wl_board.bar = wl_board_map_phys(WL_BAR0, WL_BAR0_END - WL_BAR0 + 1);
	if (!wl_board.bar) {
		printf("FUXI WLAN: L1 BAR0 window not mapped; contain\n");
		return 1;
	}
	wl_board.bar_len = (size_t)(WL_BAR0_END - WL_BAR0 + 1);
	wl_board.device_id = (uint16_t)device;
	wl_board.msi_cap = (uint16_t)msi;
	/* Vectors become irq numbers base..base+n-1 (nw_pci.c:48). The old lane
	 * never registered real interrupts, so any base is free; pick one well
	 * clear of the platform's own allocations. */
	wl_board.irq_base = 1000;
	wl_board.cfg_read = wl_board_cfg_read;
	wl_board.cfg_write = wl_board_cfg_write;
	wl_board.msi_target = wl_board_msi_target;
	/* lx_ioremap()/memremap() go through this as well: qmi.c maps the target
	 * memory regions it is told to hand the firmware, and those are DDR the
	 * old lane owns, not BAR0. */
	wl_board.map_phys = wl_board_map_phys;
	printf("FUXI WLAN: L1 board device %#x msi cap %#x pcie cap %#x bar %#llx va %#llx+%lu irq base %d\n",
	    device, msi, pcie, (uint64_t)WL_BAR0, (uint64_t)(uintptr_t)wl_board.bar,
	    (unsigned long)wl_board.bar_len, wl_board.irq_base);
	if (wl_board_subsystem() || wl_board_maxread()) { return 1; }
	return 0;
}

/* ---- entry ------------------------------------------------------------- */

/*
 * Called from the same place fuxi_wlan_init() is (bsd/dev/sweet_ufs.c via
 * patch 0099). With fuxiwifilinux unset this returns immediately, so the r491
 * baseline is byte-identical on the wire.
 */
void
fuxi_wlan_board_init(void)
{
	if (!PE_parse_boot_argn("fuxiwifilinux", &wl_linux_variant, sizeof(wl_linux_variant))) {
		return;
	}
	if (!wl_linux_variant) { return; }
	if (wl_linux_variant > 2) {
		printf("FUXI WLAN: L1 fuxiwifilinux %u out of range (0..2); ignore\n", wl_linux_variant);
		return;
	}
	if (wl_variant_invalid || !wl_w12) {
		printf("FUXI WLAN: L1 needs fuxiwlan=1 with fuxiwlanqrtr>=2; contain\n");
		return;
	}
	if (wl_board_prepare()) { return; }
	/*
	 * Variant 1 stops here: the board table and both accessors are proven by
	 * the host tests and by a boot log, but no stack object is linked yet.
	 * Variant 2 hands the endpoint to the linked Linux stack: the old W11-W15
	 * lane is skipped from step 28 on, because both drive the same MHI/CE/HTC
	 * queues and one conversation per boot is all the firmware allows.
	 */
	if (wl_linux_variant == 2) {
		wl_linux_stack = true;
		printf("FUXI WLAN: L1 Linux stack owns MHI/CE/HTC; old W11-W15 lane skipped from step 28\n");
	}
	printf("FUXI WLAN: L1 board layer ready (fuxiwifilinux=%u)\n", wl_linux_variant);
}

/* Exposed for the host test harness: it drives the same tables XNU uses. */
const struct lx_platform *
fuxi_wlan_board_platform(void)
{
	return &wl_board_platform;
}

const struct nw_board *
fuxi_wlan_board_table(void)
{
	return &wl_board;
}

/*
 * ---- D2: clean the first BHI image just before its doorbell -------------
 *
 * The ported MHI stack fills the 512 KiB SBL image with plain stores and then
 * rings the BHI doorbell; mhi_fw_load_bhi() never calls dma_sync. The arena is
 * meant to be Normal-NC (wl_dma_nc), but if pmap_set_cache_attributes left a
 * page write-back, the endpoint fetches a stale image and never leaves PBL --
 * the r501/r502 signature. Watch the stack's own MMIO writes through
 * lx_mmio_trace (lx.h:421-422) and clean the buffer at the IMGTXDB write that
 * launches the first image. Only instrumentation around the stack's existing
 * stores: the address and size are exactly what the stack wrote.
 *
 * This block is XNU-only; the m1 host harness compiles this file without
 * fuxi_wlan.c's cache helpers.
 */
#if FUXI_WLAN_XNU
extern void wl_dma_flush_range(vm_offset_t va, size_t n);
extern void (*lx_mmio_trace)(volatile void *a, uint32_t v);

static volatile void *wl_board_bhi;
static uint64_t wl_board_bhi_addr;
static uint32_t wl_board_bhi_size;
static bool wl_board_bhi_flushed;

static void
wl_board_bhi_flush(uint64_t bus, size_t n)
{
	for (unsigned i = 0; i < WL_BOARD_DMA_SLOTS; i++) {
		if (!wl_board_dma[i].used) { continue; }
		if (bus < wl_board_dma[i].iova ||
		    bus + n > wl_board_dma[i].iova + wl_board_dma[i].size) { continue; }
		const vm_offset_t va = wl_board_dma[i].va + (bus - wl_board_dma[i].iova);
		wl_dma_flush_range(va, n);
		printf("FUXI WLAN: L1 BHI flush IOVA %#llx+%#lx\n",
		    (unsigned long long)bus, (unsigned long)n);
		return;
	}
	printf("FUXI WLAN: L1 BHI flush IOVA %#llx+%#lx outside the arena\n",
	    (unsigned long long)bus, (unsigned long)n);
}

static void
wl_board_mmio_trace(volatile void *a, uint32_t v)
{
	if (!wl_board.bar) { return; }
	const uintptr_t off = (uintptr_t)a - (uintptr_t)wl_board.bar;
	if (off >= wl_board.bar_len) { return; }
	if (!wl_board_bhi) {
		const uint32_t bhioff = *(volatile uint32_t *)((char *)wl_board.bar + 0x28);
		if (!bhioff || bhioff >= wl_board.bar_len) { return; }
		wl_board_bhi = (volatile void *)((uintptr_t)wl_board.bar + bhioff);
	}
	const uintptr_t boff = (uintptr_t)a - (uintptr_t)wl_board_bhi;
	if (boff == 0x0c) {
		wl_board_bhi_addr = (wl_board_bhi_addr & 0xffffffffull) | ((uint64_t)v << 32);
	} else if (boff == 0x08) {
		wl_board_bhi_addr = (wl_board_bhi_addr & 0xffffffff00000000ull) | v;
	} else if (boff == 0x10) {
		wl_board_bhi_size = v;
	} else if (boff == 0x18 && wl_board_bhi_size && !wl_board_bhi_flushed) {
		wl_board_bhi_flushed = true;
		wl_board_bhi_flush(wl_board_bhi_addr,
		    wl_board_bhi_size < 0x80000 ? (size_t)wl_board_bhi_size : (size_t)0x80000);
	}
}
#endif /* FUXI_WLAN_XNU */

#if FUXI_WLAN_XNU
/*
 * Wi-Fi bring-up visibility: the stack only prints on error, so a probe that
 * stalls before wiphy registration looks identical to a quiet success. Report
 * the stage the stack itself records every five seconds; the link line is
 * read from cached state only (no firmware request).
 */
static void
wl_l1_wifi_note(unsigned ms)
{
	static unsigned last;
	char link[160];

	if (nw_wifi_stats.stage < 2 || ms - last < 5000) {
		return;
	}
	last = ms;
	link[0] = '\0';
	if (nw_wifi_stats.stage >= 3) {
		nw_wifi_link_state(link, sizeof(link));
	}
	printf("FUXI WLAN: L1 wifi stage %u last_err %d frames %u scans %u bss %u link %s\n",
	    nw_wifi_stats.stage, nw_wifi_stats.last_err, nw_wifi_stats.frames,
	    nw_wifi_stats.scans, nw_wifi_stats.bss_seen, link[0] ? link : "-");
}
#endif /* FUXI_WLAN_XNU */

/*
 * Brings the endpoint up. Variant 1 returns after the board table is built and
 * leaves the conversation to the old lane; variant 2 hands MHI/CE/HTC to the
 * linked stack. The firmware allows one conversation per boot, so this is the
 * only place nw_wifi_start() is called and it is called once.
 */
int
fuxi_wlan_board_start(void)
{
	/* Step 19 already prepared the table. Preparing again would re-read config
	 * space and clear Max_Read_Request a second time, on a wire that is now
	 * trained and owned; reuse the table when it is there. */
	if (!wl_board.cfg_read && wl_board_prepare()) { return 1; }
	if (!wl_linux_stack) { return 0; }
	if (wl_linux_started) { return 0; }
	/* cfg80211's nl80211_init() binds a control channel and refuses to start
	 * without a transport. Register the kern-control endpoint before the
	 * stack binds; failure is reported by the stack start below. */
	int control = wl_control_start();
	if (control) {
		printf("FUXI WLAN: L1 control endpoint registration failed: %d\n", control);
	}
	wl_linux_start_ms = wl_linux_sample_ms = wl_board_now_ms();
	wl_linux_starting = true;
#if FUXI_WLAN_XNU
	/* D2: clean the first BHI image just before its doorbell (see the
	 * wl_board_mmio_trace block above). Installed only on this path, so the
	 * old lane and variant 1 never see it. */
	lx_mmio_trace = wl_board_mmio_trace;
#endif
	printf("FUXI WLAN: L1 stack start entered; sampling the endpoint once a second\n");
	/* No MSI reaches XNU yet: the endpoint's vectors land in the sink page.
	 * Tell the stack to poll every registered handler, so the copy-engine
	 * rings make progress instead of filling up behind an interrupt that
	 * never arrives. */
	nw_wifi_irq_mode(0);
	int error = nw_wifi_start(&wl_board_platform, &wl_board);
	wl_linux_starting = false;
	if (error) {
		printf("FUXI WLAN: L1 stack start failed: %d\n", error);
		return error;
	}
	wl_linux_started = true;
	printf("FUXI WLAN: L1 stack started\n");
	return 0;
}

/* One pump turn. Nonzero while frames move, so the caller calls again at once
 * instead of sleeping; zero means the next wait can sleep. */
int
fuxi_wlan_board_step(void)
{
	if (!wl_linux_started) { return 0; }
#if FUXI_WLAN_XNU
	{
		static unsigned last_note;
		unsigned ms = (unsigned)(wl_board_now_ms() - wl_linux_start_ms);
		if (ms - last_note >= 1000) {
			last_note = ms;
			wl_l1_wifi_note(ms);
		}
	}
#endif
	return nw_wifi_step();
}

/* Idempotent, and safe before a start: the abort path calls it on every path. */
void
fuxi_wlan_board_stop(void)
{
	if (wl_linux_started) {
		wl_linux_started = false;
		nw_wifi_stop();
		printf("FUXI WLAN: L1 stack stopped\n");
	}
	wl_control_stop();
}

/* Fault accounting of the endpoint accessors, so a board test can assert that
 * a refused access left no sticky fault behind (fuxi_wlan.c keeps the counter). */
unsigned
fuxi_wlan_board_faults(void)
{
	return fw_faults;
}
