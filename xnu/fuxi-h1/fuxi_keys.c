/*
 * FUXI keys: polled Power / Volume up / Volume down for SweetHIDButtons.
 * Hardware (live DT research/device/probe-09/live.dtb, docs-lanes/buttons.md):
 *   Power    PMK8550 SID 0, pon_hlos@1300 RT_STS 0x1310 bit 7 (KPDPWR_N_SET)
 *   Vol down PMK8550 SID 0, same register, bit 6 (RESIN_N_SET)
 *   Vol up   PM8550 SID 1, GPIO6 @0x8d00, RT_STS 0x8d10 bit 0, active low
 *            (gpio_keys/vol_up: gpios <pm8550_gpios 6 GPIO_ACTIVE_LOW>,
 *            key_vol_up_default: input, pull-up, power-source 1)
 * SPMI PMIC arbiter v7 (qcom,spmi@c42d000, EE 0): core 0x0c400000 (APID map
 * 0x2000 + 4n), cnfg 0x0c42d000 (owner 4n), obsrvr 0x0c440000 (0x20 per
 * APID), chnls 0x0c500000 (0x1000 per APID). Offsets as Linux
 * drivers/spmi/spmi-pmic-arb.c v7.
 * READ ONLY by default: the only stores issue EXT_READL on observer
 * channels. With boot-arg fuxikeys_gpiocfg=1, and only if GPIO6 is not an
 * enabled input, the Linux pinctrl defaults for that one pin are written
 * through its RW channel (volatile PMIC GPIO config, nothing else).
 *
 * All HID key events of this file pass one arbiter (fuxi_key_state.h):
 * the PMIC poller, the root-only test injection sysctl debug.fuxikeys.inject
 * (boot-arg fuxikeyinject=1) and tap-to-wake (boot-arg fuxitaptowake=1,
 * driven by bsd/dev/fuxi_touch.c), plus the boot-time key script
 * (boot-arg fuxikeyscript=S) that uses the injection path. Each HID event carries the time its new
 * level was first sampled. debug.fuxikeys.held reports what HID has seen.
 * Ported from HackiOS hackios-xnu-userland userland/src/inject.c (MIT):
 * tap-to-wake (fuxi_keys_touch_wake).
 * Ported from HackiOS hackios-xnu-kernel xnu/files/bsd/dev/redwood_buttons.c
 * (APSL 2.0): the held-duration log. Notices and details in fuxi_key_state.h.
 */
#if defined(FUXI)
#include <sys/param.h>
#include <sys/systm.h>
#include <sys/errno.h>
#include <sys/kauth.h>
#include <sys/codesign.h>
#include <sys/proc_internal.h>
#include <sys/sysctl.h>
#include <kern/clock.h>
#include <kern/locks.h>
#include <kern/thread.h>
#include <kern/thread_call.h>
#include <kern/sched_prim.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include "sweet_spmi_bus.h"
#include "fuxi_key_state.h"

extern vm_offset_t ml_io_map(vm_offset_t, vm_size_t);
extern kern_return_t thread_terminate(thread_t);
extern void sweet_hid_key_at(int key, int down, uint64_t timestamp);
extern int sweet_hid_touch_mode(void);
extern void fuxi_perf_input(void);
void fuxi_keys_init(void);
void fuxi_wifi_probe_init(void); /* bsd/dev/fuxi_wifi.c: stage 1, fuxiwifiprobe */
static unsigned tccdiag;
static void fk_tccdiag(void *arg, wait_result_t wr);
static void fk_tccdiag_probe(proc_t p);
bool fuxi_keys_touch_wake(void);

#define FK_CORE_PA     0x0c400000u
#define FK_CNFG_PA     0x0c42d000u
#define FK_OBS_PA      0x0c440000u   /* EE 0 */
#define FK_CHNLS_PA    0x0c500000u
#define FK_MAP_OFF     0x2000u
#define FK_MAX_APIDS   1024u
#define FK_PON_PPID    0x013u        /* SID 0, 0x13xx */
#define FK_GPIO_PPID   0x18du        /* SID 1, 0x8dxx = GPIO6 */
#define FK_PON_RT      0x10u
#define FK_PON_PWR     0x80u
#define FK_PON_RESIN   0x40u
#define FK_NO_APID     0xffffu
#define FK_RTC_PPID    0x0061u        /* SID 0, 0x61xx = PMK8550 rtc */
#define FK_RTC_READ    0x48u          /* rtc-pm8xxx pmk8350: read 0x6148 */

static vm_offset_t fk_core, fk_cnfg, fk_obs;
static uint16_t fk_pon_apid = FK_NO_APID, fk_gpio_apid = FK_NO_APID;
static uint32_t fk_last_status;
static bool fk_started;
/* A late completion after timeout must never be mistaken for a new sample. */
static bool fk_timed_out[1024];
static int fk_chan;
static const char *const fk_names[FUXI_KEYS] = {"power", "volume-up", "volume-down"};

static LCK_GRP_DECLARE(fk_lck_grp, "fuxi-keys");
static LCK_MTX_DECLARE(fk_hid_lock, &fk_lck_grp);
static struct fuxi_key_arbiter fk_arb;        /* fk_hid_lock */
static uint64_t fk_tap_last;                  /* fk_hid_lock */
static thread_call_t fk_inject_call;
static int fk_inject_chan;

static uint64_t
fk_ms(uint64_t abs)
{
	uint64_t ns;
	absolutetime_to_nanoseconds(abs, &ns);
	return ns / NSEC_PER_MSEC;
}

/* With fk_hid_lock held: the one place that hands Fuxi keys to HID. */
static int
fk_deliver_locked(unsigned key, unsigned source, bool down, uint64_t at)
{
	int change = fuxi_key_arbitrate(&fk_arb, key, source, down);
	if (change) {
		sweet_hid_key_at((int)key, change > 0, fuxi_key_stamp(&fk_arb, at));
	}
	return change;
}

static int
fk_deliver(unsigned key, unsigned source, bool down, uint64_t at)
{
	lck_mtx_lock(&fk_hid_lock);
	int change = fk_deliver_locked(key, source, down, at);
	lck_mtx_unlock(&fk_hid_lock);
	return change;
}

static uint32_t
fk_rd(vm_offset_t base, uint32_t off)
{
	uint32_t v = *(volatile uint32_t *)(base + off);
	__asm__ volatile("dmb oshld" ::: "memory");
	return v;
}

/* v7 can map one PPID more than once; prefer the entry owned by EE 0. */
static uint16_t
fk_find_apid(uint16_t ppid, uint32_t count, unsigned *owner_out)
{
	uint16_t found = FK_NO_APID;
	unsigned found_owner = 7;
	for (uint32_t i = 0; i < count; ++i) {
		uint32_t map = fk_rd(fk_core, FK_MAP_OFF + 4u * i);
		if (map == 0 || ((map >> 8) & 0xfffu) != ppid) {
			continue;
		}
		unsigned owner = fk_rd(fk_cnfg, 4u * i) & 7u;
		if (found == FK_NO_APID || owner == 0) {
			found = (uint16_t)i;
			found_owner = owner;
		}
	}
	*owner_out = found_owner;
	return found;
}

