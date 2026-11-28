#!/usr/bin/env python3
"""r626: bring the REO block up again on top of the freshly armed rings.

r625 shows srng_dst_hw_init() sticks when it runs after the firmware is up:
all eight REO destination rings read their base back.  The chip still writes
no descriptor, so the next question is whether the REO block itself is not
enabled any more at that point - it was enabled before the firmware init,
the same window that later reads as zero.

So re-run reo_hw_setup() right after the rings are armed, with the same hash
map the common setup used, and read the enable word straight back.
"""

from pathlib import Path
import sys

MARK = "NWDIAG reo enable"
ANCHOR = "\tnwdiag_reo_mem_dump(ab);\n"

BLOCK = (
    "\t/* r626: rings armed, so bring the REO block up again on top of them */\n"
    "\tab->hal.ops->reo_hw_setup(ab, 0x43214321u);\n"
    "\tath12k_info(ab, \"" + MARK + " gen 0x%x ix2 0x%x\\n\",\n"
    "\t\t    ath12k_hif_read32(ab, 0x00a38000u + 0x0),\n"
    "\t\t    ath12k_hif_read32(ab, 0x00a38000u + 0xc));\n"
)

HIF_INCLUDE = '#include "hif.h"\n'
INCLUDE_ANCHOR = '#include "dp_rx.h"\n'


def ath12k_dir(dest: Path) -> Path:
    for cand in (
        dest,
        dest / "ath12k",
        dest / "deps/linux/drivers/net/wireless/ath/ath12k",
        dest / "drivers/net/wireless/ath/ath12k",
    ):
        if (cand / "dp_rx.c").is_file():
            return cand
    raise SystemExit(f"reo-reenable: no ath12k tree under {dest}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: reo-reenable.py <tree|ath12k>")

    root = ath12k_dir(Path(sys.argv[1]))
    path = root / "dp_rx.c"
    src = path.read_bytes().decode()

    if MARK in src:
        print("reo-reenable: already patched")
        return 0

    count = src.count(ANCHOR)
    if count != 1:
        raise SystemExit(f"reo-reenable: dump anchor count={count}, refusing")

    if HIF_INCLUDE not in src:
        if src.count(INCLUDE_ANCHOR) != 1:
            raise SystemExit("reo-reenable: include anchor not unique")
        src = src.replace(INCLUDE_ANCHOR, INCLUDE_ANCHOR + HIF_INCLUDE)

    src = src.replace(ANCHOR, BLOCK + ANCHOR)
    path.write_bytes(src.encode())
    print(f"reo-reenable: patched {path} (REO re-enabled after the rings were armed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
