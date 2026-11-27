#!/usr/bin/env python3
"""r603: clean the cache for DMA-to-device buffers, as dma_map_single does.

Every probe says the host side is healthy: the frame is 4-byte aligned, its
header is a correct authentication request, the vdev is created and started with
fw_started set, the WMI command is accepted (`wmi mgmt send ret 0`), and the
firmware answers the stats request we send right afterwards.  It simply never
transmits the frame and never reports completion.

One difference from the Linux path remains: on arm64 `dma_map_single(...,
DMA_TO_DEVICE)` cleans the CPU cache for the buffer before handing the address
to the device.  This shim only computes an IOVA - the board's dma_map matches a
slot and returns its address.  A frame the CPU just wrote can therefore sit
dirty in cache while the firmware reads stale or zero memory through its own
aperture, and a frame with no valid header is dropped without an error, which is
exactly the silence we see.  It also explains why the WMI command ring works:
that path writes the ring and rings a doorbell through the board code, which
does its own maintenance.

Clean (clean + invalidate to point of coherency) the range for every direction
except DMA_FROM_DEVICE, where the device is the writer.
"""
import sys
from pathlib import Path

ANCHOR = (
    "dma_addr_t lx_dma_map(struct device *d, void *p, size_t n, int dir) "
    "{ (void)d; return lx_plat->dma_map(p, n, dir); }\n"
)
REPLACEMENT = (
    "/* r603: arm64 dma_map_single() cleans the cache for DMA_TO_DEVICE; a shim\n"
    " * that only computes an IOVA leaves freshly written frames dirty in cache,\n"
    " * so the firmware reads stale memory and drops the frame without an error. */\n"
    "static void lx_dcache_clean(void *va, size_t n) {\n"
    "  unsigned long a = (unsigned long)va & ~63UL;\n"
    "  unsigned long e = ((unsigned long)va + n + 63UL) & ~63UL;\n"
    "  unsigned long c;\n"
    "  for (c = a; c < e; c += 64UL)\n"
    "    __asm__ volatile(\"dc civac, %0\" :: \"r\"(c) : \"memory\");\n"
    "  __asm__ volatile(\"dsb sy\" ::: \"memory\");\n"
    "}\n"
    "dma_addr_t lx_dma_map(struct device *d, void *p, size_t n, int dir) {\n"
    "  (void)d;\n"
    "  if (p && n && dir != 2)\n"
    "    lx_dcache_clean(p, n);\n"
    "  return lx_plat->dma_map(p, n, dir);\n"
    "}\n"
)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: dma-cache-clean.py <ath12kwifi-root>")
    root = Path(sys.argv[1])
    rt = root / "shim/src/lx_rt.c"
    src = rt.read_text()
    if "lx_dcache_clean" in src:
        print("dma-cache-clean: shim/src/lx_rt.c already patched")
        return
    if src.count(ANCHOR) != 1:
        raise SystemExit(f"dma-cache-clean: anchor count {src.count(ANCHOR)} (expected 1)")
    rt.write_text(src.replace(ANCHOR, REPLACEMENT, 1))
    print("dma-cache-clean: patched shim/src/lx_rt.c dma_map cache clean")


if __name__ == "__main__":
    main()