static bool
fk_read_locked(uint16_t apid, uint8_t reg, uint8_t *out)
{
	if (apid == FK_NO_APID || fk_timed_out[apid]) {
		return false;
	}
	uint32_t off = 0x20u * apid;
	uint32_t cmd = (1u << 27) | ((uint32_t)reg << 4); /* EXT_READL, 1 byte */
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint32_t *)(fk_obs + off) = cmd;
	__asm__ volatile("dsb osh" ::: "memory");
	for (unsigned i = 0; i < 1000; ++i) {
		fk_last_status = fk_rd(fk_obs, off + 0x08u);
		if (fk_last_status & 1u) {
			if (fk_last_status & 0x0eu) {
				return false;
			}
			*out = (uint8_t)fk_rd(fk_obs, off + 0x18u);
			return true;
		}
		delay(1);
	}
	fk_timed_out[apid] = true;
	return false;
}

static bool
fk_read(uint16_t apid, uint8_t reg, uint8_t *out)
{
	sweet_spmi_bus_lock();
	bool ok = fk_read_locked(apid, reg, out);
	sweet_spmi_bus_unlock();
	return ok;
}

/* Opt-in only (fuxikeys_gpiocfg=1): one EXT_WRITEL on the pin's RW channel. */
static bool
fk_write_locked(vm_offset_t chan, uint8_t reg, uint8_t val)
{
	*(volatile uint32_t *)(chan + 0x10u) = val;           /* WDATA0 */
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint32_t *)(chan + 0x00u) = (uint32_t)reg << 4; /* EXT_WRITEL */
	__asm__ volatile("dsb osh" ::: "memory");
	for (unsigned i = 0; i < 1000; ++i) {
		fk_last_status = fk_rd(chan, 0x08u);
		if (fk_last_status & 1u) {
			return (fk_last_status & 0x0eu) == 0;
		}
		delay(1);
	}
	return false;
}

static bool
fk_write(vm_offset_t chan, uint8_t reg, uint8_t val)
{
	sweet_spmi_bus_lock();
	bool ok = fk_write_locked(chan, reg, val);
	sweet_spmi_bus_unlock();
	return ok;
}

static bool
fk_gpio_input(uint8_t subtype, uint8_t mode, uint8_t en)
{
	unsigned dir = subtype >= 0x10u && subtype <= 0x13u ?
	    mode & 3u : (mode >> 4) & 7u;
	return (en & 0x80u) && (dir == 0 || dir == 2);
}

static bool
fk_gpio_dump(const char *when, uint8_t *subtype, uint8_t *mode, uint8_t *en)
{
	uint8_t type = 0, vin = 0, pull = 0, rt = 0;
	bool ok = fk_read(fk_gpio_apid, 0x04, &type) && type == 0x10 &&
	    fk_read(fk_gpio_apid, 0x05, subtype) &&
	    *subtype >= 0x10 && *subtype <= 0x13 &&
	    fk_read(fk_gpio_apid, 0x40, mode) &&
	    fk_read(fk_gpio_apid, 0x41, &vin) &&
	    fk_read(fk_gpio_apid, 0x42, &pull) &&
	    fk_read(fk_gpio_apid, 0x46, en) &&
	    fk_read(fk_gpio_apid, 0x10, &rt);
	printf("FUXI KEYS: %s gpio6 ok=%d type=%#x subtype=%#x mode=%#x vin=%#x pull=%#x en=%#x rt=%#x\n",
	    when, ok, type, *subtype, *mode, vin, pull, *en, rt);
	return ok;
}

static void
fk_sleep(unsigned ms)
{
	uint64_t deadline;
	clock_interval_to_deadline(ms, NSEC_PER_MSEC, &deadline);
	(void)assert_wait_deadline(&fk_chan, THREAD_UNINT, deadline);
	thread_block(THREAD_CONTINUE_NULL);
}

/* PMK8550 SID 0 rtc@6100 (qcom,pmk8350-rtc, live DT dtbo_a-0/rtc@6100):
 * seconds are four little-endian bytes at 0x6148, per Linux rtc-pm8xxx.c
 * (pmk8350_regs: ctrl 0x6146, write 0x6140, read 0x6148). Read only here. */
static bool
fk_rtc_read_raw(uint16_t apid, uint32_t *secs, uint8_t raw[4])
{
	uint8_t v[4];
	for (unsigned i = 0; i < 2; ++i) {
		if (!fk_read(apid, (uint8_t)(FK_RTC_READ + 0), &v[0]) ||
		    !fk_read(apid, (uint8_t)(FK_RTC_READ + 1), &v[1]) ||
		    !fk_read(apid, (uint8_t)(FK_RTC_READ + 2), &v[2]) ||
		    !fk_read(apid, (uint8_t)(FK_RTC_READ + 3), &v[3])) {
			return false;
		}
		/* A carry can land mid-read: the LSB must not go backwards. */
		uint8_t lsb = 0;
		if (!fk_read(apid, FK_RTC_READ, &lsb) || lsb >= v[0]) {
			break;
		}
		if (i == 1) {
			return false;
		}
	}
	raw[0] = v[0];
	raw[1] = v[1];
	raw[2] = v[2];
	raw[3] = v[3];
	*secs = (uint32_t)v[0] | ((uint32_t)v[1] << 8) |
	    ((uint32_t)v[2] << 16) | ((uint32_t)v[3] << 24);
	return true;
}

/* Boot-time only: the PMIC keeps this across reboots and power loss. */
static void
fk_rtc_set_clock(void)
{
	int enable = 1;
	if (PE_parse_boot_argn("fuxirtc", &enable, sizeof(enable)) && !enable) {
		printf("FUXI RTC: skipped (fuxirtc=0)\n");
		return;
	}
	uint32_t count = fk_rd(fk_core, 4) & 0x7ffu;
	unsigned owner = 7;
	uint16_t apid = fk_find_apid(FK_RTC_PPID, count, &owner);
	if (apid == FK_NO_APID) {
		printf("FUXI RTC: no APID for PPID %#x\n", FK_RTC_PPID);
		return;
	}
	uint32_t secs = 0;
	uint8_t raw[4] = {0};
	if (!fk_rtc_read_raw(apid, &secs, raw)) {
		printf("FUXI RTC: read failed apid=%u st=%#x\n", apid, fk_last_status);
		return;
	}
	unsigned offset = 0;
	(void)PE_parse_boot_argn("fuxirtcoffset", &offset, sizeof(offset));
	uint64_t epoch = (uint64_t)secs + (uint64_t)offset;
	printf("FUXI RTC: apid=%u/ee%u raw %02x %02x %02x %02x counter=%u offset=%u epoch=%llu\n",
	    apid, owner, raw[0], raw[1], raw[2], raw[3], secs, offset,
	    (unsigned long long)epoch);
	/* 2023-11 .. 2039-09: a blank or bogus RTC must not move the clock. */
	if (epoch < 1700000000ull || epoch > 2200000000ull) {
		printf("FUXI RTC: epoch %llu outside sanity window; clock left alone\n",
		    (unsigned long long)epoch);
		return;
	}
	clock_set_calendar_microtime((clock_sec_t)epoch, 0);
	clock_sec_t now_s = 0;
	clock_usec_t now_us = 0;
	clock_get_calendar_microtime(&now_s, &now_us);
	printf("FUXI RTC: clock set, now=%llu.%06u\n",
	    (unsigned long long)now_s, (unsigned)now_us);
}

