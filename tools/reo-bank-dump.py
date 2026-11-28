#!/usr/bin/env python3
"""r624: dump the REO ring-bank registers after init, and stop reading them early.

r620 and r623 both read the REO ring registers *before* ath12k_wmi_cmd_init()
and both boots came up without en0; r623 also showed those reads return
0xffffffff.  Every round that only read after init booted healthy.  So drop the
pre-init probe and, instead of guessing which register holds which ring, dump
the whole ring-bank window once and read the layout off the words themselves.
"""

import sys
from pathlib import Path

MARK = "NWDIAG reo bank"

DUMP_ANCHOR = "\tnwdiag_reo_mem_dump(ab);\n"

DUMP_LINES = [
    "\t{",
    "\t\tu32 dbg_off;",
    "\t\tint dbg_k;",
    "",
    "\t\tfor (dbg_off = 0x4e0; dbg_off < 0x7e0; dbg_off += 32) {",
    "\t\t\tath12k_info(ab,",
    "\t\t\t\t    \"NWDIAG reo bank %03x %x %x %x %x %x %x %x %x\\n\", dbg_off,",
    "\t\t\t\t    ath12k_hif_read32(ab, 0x00a38000u + dbg_off),",
    "\t\t\t\t    ath12k_hif_read32(ab, 0x00a38000u + dbg_off + 4),",
    "\t\t\t\t    ath12k_hif_read32(ab, 0x00a38000u + dbg_off + 8),",
    "\t\t\t\t    ath12k_hif_read32(ab, 0x00a38000u + dbg_off + 12),",
    "\t\t\t\t    ath12k_hif_read32(ab, 0x00a38000u + dbg_off + 16),",
    "\t\t\t\t    ath12k_hif_read32(ab, 0x00a38000u + dbg_off + 20),",
    "\t\t\t\t    ath12k_hif_read32(ab, 0x00a38000u + dbg_off + 24),",
    "\t\t\t\t    ath12k_hif_read32(ab, 0x00a38000u + dbg_off + 28));",
    "\t\t\tdbg_k++;",
    "\t\t}",
    "\t}",
]


def block():
    return "\n".join(DUMP_LINES) + "\n"


def ath12k_dir(dest):
    dest = Path(dest)
    for cand in (
        dest,
        dest / "ath12k",
        dest / "deps/linux/drivers/net/wireless/ath/ath12k",
        dest / "drivers/net/wireless/ath/ath12k",
    ):
        if (cand / "dp_rx.c").is_file():
            return cand
    raise SystemExit(f"reo-bank-dump: no ath12k source under {dest}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: reo-bank-dump.py <tree>")
    dp_rx = ath12k_dir(sys.argv[1]) / "dp_rx.c"
    src = dp_rx.read_text()

    if MARK in src:
        print(f"reo-bank-dump: already applied to {dp_rx}")
        return

    count = src.count(DUMP_ANCHOR)
    if count != 1:
        raise SystemExit(f"reo-bank-dump: anchor count={count}, refusing to patch")

    src = src.replace(DUMP_ANCHOR, DUMP_ANCHOR + block())
    dp_rx.write_text(src)
    print(f"reo-bank-dump: patched {dp_rx} (REO ring-bank dump)")


if __name__ == "__main__":
    main()
