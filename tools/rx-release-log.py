#!/usr/bin/env python3
"""r627: watch the two rings the hardware uses to hand frames back or drop them.

r626 shows the REO block is enabled (gen 0x807a1f0d, correct hash map) and all
eight destination rings hold their programming, yet not one descriptor appears
and nothing lands on the REO exception ring either.  So the frames are being
consumed somewhere upstream of the rings we can see.

Two rings sit on that path: the REO exception ring, which the hardware fills
when it cannot route a frame, and the WBM release ring, which reports frames
that were returned or released with an error.  Read both tails on every visit:
if either moves, the receive path is alive and the question becomes why the
destination rings stay empty; if neither moves, the hardware never handed a
received frame back at all.
"""

from pathlib import Path
import sys

MARK = "NWDIAG wbm err"
MARK2 = "NWDIAG reo exc"

EDITS = [
    (
        "wifi7/dp_rx.c",
        "\tsrng = &hal->srng_list[dp->rx_rel_ring.ring_id];\n",
        (
            "\t/* r627: how many frames does the release ring hand back? */\n"
            "\t{\n"
            "\t\tstatic u32 dbg_wb;\n"
            "\n"
            "\t\tdbg_wb++;\n"
            "\t\tif (dbg_wb <= 8 || !(dbg_wb & 255))\n"
            "\t\t\tath12k_info(ab, \"" + MARK + " n %u ring %u tp %u chp %u\\n\",\n"
            "\t\t\t\t    dbg_wb, dp->rx_rel_ring.ring_id,\n"
            "\t\t\t\t    srng->u.dst_ring.tp, srng->u.dst_ring.cached_hp);\n"
            "\t}\n"
        ),
    ),
    (
        "wifi7/dp_rx.c",
        "\tsrng = &hal->srng_list[reo_except->ring_id];\n",
        (
            "\t/* r627: does the exception ring ever receive a frame? */\n"
            "\t{\n"
            "\t\tstatic u32 dbg_re;\n"
            "\n"
            "\t\tdbg_re++;\n"
            "\t\tif (dbg_re <= 8 || !(dbg_re & 255))\n"
            "\t\t\tath12k_info(ab, \"" + MARK2 + " n %u ring %u tp %u chp %u\\n\",\n"
            "\t\t\t\t    dbg_re, reo_except->ring_id,\n"
            "\t\t\t\t    srng->u.dst_ring.tp, srng->u.dst_ring.cached_hp);\n"
            "\t}\n"
        ),
    ),
]


def ath12k_dir(dest: Path) -> Path:
    for cand in (
        dest,
        dest / "ath12k",
        dest / "deps/linux/drivers/net/wireless/ath/ath12k",
        dest / "drivers/net/wireless/ath/ath12k",
    ):
        if (cand / "wifi7/dp_rx.c").is_file():
            return cand
    raise SystemExit(f"rx-release-log: no ath12k tree under {dest}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: rx-release-log.py <tree|ath12k>")

    root = ath12k_dir(Path(sys.argv[1]))
    # both probes live in the same file, so validate against one snapshot and
    # then apply the replacements cumulatively - writing per edit would drop
    # the earlier one.
    texts = {}
    for rel, anchor, block in EDITS:
        path = root / rel
        if rel not in texts:
            texts[rel] = path.read_bytes().decode()
        src = texts[rel]
        if MARK in src and MARK2 in src:
            print("rx-release-log: already patched")
            return 0
        count = src.count(anchor)
        if count != 1:
            raise SystemExit(f"rx-release-log: anchor count={count} in {rel}, refusing")
        if block.rstrip("\n") in src:
            continue
        texts[rel] = src.replace(anchor, anchor + block, 1)

    for rel, text in texts.items():
        (root / rel).write_bytes(text.encode())

    print(f"rx-release-log: patched {root}/wifi7/dp_rx.c (REO exception and WBM release rings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