static bool
fk_setup(void)
{
	fk_core = ml_io_map(FK_CORE_PA, 0x3000);
	fk_cnfg = ml_io_map(FK_CNFG_PA, 0x1000);
	fk_obs = ml_io_map(FK_OBS_PA, 0x8000);
	if (!fk_core || !fk_cnfg || !fk_obs) {
		printf("FUXI KEYS: MMIO mapping failed\n");
		return false;
	}
	uint32_t version = fk_rd(fk_core, 0);
	uint32_t count = fk_rd(fk_core, 4) & 0x7ffu;
	if (version < 0x70000000u || version >= 0x80000000u ||
	    count == 0 || count > FK_MAX_APIDS) {
		printf("FUXI KEYS: unsupported arbiter version=%#x apids=%u\n",
		    version, count);
		return false;
	}
	fk_rtc_set_clock();
	unsigned pon_owner, gpio_owner;
	fk_pon_apid = fk_find_apid(FK_PON_PPID, count, &pon_owner);
	fk_gpio_apid = fk_find_apid(FK_GPIO_PPID, count, &gpio_owner);
	uint8_t ptype = 0, psub = 0, pon = 0;
	bool pok = fk_read(fk_pon_apid, 0x04, &ptype) && ptype == 0x01 &&
	    fk_read(fk_pon_apid, 0x05, &psub) && psub == 0x09 &&
	    fk_read(fk_pon_apid, FK_PON_RT, &pon);
	printf("FUXI KEYS: arb=%#x apids=%u pon-apid=%u/ee%u gpio6-apid=%u/ee%u pon ok=%d type=%#x subtype=%#x rt=%#x st=%#x\n",
	    version, count, fk_pon_apid, pon_owner, fk_gpio_apid, gpio_owner,
	    pok, ptype, psub, pon, fk_last_status);
	if (!pok) {
		fk_pon_apid = FK_NO_APID;
	}

	uint8_t subtype = 0, mode = 0, en = 0;
	bool gok = fk_gpio_dump("boot", &subtype, &mode, &en);
	if (gok && !fk_gpio_input(subtype, mode, en)) {
		int cfg = 0;
		(void)PE_parse_boot_argn("fuxikeys_gpiocfg", &cfg, sizeof(cfg));
		vm_offset_t chan = 0;
		if (cfg == 1 && gpio_owner == 0) {
			chan = ml_io_map(FK_CHNLS_PA + 0x1000u * fk_gpio_apid, 0x1000);
		}
		if (chan) {
			/* Linux pinctrl-spmi-gpio order for key_vol_up_default. */
			bool wok = fk_write(chan, 0x41, 0x01) &&  /* VIN1 (power-source 1) */
			    fk_write(chan, 0x42, 0x00) &&          /* pull-up 30 uA */
			    fk_write(chan, 0x40, 0x00) &&          /* digital input */
			    fk_write(chan, 0x46, 0x80);            /* enable */
			printf("FUXI KEYS: gpio6 configured as input: ok=%d st=%#x\n",
			    wok, fk_last_status);
			gok = fk_gpio_dump("after-cfg", &subtype, &mode, &en);
		} else {
			printf("FUXI KEYS: gpio6 cfg skipped (fuxikeys_gpiocfg=%d owner=ee%u)\n",
			    cfg, gpio_owner);
		}
	}
	if (!gok || !fk_gpio_input(subtype, mode, en)) {
		printf("FUXI KEYS: gpio6 not an enabled input; volume-up withheld\n");
		fk_gpio_apid = FK_NO_APID;
	}
	if (fk_pon_apid == FK_NO_APID && fk_gpio_apid == FK_NO_APID) {
		printf("FUXI KEYS: no readable keys\n");
		return false;
	}
	printf("FUXI KEYS: polling every 20 ms (power=%d volume-up=%d volume-down=%d)\n",
	    fk_pon_apid != FK_NO_APID, fk_gpio_apid != FK_NO_APID,
	    fk_pon_apid != FK_NO_APID);
	return true;
}

static void
fk_thread(void *arg, wait_result_t result)
{
	struct fuxi_key_state keys[FUXI_KEYS] = {0};
	unsigned errors = 0;
	(void)arg;
	(void)result;
	if (!fk_setup()) {
		goto out;
	}
	for (;;) {
		uint8_t pon = 0, gpio = 1;
		uint64_t t = mach_absolute_time();
		bool pok = fk_read(fk_pon_apid, FK_PON_RT, &pon);
		bool gok = fk_read(fk_gpio_apid, 0x10, &gpio);
		bool ok[3] = {pok, gok, pok};
		int now[3] = {!!(pon & FK_PON_PWR), !(gpio & 1u), !!(pon & FK_PON_RESIN)};
		bool failed = (fk_pon_apid != FK_NO_APID && !pok) ||
		    (fk_gpio_apid != FK_NO_APID && !gok);
		if (fuxi_keys_failed(&errors, failed)) {
			/* Balance delivered downs, so HID cannot retain a stuck key. */
			for (unsigned k = 0; k < FUXI_KEYS; ++k) {
				if (keys[k].down) {
					(void)fk_deliver(k, FUXI_KEY_SRC_HW, false, mach_absolute_time());
				}
			}
			printf("FUXI KEYS: stopped after %u consecutive read failures st=%#x\n",
			    errors, fk_last_status);
			goto terminate;
		}
		for (unsigned k = 0; k < FUXI_KEYS; ++k) {
			if (fuxi_key_sample(&keys[k], ok[k], now[k], t)) {
				if (keys[k].down) {
					printf("FUXI KEYS: %s down (pon=%#x gpio6=%#x)\n", fk_names[k], pon, gpio);
				} else {
					printf("FUXI KEYS: %s up (pon=%#x gpio6=%#x) held %llu ms\n", fk_names[k],
					    pon, gpio, fk_ms(keys[k].at - keys[k].since));
				}
				fuxi_perf_input();
				(void)fk_deliver(k, FUXI_KEY_SRC_HW, keys[k].down, keys[k].at);
			}
		}
		fk_sleep(20);
	}
out:
	printf("FUXI KEYS: stopped\n");
terminate:
	(void)thread_terminate(current_thread());
	thread_block(THREAD_CONTINUE_NULL);
}

