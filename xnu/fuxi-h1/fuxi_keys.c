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
void fuxi_wifi_probe_init(void); /* fuxiwifiprobe: WiFi stage 1 read-only, stage 2 rails */
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
 * WiFi bring-up probe (boot-arg fuxiwifiprobe=STAGE), read-only except stage 2.
 *
 * Hardware from the live DT (build/live.dtb) and docs-lanes:
 *   GCC 0x100000, PCIe0 PARF 0x1c00000, PHY 0x1c06000, DBI 0x60000000,
 *   pinctrl@f000000; apps_rsc DRV2 0x17a20000 (tcs-offset 0xd00),
 *   AOP cmd-db 0x81c60000 (0x20000); PMICs PM8550/PM8550VS/PM8550VE.
 *   WLAN_EN GPIO 80, SW_CTRL GPIO 82, PERST# GPIO 94 (active low).
 * The register map, the rail list and the request format come from
 * UniXNU-sheng package/{WifiProbe,QcomRpmh,WcnPower} (BSD-2-Clause-Patent).
 *
 *   stage 1  GCC votes, PCIe0 GDSC and the branch halt registers (read only)
 *   stage 2  stage 1 + vote the six WCN7850 rails ON through the apps RSC,
 *            then read the GCC state again (writes: RPMh rail votes only)
 *   stage 3  reserved for WLAN_EN/PERST#/GDSC/PCIe training
 * Nothing else writes a register. A block that is not clocked is never read:
 * the PCIe controller registers only after the GDSC is on and the four
 * required branches run.
 */
#define FW_GCC		0x00100000ULL
#define FW_PARF		0x01c00000ULL
#define FW_DBI		0x60000000ULL
#define FW_RSC		0x17a20000ULL
#define FW_CMDD_B	0x81c60000ULL
#define FW_CMDD_SZ	0x20000u
#define FW_TCS		0xd00u
#define FW_VOTE8	0x52008u
#define FW_VOTE0	0x52000u
#define FW_GDSC		0x6b004u
#define FW_PARF_LTSSM	0x1b0u
#define FW_PARF_PHYCTL	0x40u
#define FW_LINK_CONTROL	0x80u

#define FW_MSGID_WR	(8u | (1u << 8) | (1u << 16))
#define FW_CTL_ACTIVE	(1u << 16)
#define FW_CTL_TRIGGER	(1u << 24)
#define FW_DB_MAGIC	0x0c0330dbu
#define FW_TLMM	0x0f100000ULL
#define FW_TLMM_SZ	0x60000u
#define FW_GCC_SZ	0x80000u
#define FW_WLAN_EN	80u
#define FW_CLKREQ	95u
#define FW_PERST	94u
#define FW_PHY		0x01c06000ULL
#define FW_PHY_SZ	0x2000u
#define FW_ATU		0x60001000ULL
#define FW_CONFIG	0x60100000ULL
#define FW_TCSR		0x1fd5000ULL
#define FW_AUXRCG	0x6b074u
#define FW_RCHNGRCG	0x6b058u
#define FW_PIPEMUX	0x6b070u
#define FW_PCIEBRC	0x6b000u
#define FW_PHYBRC	0x6c01cu
#define FW_COLLAPSE	0x52020u
#define FW_PHYGDSC	0x6c000u
#define FW_VOTE8B	0x7fu
#define FW_VOTE0B	((1u << 12) | (1u << 19) | (1u << 22))
#define FW_MEMBASE	0x60300000u
#define FW_MEMSIZE	0x3d00000u
#define FW_MHI_SZ	0x200000u
#define FW_PARF_SZ	0x3000u
#define FW_DBI		0x60000000ULL
#define FW_DBI_SZ	0x1000u

struct fw_rail {
	const char *name;
	uint32_t millivolts;
};

/* WCN7850 PMU supplies, in the order the power sequencer takes them. */
static const struct fw_rail fw_rails[] = {
	{ "smpg5", 850 }, { "ldob15", 1800 }, { "smpg2", 850 },
	{ "smpe4", 950 }, { "smpg4", 1300 }, { "smpg6", 1860 },
};

struct fw_rsc_layout {
	uint32_t tcs_stride, cmd_stride, irq_status, irq_clear, wait_for_cmpl,
	    control, status, cmd_enable, cmd_msgid, cmd_addr, cmd_data;
};

