#!/usr/bin/env python3
"""r622: record which destination rings got a hardware init, and read the ring
register block back at a point where the firmware is definitely up.

Two signals, one round:

  1. A bitmask set inside ath12k_wifi7_hal_srng_dst_hw_init(). Bits survive the
     lossy console, so this answers "was the base register ever written" even
     when the per-ring print gets swallowed.

  2. A late dump: for every ring that has a register base, read the block back,
     plus the REO block's own GEN_ENABLE / MISC_CTRL / DEST_RING_CTRL_IX_2.
     If the TCL rings read back their bases while the REO rings read zero, the
     write path is fine and the REO block is not; if everything reads zero the
     read side of the aperture is the problem.
"""

import os

MARK = "NWDIAG dsthw"
MARK_MAP = "NWDIAG srng map"

GLOBALS = (
    "\n"
    "/* r622 diagnostics: survive the lossy console via a bitmask. */\n"
    "u32 nwdiag_dsthw_mask;\n"
    "u32 nwdiag_dsthw_n;\n"
)

GLOBALS_ANCHOR = '#include "hal_qcc2072.h"\n'

DST_ANCHOR = (
    "void ath12k_wifi7_hal_srng_dst_hw_init(struct ath12k_base *ab,\n"
    "\t\t\t\t       struct hal_srng *srng)\n"
    "{\n"
    "\tstruct ath12k_hal *hal = &ab->hal;\n"
    "\tu32 val;\n"
    "\tu64 hp_addr;\n"
    "\tu32 reg_base;\n"
    "\n"
    "\treg_base = srng->hwreg_base[HAL_SRNG_REG_GRP_R0];\n"
)

DST_BLOCK = (
    "\n"
    "\tnwdiag_dsthw_n++;\n"
    "\tif (srng->ring_id < 32)\n"
    "\t\tnwdiag_dsthw_mask |= BIT(srng->ring_id);\n"
)

DUMP_ANCHOR = "\tnwdiag_reo_mem_dump(ab);\n"

DUMP_BLOCK = (
    "\n"
    "\t{\n"
    "\t\textern u32 nwdiag_dsthw_mask;\n"
    "\t\textern u32 nwdiag_dsthw_n;\n"
    "\t\tint dbg_i;\n"
    "\t\tint dbg_lim = 0;\n"
    "\n"
    "\t\tath12k_info(ab, \"" + MARK + " n %u mask 0x%x\\n\",\n"
    "\t\t\t    nwdiag_dsthw_n, nwdiag_dsthw_mask);\n"
    "\t\t/* Offsets are spelled out on purpose: HAL_REO1_GEN_ENABLE and\n"
    "\t\t * HAL_REO1_DEST_RING_CTRL_IX_2 live in wifi7/hal.h, which this\n"
    "\t\t * translation unit does not include. Values: wifi7/hal.h:120\n"
    "\t\t * (0x0) and wifi7/hal.h:125 (0xc).\n"
    "\t\t */\n"
    "\t\tath12k_info(ab, \"" + MARK + " reo gen 0x%x ix2 0x%x\\n\",\n"
    "\t\t\t    ath12k_hif_read32(ab, HAL_SEQ_WCSS_UMAC_REO_REG + 0x0),\n"
    "\t\t\t    ath12k_hif_read32(ab, HAL_SEQ_WCSS_UMAC_REO_REG + 0xc));\n"
    "\n"
    "\t\tfor (dbg_i = 0; dbg_i < HAL_SRNG_RING_ID_MAX && dbg_lim < 24; dbg_i++) {\n"
    "\t\t\tstruct hal_srng *dbg_sm = &ab->hal.srng_list[dbg_i];\n"
    "\n"
    "\t\t\tif (!dbg_sm->ring_base_vaddr ||\n"
    "\t\t\t    !dbg_sm->hwreg_base[HAL_SRNG_REG_GRP_R0])\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\tdbg_lim++;\n"
    "\t\t\tath12k_info(ab, \"" + MARK_MAP + " id %d hw 0x%x val 0x%x\\n\",\n"
    "\t\t\t\t    dbg_i,\n"
    "\t\t\t\t    dbg_sm->hwreg_base[HAL_SRNG_REG_GRP_R0],\n"
    "\t\t\t\t    ath12k_hif_read32(ab,\n"
    "\t\t\t\t\t\t      dbg_sm->hwreg_base[HAL_SRNG_REG_GRP_R0]));\n"
    "\t\t}\n"
    "\t}\n"
)


def ath12k_dir(dest):
    """Accept the repo root, the linux root, or the ath12k dir itself."""
    root = os.path.normpath(dest)
    candidates = [
        root,
        os.path.join(root, "ath12k"),
        os.path.join(root, "deps", "linux", "drivers", "net", "wireless",
                     "ath", "ath12k"),
        os.path.join(root, "drivers", "net", "wireless", "ath", "ath12k"),
    ]
    for cand in candidates:
        if os.path.isfile(os.path.join(cand, "dp_rx.c")):
            return cand
    return root


def read(path):
    with open(path, "rb") as fh:
        return fh.read().decode()


def write(path, text):
    with open(path, "wb") as fh:
        fh.write(text.encode())


def apply_edits(plan):
    for path, anchor, _block, _name in plan:
        text = read(path)
        if text.count(anchor) != 1:
            raise SystemExit("%s: anchor count=%d, refusing"
                             % (os.path.basename(path), text.count(anchor)))
    for path, anchor, block, name in plan:
        text = read(path)
        if block in text:
            print("skip %s (already patched)" % name)
            continue
        text = text.replace(anchor, anchor + block, 1)
        write(path, text)
        print("ok   %s" % name)


def main(dest):
    root = ath12k_dir(dest)
    hal_c = os.path.join(root, "wifi7", "hal.c")
    dp_rx = os.path.join(root, "dp_rx.c")
    for path in (hal_c, dp_rx):
        if not os.path.isfile(path):
            raise SystemExit("missing %s" % path)

    apply_edits([
        (hal_c, GLOBALS_ANCHOR, GLOBALS, "dsthw counters"),
        (hal_c, DST_ANCHOR, DST_BLOCK, "dst hw init bitmask"),
        (dp_rx, DUMP_ANCHOR, DUMP_BLOCK, "late srng map dump"),
    ])


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