/*
 * fuxikeyscript=S: S seconds after the key thread starts, press Power (lock),
 * Power (wake), Volume Up twice and Volume Down once through the injection
 * path, so a boot without anyone at the phone shows how iOS maps each key.
 * fuxikeyscriptn=N runs only the first N steps (N=1: a single Power press,
 * to watch auto-lock after a wake). Only for device tests; it does not need
 * fuxikeyinject=1.
 */
static int fk_inject(const struct fuxi_key_inject *cmd);
static int fk_script_chan;
static unsigned fk_script_count;
static const struct { unsigned key, gap_ms; const char *why; } fk_script_steps[] = {
	{0, 8000, "power (expect lock)"},
	{0, 8000, "power (expect wake)"},
	{1, 4000, "volume-up"},
	{1, 4000, "volume-up"},
	{2, 4000, "volume-down"},
};

static void
fk_script_sleep(unsigned ms)
{
	uint64_t deadline;
	clock_interval_to_deadline(ms, NSEC_PER_MSEC, &deadline);
	(void)assert_wait_deadline(&fk_script_chan, THREAD_UNINT, deadline);
	thread_block(THREAD_CONTINUE_NULL);
}

static void
fk_script(void *arg, wait_result_t wr)
{
	(void)wr;
	unsigned delay = (unsigned)(uintptr_t)arg;
	printf("FUXI KEYS: script starts in %u s\n", delay);
	fk_script_sleep(delay * 1000u);
	for (unsigned i = 0; i < fk_script_count; ++i) {
		struct fuxi_key_inject cmd = {fk_script_steps[i].key, FUXI_KEY_INJECT_CLICK, 120};
		printf("FUXI KEYS: script step %u %s\n", i + 1, fk_script_steps[i].why);
		(void)fk_inject(&cmd);
		fk_script_sleep(fk_script_steps[i].gap_ms);
	}
	printf("FUXI KEYS: script done\n");
	(void)thread_terminate(current_thread());
	thread_block(THREAD_CONTINUE_NULL);
}

/*
 * fuxitouchscript=S: S seconds after the key thread starts, run a fixed
 * gesture loop through the normal touch HID path, so GPU/UI timing can be
 * compared between boots without anyone at the phone. Each loop: wake with
 * Power if the display is off, swipe up from the bottom edge (unlock/home),
 * three home-page swipes left and three right, pull down Spotlight and go
 * home, pull down Control Center and go home. fuxitouchscriptn=N loops
 * (default 10, at most 100). Only for device tests: the touches are synthetic
 * and finger 0 must not be in use by a real contact.
 */
extern void sweet_hid_touch(int finger, int down, int x, int y);
extern int sweet_fb_display_off;       /* iokit/Kernel/arm/SweetFramebuffer.cpp */
static unsigned fk_touch_loops = 10;

static void
fk_swipe(int x0, int y0, int x1, int y1, unsigned ms)
{
	unsigned steps = ms / 8u ? ms / 8u : 1u;
	for (unsigned i = 0; i <= steps; ++i) {
		int x = x0 + (x1 - x0) * (int)i / (int)steps;
		int y = y0 + (y1 - y0) * (int)i / (int)steps;
		fuxi_perf_input();
		sweet_hid_touch(0, 1, x, y);
		fk_script_sleep(8);
	}
	sweet_hid_touch(0, 0, x1, y1);
}

static void
fk_touch_script(void *arg, wait_result_t wr)
{
	(void)wr;
	unsigned delay = (unsigned)(uintptr_t)arg;
	printf("FUXI TOUCH SCRIPT: starts in %u s, %u loops\n", delay, fk_touch_loops);
	fk_script_sleep(delay * 1000u);
	for (unsigned loop = 1; loop <= fk_touch_loops; ++loop) {
		if (__atomic_load_n(&sweet_fb_display_off, __ATOMIC_RELAXED)) {
			struct fuxi_key_inject cmd = {0, FUXI_KEY_INJECT_CLICK, 120};
			(void)fk_inject(&cmd);
			fk_script_sleep(1500);
		}
		printf("FUXI TOUCH SCRIPT: loop %u start\n", loop);
		fk_swipe(540, 2390, 540, 1100, 250);   /* unlock, or home */
		fk_script_sleep(2000);
		for (unsigned i = 0; i < 6; ++i) {
			if (i < 3) {
				fk_swipe(900, 1300, 180, 1300, 250);
			} else {
				fk_swipe(180, 1300, 900, 1300, 250);
			}
			fk_script_sleep(1200);
		}
		fk_swipe(540, 900, 540, 1600, 250);    /* Spotlight */
		fk_script_sleep(1500);
		fk_swipe(540, 2390, 540, 1100, 250);
		fk_script_sleep(1500);
		fk_swipe(1000, 10, 1000, 1100, 250);   /* Control Center */
		fk_script_sleep(1500);
		fk_swipe(540, 2390, 540, 1100, 250);
		fk_script_sleep(1500);
		printf("FUXI TOUCH SCRIPT: loop %u done\n", loop);
	}
	printf("FUXI TOUCH SCRIPT: done\n");
	(void)thread_terminate(current_thread());
	thread_block(THREAD_CONTINUE_NULL);
}

/*
 * fuxitaps=S: S seconds after the key thread starts, run the explicit command
 * list in fuxitapcmd=STR through the same synthetic touch/key path, so a
 * remote operator can drive the UI at exact coordinates. Commands are
 * separated by ';': tX,Y tap, sX0,Y0,X1,Y1,MS swipe, wMS wait, kP key click
 * (0 power, 1 volume-up, 2 volume-down) and pMS wait then screenshot. Each
 * screenshot prints "FUXI TOUR: shot N step" so tools/fuxi-xnu-monitor.py
 * --tour-shots saves the framebuffer at that point. Coordinates are in the
 * 1080x2400 panel space. Device tests only (synthetic touches, finger 0).
 */
static const char *
fk_taps_nums(const char *p, long *out, int max, int *count)
{
	int n = 0;

	while (n < max) {
		long v = 0;
		while (*p == ',' || *p == ' ') {
			p++;
		}
		if (*p < '0' || *p > '9') {
			break;
		}
		while (*p >= '0' && *p <= '9') {
			v = v * 10 + (*p - '0');
			p++;
		}
		out[n++] = v;
	}
	*count = n;
	return p;
}

