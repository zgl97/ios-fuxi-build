/*
 * FUXI WiFi stage 1: a read-only look at what the boot loaders left of the
 * WCN7850 path on the fuxi board.
 * Hardware facts, from the live DT (build/live.dtb, research/device/probe-09):
 *   GCC 0x100000, PCIe0 PARF 0x1c00000, PHY 0x1c06000, DBI 0x60000000,
 *   TLMM pinctrl@f000000 (0x1000000).  WLAN_EN GPIO 80, SW_CTRL GPIO 82,
 *   PERST# GPIO 94 (active low), WAKE# GPIO 96; the cnss node carries the six
 *   WCN7850 rails (1.8 V io, 0.966 V dig, 1.012 V aon, 1.9 V rfa1, 1.35 V rfa2).
 * The register map and the stage-1 flow come from UniXNU-sheng's
 * package/WifiProbe.cpp (BSD-2-Clause-Patent): votes in 0x52008/0x52000, the
 * PCIe0 GDSC at 0x6b004 (bit 31 PWR_ON), the branch halt registers kBranches.
 * The controller registers are only read when the GDSC is on and the four
 * branches (slv_axi, mstr_axi, cfg_ahb, aux) run: a read of an unclocked block
 * stalls the bus, so everything else is skipped.
 * Nothing here writes a register.
 */
#if defined(FUXI)
#include <sys/param.h>
#include <sys/systm.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);

#define FW_GCC		0x00100000ULL
#define FW_TLMM_F	0x0f000000ULL
#define FW_TLMM_F1	0x0f100000ULL
#define FW_PARF		0x01c00000ULL
#define FW_DBI		0x60000000ULL

#define FW_VOTE8	0x52008u
#define FW_VOTE0	0x52000u
#define FW_GDSC		0x6b004u
#define FW_PARF_LTSSM	0x1b0u
#define FW_PARF_PHYCTL	0x40u
#define FW_LINK_CONTROL	0x80u

static const uint32_t fw_branches[] = {
	0x6b020, 0x6b02c, 0x6b038, 0x6b03c, 0x6b048,
	0x6b01c, 0x10030, 0x1003c, 0x1004c, 0x6b054,
};
static const unsigned fw_pins[] = { 80, 82, 94 };

static uint32_t
fw_pinword(volatile uint32_t *tlmm)
{
	uint32_t word = 0;
	unsigned i;

	for (i = 0; i < sizeof fw_pins / sizeof fw_pins[0]; i++) {
		word |= (tlmm[(fw_pins[i] * 0x1000u + 4u) / 4u] & 3u) << (8u * i);
	}
	return word;
}

static void
fuxi_wifi_thread(void *arg, wait_result_t wr)
{
	volatile uint32_t *gcc, *tlmm, *tlmm1, *parf, *dbi;
	uint32_t vote8, vote0, gdsc, halt = 0, needed;
	unsigned i;

	(void)arg;
	(void)wr;
	thread_set_thread_name(current_thread(), "fuxi-wifi");

	gcc = (volatile uint32_t *)ml_io_map(FW_GCC, 0x80000);
	tlmm = (volatile uint32_t *)ml_io_map(FW_TLMM_F, 0x100000);
	tlmm1 = (volatile uint32_t *)ml_io_map(FW_TLMM_F1, 0x100000);
	if (!gcc || !tlmm) {
		printf("FUXI WIFI: map failed gcc %p tlmm %p\n", gcc, tlmm);
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
	needed = (halt & 0xfu);

	printf("FUXI WIFI: gcc vote8 %#x vote0 %#x gdsc %#x halt %#x\n",
	    vote8, vote0, gdsc, halt);
	printf("FUXI WIFI: tlmm@f000000 %#x tlmm@f100000 %#x\n",
	    fw_pinword(tlmm), tlmm1 ? fw_pinword(tlmm1) : 0);

	if ((gdsc & (1u << 31)) && needed == 0) {
		parf = (volatile uint32_t *)ml_io_map(FW_PARF, 0x3000);
		dbi = (volatile uint32_t *)ml_io_map(FW_DBI, 0x1000);
		if (parf && dbi) {
			printf("FUXI WIFI: parf ltssm %#x phyctl %#x\n",
			    parf[FW_PARF_LTSSM / 4u], parf[FW_PARF_PHYCTL / 4u]);
			printf("FUXI WIFI: ep vendor %#x device %#x link %#x\n",
			    dbi[0], dbi[1], dbi[FW_LINK_CONTROL / 4u]);
		} else {
			printf("FUXI WIFI: parf/dbi map failed %p %p\n", parf, dbi);
		}
	} else {
		printf("FUXI WIFI: controller read skipped (gdsc on %u, clocks off %#x)\n",
		    (gdsc >> 31) & 1u, needed);
	}
	printf("FUXI WIFI: stage 1 done\n");
}

void
fuxi_wifi_probe_init(void)
{
	unsigned delay = 0;
	thread_t thread = THREAD_NULL;

	if (!PE_parse_boot_argn("fuxiwifiprobe", &delay, sizeof delay) || delay == 0) {
		return;
	}
	if (kernel_thread_start(fuxi_wifi_thread, NULL, &thread) == KERN_SUCCESS) {
		thread_deallocate(thread);
	} else {
		printf("FUXI WIFI: thread start failed\n");
	}
}
#endif /* FUXI */