static uint32_t fw_stage;

static const uint32_t fw_branches[] = {
	0x6b020, 0x6b02c, 0x6b038, 0x6b03c, 0x6b048,
	0x6b01c, 0x10030, 0x1003c, 0x1004c, 0x6b054,
};

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

static struct fw_rsc_layout
fw_layout(uint32_t drv_id)
{
	struct fw_rsc_layout v3 = { 672, 24, 0x04, 0x08, 0x20, 0x24, 0x28,
		0x2c, 0x34, 0x38, 0x3c };
	struct fw_rsc_layout v2 = { 672, 20, 0x04, 0x08, 0x10, 0x14, 0x18,
		0x1c, 0x30, 0x34, 0x38 };
	return ((drv_id >> 16) & 0xffu) >= 3u ? v3 : v2;
}

static uint16_t
fw_le16(const uint8_t *p)
{
	return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t
fw_le32(const uint8_t *p)
{
	return (uint32_t)fw_le16(p) | ((uint32_t)fw_le16(p + 2) << 16);
}

/* AOP command database: header, then 8 slave records, then the entries the
 * offsets count from (byte 144). Entries are 24 bytes with an 8-byte name. */
static int
fw_db_find(const uint8_t *db, size_t bytes, const char *name, uint32_t *address)
{
	char id[8] = { 0 };
	unsigned s, i;

	if (bytes < 144 || fw_le32(db + 4) != FW_DB_MAGIC) {
		return -1;
	}
	for (i = 0; i < 8 && name[i]; i++) {
		id[i] = name[i];
	}
	for (s = 0; s < 8; s++) {
		const uint8_t *slave = db + 8 + 16 * s;
		uint32_t heads, count, e;

		if (!fw_le16(slave)) {
			break;
		}
		heads = 144u + fw_le16(slave + 2);
		count = fw_le16(slave + 6);
		if ((size_t)heads + (size_t)count * 24 > bytes) {
			return -1;
		}
		for (e = 0; e < count; e++) {
			const uint8_t *entry = db + heads + 24 * e;
			unsigned same = 1;

			for (i = 0; i < 8 && same; i++) {
				same = entry[i] == (uint8_t)id[i];
			}
			if (!same) {
				continue;
			}
			*address = fw_le32(entry + 16);
			return *address ? 0 : -1;
		}
	}
	return -1;
}

/* One active-only request of a single command in TCS 0, applied in order and
 * released after two milliseconds (the RSC's done latch reads zero here, as
 * sheng found with the same firmware). */
static void
fw_rpmh_send(volatile uint32_t *r, const struct fw_rsc_layout *l, uint32_t addr,
    uint32_t value)
{
	const uint32_t b = FW_TCS + l->tcs_stride * 0;
	const uint32_t cmd = b + l->cmd_stride * 0;

	r[(b + l->control) / 4] = 0;
	r[(b + l->irq_clear) / 4] = 1u << 0;
	r[(cmd + l->cmd_msgid) / 4] = FW_MSGID_WR;
	r[(cmd + l->cmd_addr) / 4] = addr;
	r[(cmd + l->cmd_data) / 4] = value;
	r[(b + l->wait_for_cmpl) / 4] = 1u;
	r[(b + l->cmd_enable) / 4] = 1u;
	r[(b + l->control) / 4] = FW_CTL_ACTIVE;
	__asm__ volatile("dsb sy" ::: "memory");
	r[(b + l->control) / 4] = FW_CTL_ACTIVE | FW_CTL_TRIGGER;
	__asm__ volatile("dsb sy" ::: "memory");

	delay(2000);

	r[(b + l->irq_clear) / 4] = 1u << 0;
	r[(b + l->control) / 4] = 0;
	r[(b + l->cmd_enable) / 4] = 0;
	__asm__ volatile("dsb sy" ::: "memory");
}

static void
fw_read_gcc(const char *tag)
{
	volatile uint32_t *gcc;
	uint32_t vote8, vote0, gdsc, halt = 0;
	unsigned i;

	gcc = (volatile uint32_t *)ml_io_map(FW_GCC, 0x80000);
	if (!gcc) {
		printf("FUXI WIFI: %s gcc map failed\n", tag);
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
	printf("FUXI WIFI: %s gcc vote8 %#x vote0 %#x gdsc %#x halt %#x\n",
	    tag, vote8, vote0, gdsc, halt);
}

static void
fw_power_rails(void)
{
	volatile uint32_t *r;
	const uint8_t *db;
	struct fw_rsc_layout l;
	uint32_t found = 0;
	unsigned i;

	db = (const uint8_t *)ml_io_map(FW_CMDD_B, FW_CMDD_SZ);
	if (!db) {
		printf("FUXI WIFI: cmd-db map failed\n");
		return;
	}
	r = (volatile uint32_t *)ml_io_map(FW_RSC, 0x10000);
	if (!r) {
		printf("FUXI WIFI: apps RSC map failed\n");
		return;
	}
	l = fw_layout(r[0]);
	printf("FUXI WIFI: rsc drv id %#x tcs-cfg %#x stride %u cmd %u\n",
	    r[0], r[0x0cu / 4u], l.tcs_stride, l.cmd_stride);

	/* Keep the three active TCS done interrupts enabled and clear stale bits. */
	r[FW_TCS / 4] = 7u;
	r[(FW_TCS + l.irq_clear) / 4] = 7u;
	__asm__ volatile("dsb sy" ::: "memory");

	for (i = 0; i < sizeof fw_rails / sizeof fw_rails[0]; i++) {
		uint32_t addr = 0;

		if (fw_db_find(db, FW_CMDD_SZ, fw_rails[i].name, &addr)) {
			printf("FUXI WIFI: rail %s missing in cmd-db\n", fw_rails[i].name);
			continue;
		}
		found |= 1u << i;
		printf("FUXI WIFI: rail %s addr %#x -> %u mV\n", fw_rails[i].name,
		    addr, fw_rails[i].millivolts);
		fw_rpmh_send(r, &l, addr + 8, 7);                 /* mode: HPM */
		fw_rpmh_send(r, &l, addr, fw_rails[i].millivolts); /* voltage */
		fw_rpmh_send(r, &l, addr + 4, 1);                 /* enable */
		delay(20000);
	}
	printf("FUXI WIFI: rails found %#x of %#x\n", found,
	    (uint32_t)((1u << (sizeof fw_rails / sizeof fw_rails[0])) - 1u));
}

/* --- WiFi stage 3: WLAN_EN / PERST# / CLKREQ# pins and the two PCIe GDSCs. Both blocks are
 * clocked by the always-on TLMM and GCC top, so the writes are safe before the PCIe clocks run. */
static void
fw_tlmm_pin(volatile uint32_t *t, unsigned pin, uint32_t cfg, int level)
{
	if (level >= 0)
		t[(pin * 0x1000 + 4) / 4] = (uint32_t)(level ? 2u : 0u);
	t[pin * 0x1000 / 4] = cfg;
	if (level >= 0)
		t[(pin * 0x1000 + 4) / 4] = (uint32_t)(level ? 2u : 0u);
}

static uint32_t
fw_tlmm_word(volatile uint32_t *t)
{
	uint32_t w = 0;

	w |= t[(FW_WLAN_EN * 0x1000 + 4) / 4] & 3u;
	w |= (t[(FW_PERST * 0x1000 + 4) / 4] & 3u) << 8;
	w |= (t[(FW_CLKREQ * 0x1000 + 4) / 4] & 3u) << 16;

	return w;
}

static void
fw_wifi_power(void)
{
	vm_offset_t base;
	volatile uint32_t *g, *t;
	unsigned tries;
	uint32_t power;

	base = ml_io_map(FW_TLMM, FW_TLMM_SZ);
	if (base == 0) {
		printf("FUXI WIFI: tlmm map failed %#llx\n", (unsigned long long)FW_TLMM);
		return;
	}
	t = (volatile uint32_t *)base;
	printf("FUXI WIFI: stage 3 pin word %#x\n", fw_tlmm_word(t));

	/* PERST# is active low: hold the endpoint in reset while the chip is powered up. */
	printf("FUXI WIFI: perst %u low\n", FW_PERST);
	fw_tlmm_pin(t, FW_PERST, 1u | 1u << 9, 0);
	/* CLKREQ# belongs to the PCIe function: function 3, pull up, no output. */
	fw_tlmm_pin(t, FW_CLKREQ, 3u | 1u << 2, -1);
	/* Drop WLAN_EN first so the chip starts from nothing, then power it (pwrseq-qcom-wcn: 50 ms). */
	printf("FUXI WIFI: wlan_en %u low, 150 ms\n", FW_WLAN_EN);
	fw_tlmm_pin(t, FW_WLAN_EN, 1u | 3u << 6 | 1u << 9, 0);
	delay(150000);
	printf("FUXI WIFI: wlan_en %u high, 60 ms\n", FW_WLAN_EN);
	fw_tlmm_pin(t, FW_WLAN_EN, 1u | 3u << 6 | 1u << 9, 1);
	delay(60000);
	printf("FUXI WIFI: pin word after %#x perst cfg %#x/%#x wlan_en cfg %#x/%#x\n",
	    fw_tlmm_word(t),
	    t[FW_PERST * 0x1000 / 4], t[(FW_PERST * 0x1000 + 4) / 4],
	    t[FW_WLAN_EN * 0x1000 / 4], t[(FW_WLAN_EN * 0x1000 + 4) / 4]);

	base = ml_io_map(FW_GCC, FW_GCC_SZ);
	if (base == 0) {
		printf("FUXI WIFI: gcc map failed\n");
		return;
	}
	g = (volatile uint32_t *)base;
	/* A set bit in the collapse register keeps a GDSC collapsed: clear PCIe0 (bit 0) and PHY (bit 3). */
	printf("FUXI WIFI: gdsc collapse before %#x pcie0 %#x phy %#x\n",
	    g[0x52020 / 4], g[0x6b004 / 4], g[0x6c000 / 4]);
	g[0x52020 / 4] &= ~(1u << 0 | 1u << 3);
	for (tries = 0; tries < 100; tries++) {
		power = (g[0x6b004 / 4] >> 31) | ((g[0x6c000 / 4] >> 31) << 1);
		if (power == 3)
			break;
		delay(10000);
	}
	printf("FUXI WIFI: gdsc after %u tries pcie0 %#x phy %#x power %u\n",
	    tries, g[0x6b004 / 4], g[0x6c000 / 4],
	    (uint32_t)((g[0x6b004 / 4] >> 31) | ((g[0x6c000 / 4] >> 31) << 1)));
	printf("FUXI WIFI: stage 3 done\n");
}

struct fw_phyinit {
	uint16_t	offset;
	uint8_t		value;
};

static const struct fw_phyinit fw_serdes[] = {
	{0x00c0, 0x01}, {0x00cc, 0x62}, {0x00d0, 0x02}, {0x0060, 0xf8},
	{0x0064, 0x01}, {0x0000, 0x93}, {0x0004, 0x01}, {0x00e0, 0x90},
	{0x00e4, 0x82}, {0x00f4, 0x07}, {0x0070, 0x02}, {0x0010, 0x02},
	{0x0074, 0x16}, {0x0014, 0x16}, {0x0078, 0x36}, {0x0018, 0x36},
	{0x0110, 0x08}, {0x00bc, 0x0a}, {0x0120, 0x42}, {0x0080, 0x04},
	{0x0084, 0x0d}, {0x0020, 0x0a}, {0x0024, 0x1a}, {0x0088, 0x41},
	{0x0028, 0x34}, {0x0090, 0xab}, {0x0094, 0xaa}, {0x0098, 0x01},
	{0x0030, 0x55}, {0x0034, 0x55}, {0x0038, 0x01}, {0x0140, 0x14},
	{0x0164, 0x34}, {0x003c, 0x01}, {0x001c, 0x04}, {0x0174, 0x16},
	{0x01bc, 0x0f}, {0x0170, 0xa0}
};

static const struct fw_phyinit fw_tx[] = {
	{0x0e84, 0x15}, {0x0e90, 0x3f}, {0x0ee4, 0x02}, {0x0e40, 0x06},
	{0x0e3c, 0x18}
};

static const struct fw_phyinit fw_rx[] = {
	{0x11a4, 0x38}, {0x10dc, 0x11}, {0x1160, 0xbf}, {0x1164, 0xbf},
	{0x1168, 0xb7}, {0x116c, 0xea}, {0x115c, 0x3f}, {0x1174, 0x5c},
	{0x1178, 0x9c}, {0x117c, 0x1a}, {0x1180, 0x89}, {0x1170, 0xdc},
	{0x1188, 0x94}, {0x118c, 0x5b}, {0x1190, 0x1a}, {0x1194, 0x89},
	{0x10cc, 0x00}, {0x1008, 0x09}, {0x1014, 0x05}, {0x104c, 0x08},
	{0x1050, 0x08}, {0x10d8, 0x0f}, {0x1118, 0x1c}, {0x10f8, 0x07},
	{0x11f8, 0x08}
};

static const struct fw_phyinit fw_pcs[] = {
	{0x02dc, 0x05}, {0x0388, 0x77}, {0x0398, 0x0b}, {0x03e0, 0x0f},
	{0x03d0, 0x8c}
};

static const struct fw_phyinit fw_pcsmisc[] = {
	{0x06a4, 0x1e}, {0x06f4, 0x27}, {0x060c, 0x1d}, {0x0614, 0x07},
	{0x0620, 0xc1}, {0x0694, 0x00}
};

static void
fw_wait_clear(volatile uint32_t *reg, uint32_t mask, unsigned tries)
{
	unsigned i;

	for (i = 0; i < tries && (*reg & mask); i++)
		delay(100);
}

static void
fw_pulse_reset(volatile uint32_t *g, uint32_t bcr)
{
	g[bcr / 4] |= 1u;
	delay(1000);
	g[bcr / 4] &= ~1u;
	delay(1000);
}

/* WiFi stage 4: the PCIe0 clocks, the controller's PARF set-up, the QMP PHY tables, then PERST#
 * release and the LTSSM. Ends with a configuration read of the endpoint, which is still in reset
 * if the link never came up. */
static void
fw_pcie_up(void)
{
	vm_offset_t base;
	volatile uint32_t *g, *t, *f, *d, *p, *a, *c;
	uint32_t off, tries, power, ms;
	unsigned i;

	base = ml_io_map(FW_GCC, FW_GCC_SZ);
	if (base == 0) {
		printf("FUXI WIFI: stage 4 gcc map failed\n");
		return;
	}
	g = (volatile uint32_t *)base;
	printf("FUXI WIFI: stage 4 clocks\n");
	/* Sources first (an RCG takes its new configuration on CMD bit 0), the pipe mux parked on
	 * TCXO until the PHY runs, then the branch votes. */
	g[(FW_AUXRCG + 4) / 4] = 0;
	g[FW_AUXRCG / 4] |= 1u;
	g[(FW_RCHNGRCG + 4) / 4] = 6u << 8 | 5u;
	g[FW_RCHNGRCG / 4] |= 1u;
	fw_wait_clear(&g[FW_AUXRCG / 4], 1u, 100);
	fw_wait_clear(&g[FW_RCHNGRCG / 4], 1u, 100);
	g[FW_PIPEMUX / 4] = (g[FW_PIPEMUX / 4] & ~3u) | 2u;
	g[FW_VOTE8 / 4] |= FW_VOTE8B;
	g[FW_VOTE0 / 4] |= FW_VOTE0B;
	base = ml_io_map(FW_TCSR, 0x1000);
	if (base == 0) {
		printf("FUXI WIFI: stage 4 tcsr map failed\n");
		return;
	}
	((volatile uint32_t *)base)[0x100 / 4] |= 1u;
	printf("FUXI WIFI: stage 4 clk votes %#x %#x pipe mux %#x\n",
	    g[FW_VOTE8 / 4], g[FW_VOTE0 / 4], g[FW_PIPEMUX / 4]);
	delay(200);
	off = 0;
	for (i = 0; i < sizeof fw_branches / sizeof fw_branches[0]; i++)
		if (g[fw_branches[i] / 4] & (1u << 31))
			off |= 1u << i;
	printf("FUXI WIFI: stage 4 branches off %#x\n", off);
	fw_pulse_reset(g, FW_PCIEBRC);
	fw_pulse_reset(g, FW_PHYBRC);

	base = ml_io_map(FW_PARF, FW_PARF_SZ);
	if (base == 0) {
		printf("FUXI WIFI: stage 4 parf map failed\n");
		return;
	}
	f = (volatile uint32_t *)base;
	printf("FUXI WIFI: stage 4 parf\n");
	f[0x1000 / 4] = 4u;
	f[0x40 / 4] &= ~1u;
	f[0x350 / 4] = (uint32_t)FW_DBI;
	f[0x354 / 4] = (uint32_t)(FW_DBI >> 32);
	f[0x634 / 4] = (uint32_t)FW_ATU;
	f[0x638 / 4] = (uint32_t)(FW_ATU >> 32);
	f[0x358 / 4] = 0;
	f[0x35c / 4] = 0x80000000u;
	f[0x0 / 4] &= ~(1u << 29);
	f[0x174 / 4] |= 1u << 4;
	f[0x20 / 4] &= ~(1u << 5);
	f[0x1a8 / 4] |= 1u << 31;

	base = ml_io_map(FW_PHY, FW_PHY_SZ);
	if (base == 0) {
		printf("FUXI WIFI: stage 4 phy map failed\n");
		return;
	}
	p = (volatile uint32_t *)base;
	printf("FUXI WIFI: stage 4 phy tables\n");
	p[0x240 / 4] |= 3u;
	for (i = 0; i < sizeof fw_serdes / sizeof fw_serdes[0]; i++)
		p[fw_serdes[i].offset / 4] = fw_serdes[i].value;
	for (i = 0; i < sizeof fw_tx / sizeof fw_tx[0]; i++) {
		p[fw_tx[i].offset / 4] = fw_tx[i].value;
		p[(fw_tx[i].offset + 0x800) / 4] = fw_tx[i].value;
	}
	for (i = 0; i < sizeof fw_rx / sizeof fw_rx[0]; i++) {
		p[fw_rx[i].offset / 4] = fw_rx[i].value;
		p[(fw_rx[i].offset + 0x800) / 4] = fw_rx[i].value;
	}
	for (i = 0; i < sizeof fw_pcs / sizeof fw_pcs[0]; i++)
		p[fw_pcs[i].offset / 4] = fw_pcs[i].value;
	for (i = 0; i < sizeof fw_pcsmisc / sizeof fw_pcsmisc[0]; i++)
		p[fw_pcsmisc[i].offset / 4] = fw_pcsmisc[i].value;
	g[FW_PIPEMUX / 4] &= ~3u;
	p[0x200 / 4] &= ~1u;
	p[0x244 / 4] |= 3u;
	delay(1000);
	for (tries = 0; tries < 100 && (p[0x214 / 4] & (1u << 6)); tries++)
		delay(100);
	printf("FUXI WIFI: stage 4 phy status1 %#x tries %u\n", p[0x214 / 4], tries);

	/* The controller tells the SMMU which stream a requester's DMA belongs to from a hash table of
	 * requester IDs; the iommu-map sends requester 0x100 to stream 0x1401. */
	for (i = 0; i < 256; i++)
		f[(0x2000 + 4 * i) / 4] = 0;
	f[0x2c00 / 4] &= ~1u;
	{
		uint8_t crc = 0;
		uint16_t bdf = 0x100;
		uint8_t bytes[2];
		int bit;
		bytes[0] = (uint8_t)(bdf >> 8);
		bytes[1] = (uint8_t)bdf;
		for (i = 0; i < 2; i++) {
			uint8_t byte = bytes[i];
			crc ^= byte;
			for (bit = 0; bit < 8; bit++)
				crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 7) :
				    (uint8_t)(crc << 1);
		}
		f[(0x2000 + 4 * crc) / 4] = 0x100u << 16 | 1u << 8;
		printf("FUXI WIFI: stage 4 sid hash %#x entry %#x\n", crc,
		    f[(0x2000 + 4 * crc) / 4]);
	}

	base = ml_io_map(FW_DBI, FW_DBI_SZ);
	if (base == 0) {
		printf("FUXI WIFI: stage 4 dbi map failed\n");
		return;
	}
	d = (volatile uint32_t *)base;
	d[0x18 / 4] = 0x00010100;
	d[0x4 / 4] |= 0x6u;

	base = ml_io_map(FW_TLMM, FW_TLMM_SZ);
	if (base == 0) {
		printf("FUXI WIFI: stage 4 tlmm map failed\n");
		return;
	}
	t = (volatile uint32_t *)base;
	printf("FUXI WIFI: stage 4 release perst\n");
	delay(100000);
	fw_tlmm_pin(t, FW_PERST, 1u | 1u << 9, 1);
	delay(1000);
	f[FW_PARF_LTSSM / 4] |= 1u << 8;
	printf("FUXI WIFI: stage 4 ltssm enabled, polling\n");
	ms = 0;
	while (ms < 1000) {
		if ((d[0x72c / 4] & (1u << 4)) && (d[0x728 / 4] & 0x3fu) == 0x11)
			break;
		delay(1000);
		ms++;
	}
	printf("FUXI WIFI: stage 4 link %u ms debug0 %#x debug1 %#x\n",
	    ms, d[0x728 / 4], d[0x72c / 4]);
	printf("FUXI WIFI: stage 4 dbi id %#x linkctl %#x ltssm %#x\n",
	    d[0], d[0x80 / 4], f[FW_PARF_LTSSM / 4]);

	if (ms >= 1000) {
		printf("FUXI WIFI: stage 4 no link\n");
		return;
	}

	base = ml_io_map(FW_ATU, 0x1000);
	if (base == 0) {
		printf("FUXI WIFI: stage 4 atu map failed\n");
		return;
	}
	a = (volatile uint32_t *)base;
	a[0x08 / 4] = (uint32_t)FW_CONFIG;
	a[0x0c / 4] = (uint32_t)(FW_CONFIG >> 32);
	a[0x10 / 4] = (uint32_t)(FW_CONFIG + 0xfffff);
	a[0x20 / 4] = (uint32_t)((FW_CONFIG + 0xfffff) >> 32);
	a[0x14 / 4] = 1u << 24;
	a[0x18 / 4] = 0;
	a[0x0 / 4] = 0x4;
	a[0x04 / 4] = 1u << 31;
	for (i = 0; i < 5 && !(a[0x04 / 4] & (1u << 31)); i++)
		delay(10);
	base = ml_io_map(FW_CONFIG, 0x1000);
	if (base == 0) {
		printf("FUXI WIFI: stage 4 config map failed\n");
		return;
	}
	c = (volatile uint32_t *)base;
	printf("FUXI WIFI: stage 4 endpoint id %#x class %#x\n", c[0], c[0x8 / 4]);
	if (fw_stage >= 5) {
		fw_mhi_probe(a, c, d);
	} else {
		printf("FUXI WIFI: stage 4 done\n");
	}
}

/* Stage 5: forward the iATU's memory window to bus 1, give the endpoint's BAR0 an address inside it,
 * turn memory decoding and bus mastering on, and read the MHI registers the BAR starts with. Still
 * read-only: nothing is written to the MHI block or below it. */
static void
fw_mhi_regs(volatile uint32_t *m, uint32_t span)
{
	uint32_t bhi, bhie, level5 = 0;

	printf("FUXI MHI: reglen %#x imglow %#x imgsize %#x bhioff %#x bhieoff %#x\n",
	    m[0x00 / 4], m[0x08 / 4], m[0x10 / 4], m[0x28 / 4], m[0x2c / 4]);
	printf("FUXI MHI: ctrl %#x status %#x state %#x\n", m[0x38 / 4], m[0x48 / 4],
	    (m[0x48 / 4] >> 8) & 0xffu);
	bhi = m[0x28 / 4];
	bhie = m[0x2c / 4];
	if (bhi && bhi < span - 0x40) {
		printf("FUXI MHI: bhi execenv %#x status %#x errcode %#x intvec %#x\n",
		    m[(bhi + 0x28) / 4], m[(bhi + 0x2c) / 4], m[(bhi + 0x30) / 4],
		    m[(bhi + 0x20) / 4]);
		printf("FUXI MHI: bhi imgaddr %#x size %#x txdb %#x\n",
		    m[(bhi + 0x08) / 4], m[(bhi + 0x10) / 4], m[(bhi + 0x18) / 4]);
	}
	if (bhie && bhie < span - 0x48) {
		printf("FUXI MHI: bhie vecaddr %#x/%#x vecsize %#x vecdb %#x vecstatus %#x\n",
		    m[(bhie + 0x2c) / 4], m[(bhie + 0x30) / 4], m[(bhie + 0x34) / 4],
		    m[(bhie + 0x3c) / 4], m[(bhie + 0x44) / 4]);
	}
	if (PE_parse_boot_argn("fuximhi", &level5, sizeof level5))
		printf("FUXI MHI: level %u\n", level5);
}

static void
fw_mhi_probe(volatile uint32_t *patu, volatile uint32_t *pcfg, volatile uint32_t *pdbi)
{
	volatile uint32_t *r1, *m;
	vm_offset_t base;
	uint32_t mask, size, address, command;

	printf("FUXI MHI: stage 5 starting\n");
	/* region 1: CPU window to the same PCI addresses the devices see */
	r1 = patu + 0x200 / 4;
	r1[0x08 / 4] = FW_MEMBASE;
	r1[0x0c / 4] = 0;
	r1[0x10 / 4] = FW_MEMBASE + FW_MEMSIZE - 1;
	r1[0x20 / 4] = 0;
	r1[0x14 / 4] = FW_MEMBASE;
	r1[0x18 / 4] = 0;
	r1[0x00 / 4] = 0x0;
	r1[0x04 / 4] = 1u << 31;
	for (size = 0; size < 5 && !(r1[0x04 / 4] & (1u << 31)); size++)
		delay(10);
	/* type 1 header memory base/limit so the root port forwards bus 1's memory */
	pdbi[0x20 / 4] = ((FW_MEMBASE + FW_MEMSIZE - 1) & 0xfff00000u) |
	    ((FW_MEMBASE >> 16) & 0xfff0u);
	/* BAR0: size it while it decodes nothing, then put it at the start of the window */
	mask = pcfg[0x10 / 4];
	pcfg[0x10 / 4] = 0xffffffffu;
	mask = pcfg[0x10 / 4];
	pcfg[0x10 / 4] = 0;
	if ((mask & ~0xfu) == 0) {
		printf("FUXI MHI: bar0 not implemented (%#x)\n", mask);
		return;
	}
	size = ~(mask & ~0xfu) + 1;
	if (size > FW_MHI_SZ)
		size = FW_MHI_SZ;
	address = (FW_MEMBASE + size - 1) & ~(size - 1);
	pcfg[0x10 / 4] = address | (mask & 0xfu);
	if ((mask & 6u) == 4u)
		pcfg[0x14 / 4] = 0;
	command = (pcfg[0x04 / 4] & 0xffff0000u) | 0x6u;
	pcfg[0x04 / 4] = command;
	printf("FUXI MHI: bar0 mask %#x size %#x address %#x command %#x\n", mask, size,
	    address, command);
	base = ml_io_map(address, size);
	if (base == 0) {
		printf("FUXI MHI: bar map failed\n");
		return;
	}
	m = (volatile uint32_t *)base;
	fw_mhi_regs(m, size);
	printf("FUXI MHI: stage 5 done\n");
}

static void
fk_wifi_thread(void *arg, wait_result_t wr)
{
	unsigned stage = (unsigned)(uintptr_t)arg;

	(void)wr;
	thread_set_thread_name(current_thread(), "fuxi-wifi");
	printf("FUXI WIFI: stage %u starting\n", stage);

	fw_read_gcc("before");
	if (stage >= 2) {
		printf("FUXI WIFI: voting WCN7850 rails\n");
		fw_power_rails();
		fw_read_gcc("after");
	}
	if (stage >= 3) {
		printf("FUXI WIFI: powering pins and GDSCs\n");
		fw_wifi_power();
	}
	if (stage >= 4) {
		fw_stage = stage;
		printf("FUXI WIFI: stage 4 starting\n");
		fw_pcie_up();
	}
	printf("FUXI WIFI: probe done\n");
}

void
fuxi_wifi_probe_init(void)
{
	unsigned stage = 0;
	thread_t thread = THREAD_NULL;

	if (!PE_parse_boot_argn("fuxiwifiprobe", &stage, sizeof stage) || stage == 0) {
		return;
	}
	if (kernel_thread_start(fk_wifi_thread, (void *)(uintptr_t)stage, &thread) ==
	    KERN_SUCCESS) {
		thread_deallocate(thread);
	} else {
		printf("FUXI WIFI: thread start failed\n");
	}
}
#endif /* FUXI */