static void
fk_taps(void *arg, wait_result_t wr)
{
	unsigned delay = (unsigned)(uintptr_t)arg;
	unsigned shots = 0;
	const char *p;
	char cmds[400];

	(void)wr;
	if (!PE_parse_boot_argn("fuxitapcmd", cmds, sizeof(cmds))) {
		printf("FUXI TAPS: fuxitapcmd missing\n");
		(void)thread_terminate(current_thread());
		thread_block(THREAD_CONTINUE_NULL);
		return;
	}
	unsigned loops = 1;
	(void)PE_parse_boot_argn("fuxitapsloop", &loops, sizeof(loops));
	if (loops < 1) {
		loops = 1;
	} else if (loops > 20) {
		loops = 20;
	}
	printf("FUXI TAPS: starts in %u s, %u loops: %s\n", delay, loops, cmds);
	fk_script_sleep(delay * 1000u);
	for (unsigned pass = 0; pass < loops; pass++) {
	if (loops > 1) {
		printf("FUXI TAPS: loop %u/%u\n", pass + 1, loops);
	}
	p = cmds;
	while (*p) {
		long v[5];
		int n = 0;
		char op;
		while (*p == ';' || *p == ' ') {
			p++;
		}
		if (!*p) {
			break;
		}
		op = *p++;
		p = fk_taps_nums(p, v, 5, &n);
		while (*p && *p != ';') {
			p++;
		}
		if (op == 't' && n >= 2) {
			printf("FUXI TAPS: tap %ld,%ld\n", v[0], v[1]);
			fuxi_perf_input();
			sweet_hid_touch(0, 1, (int)v[0], (int)v[1]);
			fk_script_sleep(80);
			sweet_hid_touch(0, 0, (int)v[0], (int)v[1]);
			fk_script_sleep(1200);
			printf("FUXI TOUR: shot %u step\n", ++shots);
		} else if (op == 's' && n >= 4) {
			printf("FUXI TAPS: swipe %ld,%ld -> %ld,%ld in %ld ms\n", v[0], v[1], v[2], v[3],
			    n >= 5 ? v[4] : 250);
			fk_swipe((int)v[0], (int)v[1], (int)v[2], (int)v[3],
			    n >= 5 ? (unsigned)v[4] : 250u);
			fk_script_sleep(1200);
			printf("FUXI TOUR: shot %u step\n", ++shots);
		} else if (op == 'w' && n >= 1) {
			printf("FUXI TAPS: wait %ld ms\n", v[0]);
			fk_script_sleep((unsigned)v[0]);
		} else if (op == 'p' && n >= 1) {
			printf("FUXI TAPS: shot after %ld ms\n", v[0]);
			fk_script_sleep((unsigned)v[0]);
			printf("FUXI TOUR: shot %u step\n", ++shots);
		} else if (op == 'k' && n >= 1) {
			struct fuxi_key_inject cmd = {(unsigned)v[0], FUXI_KEY_INJECT_CLICK, 120};
			printf("FUXI TAPS: key %ld %s\n", v[0],
			    v[0] == 0 ? "power" : v[0] == 1 ? "volume-up" :
			    v[0] == 2 ? "volume-down" : "");
			(void)fk_inject(&cmd);
			fk_script_sleep(1200);
		} else {
			printf("FUXI TAPS: bad command '%c' with %d numbers\n", op, n);
		}
	}
	}
	printf("FUXI TAPS: done, %u shots, %u loops\n", shots, loops);
	(void)thread_terminate(current_thread());
	thread_block(THREAD_CONTINUE_NULL);
}

/*
 * fuxitour=S: S seconds after the key thread starts, walk a fixed tour of
 * screens for screenshots: lock screen, home, Today view, Spotlight, Control
 * Center, Settings (General, About), Clock, Calendar and Photos. Each screen
 * prints "FUXI TOUR: shot N name" and holds for fuxitourhold=H seconds
 * (default 45) so the USB monitor can read the framebuffer. Tap targets match
 * the stock iOS 16 home layout at 1080x2400; fuxitourg=Y and fuxitoura=Y move
 * the General and About rows in Settings. Device tests only (synthetic touch).
 */
static unsigned fk_tour_hold = 45, fk_tour_general = 2100, fk_tour_about = 400;

static void
fk_tap(int x, int y)
{
	fuxi_perf_input();
	sweet_hid_touch(0, 1, x, y);
	fk_script_sleep(80);
	sweet_hid_touch(0, 0, x, y);
}

static void
fk_tour_shot(unsigned *shot, const char *name)
{
	printf("FUXI TOUR: shot %u %s\n", ++*shot, name);
	fk_script_sleep(fk_tour_hold * 1000u);
}

static void
fk_tour_home(void)
{
	fk_swipe(540, 2390, 540, 1100, 250);
	fk_script_sleep(2000);
}

static void
fk_tour_app(unsigned *shot, int x, int y, const char *name)
{
	fk_tap(x, y);
	fk_script_sleep(4000);
	fk_tour_shot(shot, name);
}

static void
fk_tour(void *arg, wait_result_t wr)
{
	(void)wr;
	unsigned shot = 0;
	printf("FUXI TOUR: starts in %u s, hold %u s\n", (unsigned)(uintptr_t)arg, fk_tour_hold);
	fk_script_sleep((unsigned)(uintptr_t)arg * 1000u);
	if (__atomic_load_n(&sweet_fb_display_off, __ATOMIC_RELAXED)) {
		struct fuxi_key_inject cmd = {0, FUXI_KEY_INJECT_CLICK, 120};
		(void)fk_inject(&cmd);
		fk_script_sleep(2000);
	}
	fk_tour_shot(&shot, "lock");
	fk_tour_home();
	fk_tour_shot(&shot, "home");
	/* App Library is past the last home page; extra swipes there are ignored. */
	for (unsigned i = 0; i < 4; ++i) {
		fk_swipe(900, 1300, 180, 1300, 250);
		fk_script_sleep(1200);
	}
	fk_tour_shot(&shot, "app-library");
	fk_script_sleep(8000);
	fk_tour_shot(&shot, "app-library-idle");
	fk_script_sleep(20000);
	fk_tour_shot(&shot, "app-library-idle-30s");
	fk_tour_home();
	fk_tour_home();
	fk_swipe(60, 1300, 1020, 1300, 450);    /* Today view */
	fk_script_sleep(2000);
	fk_tour_shot(&shot, "today");
	fk_tour_home();
	fk_tour_home();
	fk_swipe(540, 900, 540, 1600, 250);     /* Spotlight */
	fk_script_sleep(2000);
	fk_tour_shot(&shot, "spotlight");
	fk_tour_home();
	fk_swipe(1000, 10, 1000, 1100, 250);    /* Control Center */
	fk_script_sleep(2000);
	fk_tour_shot(&shot, "control-center");
	/* A home gesture on another page only returns to page 1 the second time. */
	fk_tour_home();
	fk_tour_home();
	/* Settings reopens its last page: tap Back twice to reach the top list. */
	fk_tap(670, 1710);
	fk_script_sleep(4000);
	fk_tap(110, 180);
	fk_script_sleep(1500);
	fk_tap(110, 180);
	fk_script_sleep(2000);
	fk_tour_shot(&shot, "settings");
	fk_tour_app(&shot, 540, (int)fk_tour_general, "general");
	fk_tour_app(&shot, 540, (int)fk_tour_about, "about");
	fk_tour_home();
	fk_tour_app(&shot, 670, 1140, "clock");
	fk_tour_home();
	fk_tour_app(&shot, 410, 860, "calendar");
	fk_tour_home();
	fk_tour_app(&shot, 670, 860, "photos");
	fk_tour_home();
	printf("FUXI TOUR: done\n");
	(void)thread_terminate(current_thread());
	thread_block(THREAD_CONTINUE_NULL);
}

/*
 * fuxiautolock=S (10..3600): iOS turns the display off ~30 s after boot but
 * never again after a Power wake (docs-lanes/backlight.md). Once the display
 * has gone off and on at least once, press Power when it has been on for S
 * seconds with no key or touch report, so iOS locks through its normal path.
 * Opt-in: this also ends playback that holds the screen awake without input.
 */
extern int sweet_fb_display_off;       /* iokit/Kernel/arm/SweetFramebuffer.cpp */
extern uint64_t sweet_hid_last_input;  /* iokit/Kernel/arm/SweetHID.cpp */
static int fk_autolock_chan;

static void
fk_autolock(void *arg, wait_result_t wr)
{
	(void)wr;
	unsigned secs = (unsigned)(uintptr_t)arg;
	uint64_t limit, on_since = 0, now;
	bool armed = false, was_off = false;
	unsigned presses = 0;
	clock_interval_to_absolutetime_interval(secs, NSEC_PER_SEC, &limit);
	printf("FUXI KEYS: autolock after %u s without input (armed after the first display off)\n", secs);
	for (;;) {
		uint64_t deadline;
		clock_interval_to_deadline(1000, NSEC_PER_MSEC, &deadline);
		(void)assert_wait_deadline(&fk_autolock_chan, THREAD_UNINT, deadline);
		thread_block(THREAD_CONTINUE_NULL);
		now = mach_absolute_time();
		bool off = __atomic_load_n(&sweet_fb_display_off, __ATOMIC_RELAXED) != 0;
		if (off) {
			armed = was_off = true;
			continue;
		}
		if (was_off) {
			was_off = false;
			on_since = now;
		}
		if (!armed) {
			continue;
		}
		uint64_t last = __atomic_load_n(&sweet_hid_last_input, __ATOMIC_RELAXED);
		if (last < on_since) {
			last = on_since;
		}
		if (now - last < limit) {
			continue;
		}
		struct fuxi_key_inject cmd = {0, FUXI_KEY_INJECT_CLICK, 120};
		presses++;
		if (presses <= 16 || (presses % 64) == 0) {
			printf("FUXI KEYS: autolock press %u after %u s without input\n", presses, secs);
		}
		(void)fk_inject(&cmd);
		/* The click is itself input; wait a full period before trying again. */
		on_since = mach_absolute_time();
	}
}

void
fuxi_keys_init(void)
{
	int enabled = 1;
	thread_t thread;
	if (fk_started) {
		return;
	}
	fk_started = true;
	(void)PE_parse_boot_argn("fuxikeys", &enabled, sizeof(enabled));
	if (!enabled) {
		printf("FUXI KEYS: disabled (fuxikeys=0)\n");
		return;
	}
	if (kernel_thread_start(fk_thread, NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	} else {
		printf("FUXI KEYS: kernel_thread_start failed\n");
	}
	unsigned autolock = 0;
	if (PE_parse_boot_argn("fuxiautolock", &autolock, sizeof(autolock)) && autolock >= 10 && autolock <= 3600) {
		if (kernel_thread_start(fk_autolock, (void *)(uintptr_t)autolock, &thread) == KERN_SUCCESS) {
			thread_deallocate(thread);
		} else {
			printf("FUXI KEYS: autolock thread_start failed\n");
		}
	}
	unsigned script = 0;
	if (PE_parse_boot_argn("fuxikeyscript", &script, sizeof(script)) && script > 0 && script < 3600) {
		unsigned steps = sizeof(fk_script_steps) / sizeof(fk_script_steps[0]);
		fk_script_count = steps;
		if (PE_parse_boot_argn("fuxikeyscriptn", &fk_script_count, sizeof(fk_script_count)) &&
		    (fk_script_count == 0 || fk_script_count > steps)) {
			fk_script_count = steps;
		}
		if (kernel_thread_start(fk_script, (void *)(uintptr_t)script, &thread) == KERN_SUCCESS) {
			thread_deallocate(thread);
		} else {
			printf("FUXI KEYS: script thread_start failed\n");
		}
	}
	unsigned tour = 0;
	if (PE_parse_boot_argn("fuxitour", &tour, sizeof(tour)) && tour > 0 && tour < 3600) {
		unsigned v;
		if (PE_parse_boot_argn("fuxitourhold", &v, sizeof(v)) && v >= 5 && v <= 300) {
			fk_tour_hold = v;
		}
		if (PE_parse_boot_argn("fuxitourg", &v, sizeof(v)) && v < 2400) {
			fk_tour_general = v;
		}
		if (PE_parse_boot_argn("fuxitoura", &v, sizeof(v)) && v < 2400) {
			fk_tour_about = v;
		}
		if (kernel_thread_start(fk_tour, (void *)(uintptr_t)tour, &thread) == KERN_SUCCESS) {
			thread_deallocate(thread);
		} else {
			printf("FUXI TOUR: thread_start failed\n");
		}
	}
	unsigned touch_script = 0;
	if (PE_parse_boot_argn("fuxitouchscript", &touch_script, sizeof(touch_script)) &&
	    touch_script > 0 && touch_script < 3600) {
		if (PE_parse_boot_argn("fuxitouchscriptn", &fk_touch_loops, sizeof(fk_touch_loops)) &&
		    (fk_touch_loops == 0 || fk_touch_loops > 100)) {
			fk_touch_loops = 10;
		}
		if (kernel_thread_start(fk_touch_script, (void *)(uintptr_t)touch_script, &thread) ==
		    KERN_SUCCESS) {
			thread_deallocate(thread);
		} else {
			printf("FUXI TOUCH SCRIPT: thread_start failed\n");
		}
	}
	unsigned taps = 0;
	if (PE_parse_boot_argn("fuxitccdiag", &tccdiag, sizeof(tccdiag)) &&
	    tccdiag > 0 && tccdiag < 3600) {
		if (kernel_thread_start(fk_tccdiag, (void *)(uintptr_t)tccdiag, &thread) ==
		    KERN_SUCCESS) {
			thread_deallocate(thread);
		} else {
			printf("FUXI TCC: thread_start failed\n");
		}
	}
	if (PE_parse_boot_argn("fuxitaps", &taps, sizeof(taps)) && taps > 0 && taps < 3600) {
		if (kernel_thread_start(fk_taps, (void *)(uintptr_t)taps, &thread) == KERN_SUCCESS) {
			thread_deallocate(thread);
		} else {
			printf("FUXI TAPS: thread_start failed\n");
		}
	}
	fuxi_wifi_probe_init();
}
/*
 * fuxitccdiag=S: S seconds after init, print what the kernel can see about the
 * code signing state of the processes that decide TCC answers. Evidence only.
 */
static const char *fk_tccdiag_want[] = {
	"assetsd", "tccd", "SpringBoard", "launchd", "MobileSlideShow", "photolibraryd"
};

static void
fk_tccdiag_probe(proc_t p)
{
	const char *n = p->p_comm;
	if (!n) {
		return;
	}
	void *blob = NULL;
	size_t len = 0;
	int rc = cs_entitlements_blob_get(p, &blob, &len);
	printf("FUXI TCC: pid %d %s flags %#x ent rc=%d len=%lu blob=%p\n",
	    p->p_pid, n, proc_getcsflags(p), rc, (unsigned long)len, blob);
}

static void
fk_tccdiag(void *arg, wait_result_t wr)
{
	uintptr_t when = (uintptr_t)arg;
	(void)wr;
	fk_script_sleep((uint32_t)when * 1000u);
	printf("FUXI TCC: dump at %lu s\n", (unsigned long)when);
	unsigned found = 0, seen = 0;
	for (int pid = 1; pid <= 400; pid++) {
		proc_t p = proc_find(pid);
		if (p == PROC_NULL || p == NULL) {
			continue;
		}
		seen++;
		const char *n = p->p_comm;
		if (seen <= 10 && n) {
			printf("FUXI TCC: scan pid %d comm '%s'\n", pid, n);
		}
		if (n) {
			for (unsigned i = 0; i < sizeof(fk_tccdiag_want) / sizeof(fk_tccdiag_want[0]); i++) {
				if (strcmp(n, fk_tccdiag_want[i]) == 0) {
					fk_tccdiag_probe(p);
					found++;
					break;
				}
			}
		}
		proc_rele(p);
	}
	printf("FUXI TCC: done, seen %u procs, matched %u\n", seen, found);
}
/* Injected downs are released at the latest FUXI_KEY_INJECT_HOLD_MAX_MS later. */
static void
fk_inject_expire(thread_call_param_t p0, thread_call_param_t p1)
{
	(void)p0;
	(void)p1;
	uint64_t now = mach_absolute_time();
	lck_mtx_lock(&fk_hid_lock);
	unsigned mask = fuxi_key_held_mask(&fk_arb, FUXI_KEY_SRC_INJECT);
	for (unsigned k = 0; k < FUXI_KEYS; ++k) {
		if (mask & (1u << k)) {
			(void)fk_deliver_locked(k, FUXI_KEY_SRC_INJECT, false, now);
		}
	}
	lck_mtx_unlock(&fk_hid_lock);
	if (mask) {
		printf("FUXI KEYS: inject auto-release mask=%#x after %u ms\n", mask,
		    FUXI_KEY_INJECT_HOLD_MAX_MS);
	}
}

static int
fk_inject_one(unsigned key, bool down)
{
	int change = fk_deliver(key, FUXI_KEY_SRC_INJECT, down, mach_absolute_time());
	printf("FUXI KEYS: inject %s %s -> hid %s\n", fk_names[key], down ? "down" : "up",
	    change > 0 ? "down" : change < 0 ? "up" : "unchanged");
	if (change) {
		fuxi_perf_input();
	}
	return change;
}

static int
fk_inject(const struct fuxi_key_inject *cmd)
{
	if (!fk_inject_call) {
		thread_call_t call = thread_call_allocate(fk_inject_expire, NULL);
		if (!call) {
			return ENOMEM;
		}
		lck_mtx_lock(&fk_hid_lock);
		if (!fk_inject_call) {
			fk_inject_call = call;
			call = NULL;
		}
		lck_mtx_unlock(&fk_hid_lock);
		if (call) {
			(void)thread_call_free(call);
		}
	}
	if (cmd->op != FUXI_KEY_INJECT_UP) {
		uint64_t deadline;
		clock_interval_to_deadline(FUXI_KEY_INJECT_HOLD_MAX_MS, NSEC_PER_MSEC, &deadline);
		(void)thread_call_enter_delayed(fk_inject_call, deadline);
		(void)fk_inject_one(cmd->key, true);
	}
	if (cmd->op == FUXI_KEY_INJECT_CLICK) {
		uint64_t deadline;
		clock_interval_to_deadline(cmd->hold_ms, NSEC_PER_MSEC, &deadline);
		(void)assert_wait_deadline(&fk_inject_chan, THREAD_UNINT, deadline);
		thread_block(THREAD_CONTINUE_NULL);
	}
	if (cmd->op != FUXI_KEY_INJECT_DOWN) {
		(void)fk_inject_one(cmd->key, false);
	}
	return 0;
}

/*
 * debug.fuxikeys.inject: write a command word (fuxi_key_state.h) to press or
 * release a key through the same arbiter and HID path as the PMIC keys.
 * Root only (no CTLFLAG_ANYBODY, checked again here) and only with boot-arg
 * fuxikeyinject=1; otherwise writes fail with ENOTSUP. Reads return the
 * keys currently held by injection.
 */
static int
fk_sysctl_inject SYSCTL_HANDLER_ARGS
{
#pragma unused(oidp, arg1, arg2)
	lck_mtx_lock(&fk_hid_lock);
	int value = (int)fuxi_key_held_mask(&fk_arb, FUXI_KEY_SRC_INJECT), changed = 0;
	lck_mtx_unlock(&fk_hid_lock);
	int error = sysctl_io_number(req, value, sizeof(value), &value, &changed);
	if (error || !changed) {
		return error;
	}
	int enabled = 0;
	if (!PE_parse_boot_argn("fuxikeyinject", &enabled, sizeof(enabled)) || enabled != 1) {
		return ENOTSUP;
	}
	if (!kauth_cred_issuser(kauth_cred_get())) {
		return EPERM;
	}
	struct fuxi_key_inject cmd;
	if ((error = fuxi_key_inject_decode(value, &cmd))) {
		return error;
	}
	return fk_inject(&cmd);
}

/*
 * debug.fuxikeys.held: bits 0-2 the keys HID has down (power, volume up,
 * volume down); bits 8-10 held by the PMIC, 16-18 by injection, 24-26 by
 * tap-to-wake. A SpringBoard-side long-press guard can read bit 0.
 */
static int
fk_sysctl_held SYSCTL_HANDLER_ARGS
{
#pragma unused(oidp, arg1, arg2)
	lck_mtx_lock(&fk_hid_lock);
	int value = (int)(fuxi_key_held_mask(&fk_arb, ~0u) |
	    fuxi_key_held_mask(&fk_arb, FUXI_KEY_SRC_HW) << 8 |
	    fuxi_key_held_mask(&fk_arb, FUXI_KEY_SRC_INJECT) << 16 |
	    fuxi_key_held_mask(&fk_arb, FUXI_KEY_SRC_TAP) << 24);
	lck_mtx_unlock(&fk_hid_lock);
	return SYSCTL_OUT(req, &value, sizeof(value));
}

SYSCTL_NODE(_debug, OID_AUTO, fuxikeys, CTLFLAG_RW | CTLFLAG_LOCKED, 0, "Fuxi buttons");
SYSCTL_PROC(_debug_fuxikeys, OID_AUTO, inject, CTLTYPE_INT | CTLFLAG_RW | CTLFLAG_LOCKED,
    0, 0, fk_sysctl_inject, "I", "inject a key event (root, boot-arg fuxikeyinject=1)");
SYSCTL_PROC(_debug_fuxikeys, OID_AUTO, held, CTLTYPE_INT | CTLFLAG_RD | CTLFLAG_LOCKED,
    0, 0, fk_sysctl_held, "I", "keys held as HID sees them, and by which source");

/*
 * Called by bsd/dev/fuxi_touch.c for a new contact while the display is off.
 * With boot-arg fuxitaptowake=1 and TouchDetectionMode 0xfe, press and
 * release power (as HackiOS does) and return true: the touch driver then
 * keeps the rest of that contact from iOS. Not while power is held.
 */
bool
fuxi_keys_touch_wake(void)
{
	static int enabled = -1;
	static uint64_t interval;
	if (enabled < 0) {
		int on = 0;
		(void)PE_parse_boot_argn("fuxitaptowake", &on, sizeof(on));
		nanoseconds_to_absolutetime(1500 * NSEC_PER_MSEC, &interval);
		enabled = on == 1;
	}
	if (!enabled) {
		return false;
	}
	int mode = sweet_hid_touch_mode();
	uint64_t now = mach_absolute_time();
	lck_mtx_lock(&fk_hid_lock);
	bool go = fk_arb.held[0] == 0 && fuxi_tap_wake_allowed(mode, now, &fk_tap_last, interval);
	if (go) {
		(void)fk_deliver_locked(0, FUXI_KEY_SRC_TAP, true, now);
		(void)fk_deliver_locked(0, FUXI_KEY_SRC_TAP, false, now);
	}
	lck_mtx_unlock(&fk_hid_lock);
	if (go) {
		fuxi_perf_input();
		printf("FUXI KEYS: tap-to-wake power click (touch mode %#x)\n", mode);
	}
	return go;
}

/*
 * WiFi stage 1: a read-only look at what the boot loaders left of the
 * WCN7850 path on the fuxi board (boot-arg fuxiwifiprobe=STAGE).
 * Hardware facts from the live DT (build/live.dtb): GCC 0x100000, PCIe0 PARF
 * 0x1c00000, PHY 0x1c06000, DBI 0x60000000, pinctrl@f000000 (16 MiB);
 * WLAN_EN GPIO 80, SW_CTRL GPIO 82, PERST# GPIO 94 (active low), WAKE# GPIO 96.
 *
 * The register map and the flow come from UniXNU-sheng's package/WifiProbe.cpp
 * (BSD-2-Clause-Patent). Its warning applies here too: "a read of an unclocked
 * block stalls the bus", so every block is staged and announced first:
 *   stage 1  GCC votes, PCIe0 GDSC and branch halt registers (always clocked)
 *   stage 2  TLMM pin config words (only if asked; the key driver talks GPIO
 *            over SPMI, so the TLMM may be unclocked on this board)
 *   stage 3  PCIe0 PARF/DBI, and only when the GDSC is on and the four
 *            required branches run
 * Nothing here writes a register.
 */
#define FW_GCC		0x00100000ULL
#define FW_TLMM		0x0f000000ULL
#define FW_PARF		0x01c00000ULL
#define FW_DBI		0x60000000ULL
#define FW_VOTE8	0x52008u
#define FW_VOTE0	0x52000u
#define FW_GDSC		0x6b004u
#define FW_PARF_LTSSM	0x1b0u
#define FW_PARF_PHYCTL	0x40u
#define FW_LINK_CONTROL	0x80u

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

static const uint32_t fw_branches[] = {
	0x6b020, 0x6b02c, 0x6b038, 0x6b03c, 0x6b048,
	0x6b01c, 0x10030, 0x1003c, 0x1004c, 0x6b054,
};
static const unsigned fw_pins[] = { 80, 82, 94 };

static void
fk_wifi_thread(void *arg, wait_result_t wr)
{
	volatile uint32_t *gcc;
	uint32_t vote8, vote0, gdsc, halt = 0, needed;
	unsigned stage = (unsigned)(uintptr_t)arg;
	unsigned i;

	(void)wr;
	thread_set_thread_name(current_thread(), "fuxi-wifi");
	printf("FUXI WIFI: stage %u starting\n", stage);

	printf("FUXI WIFI: mapping gcc\n");
	gcc = (volatile uint32_t *)ml_io_map(FW_GCC, 0x80000);
	if (!gcc) {
		printf("FUXI WIFI: gcc map failed\n");
		return;
	}
	vote8 = gcc[FW_VOTE8 / 4u];
	vote0 = gcc[FW_VOTE0 / 4u];
	gdsc = gcc[FW_GDSC / 4u];
	for (i = 0; i < sizeof fw_branches / sizeof fw_branches[0]; i++) {
		if (gcc[fw_branches[i] / 4u] & (1u << 31)) {
			halt |= 1u << i;
		}
	}
	needed = halt & 0xfu;
	printf("FUXI WIFI: gcc vote8 %#x vote0 %#x gdsc %#x halt %#x\n",
	    vote8, vote0, gdsc, halt);

	if (stage >= 2) {
		volatile uint32_t *tlmm;
		uint32_t word = 0;

		printf("FUXI WIFI: mapping tlmm@f000000\n");
		tlmm = (volatile uint32_t *)ml_io_map(FW_TLMM, 0x1000000);
		if (tlmm) {
			for (i = 0; i < sizeof fw_pins / sizeof fw_pins[0]; i++) {
				word |= (tlmm[(fw_pins[i] * 0x1000u + 4u) / 4u] & 3u) << (8u * i);
			}
			printf("FUXI WIFI: tlmm pins %#x\n", word);
		} else {
			printf("FUXI WIFI: tlmm map failed\n");
		}
	}

	if (stage >= 3) {
		if ((gdsc & (1u << 31)) && needed == 0) {
			volatile uint32_t *parf, *dbi;

			printf("FUXI WIFI: mapping parf/dbi\n");
			parf = (volatile uint32_t *)ml_io_map(FW_PARF, 0x3000);
			dbi = (volatile uint32_t *)ml_io_map(FW_DBI, 0x1000);
			if (parf && dbi) {
				printf("FUXI WIFI: parf ltssm %#x phyctl %#x\n",
				    parf[FW_PARF_LTSSM / 4u], parf[FW_PARF_PHYCTL / 4u]);
				printf("FUXI WIFI: ep vendor %#x device %#x link %#x\n",
				    dbi[0], dbi[1], dbi[FW_LINK_CONTROL / 4u]);
			} else {
				printf("FUXI WIFI: parf/dbi map failed\n");
			}
		} else {
			printf("FUXI WIFI: controller read skipped (gdsc on %u, clocks off %#x)\n",
			    (gdsc >> 31) & 1u, needed);
		}
	}
	printf("FUXI WIFI: stage 1 done\n");
}

void
fuxi_wifi_probe_init(void)
{
	unsigned stage = 0;
	thread_t thread = THREAD_NULL;

	if (!PE_parse_boot_argn("fuxiwifiprobe", &stage, sizeof stage) || stage == 0) {
		return;
	}
	if (kernel_thread_start(fk_wifi_thread, (void *)(uintptr_t)stage, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	} else {
		printf("FUXI WIFI: thread start failed\n");
	}
}
#endif /* FUXI */
