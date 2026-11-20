#!/usr/bin/env python3
"""Generate the XNU-side plumbing that builds the Ath12kWifi Linux stack in.

Three files come out of one manifest, so the staged tree, the kernel config and
the per-object flags cannot drift apart:

  xnu/fuxi-ath12k/manifest.txt       label, staged path, flag group
  xnu/patches/9999-stage-ath12k.sh   copy the staged sources into bsd/
  xnu/patches/0117-fuxi-ath12k.patch declare the option and the 125 objects

Why the split: dxb runs every patches/*.sh first and applies every patches/*.patch
afterwards (see the "Running script" lines that precede the first "Applying
patch" in any kernel build log). A staging script therefore cannot edit a file
that an earlier patch creates, and a patch cannot carry sources that a later
script would have to stage. So the script stages sources and the patch edits the
three files that decide what gets compiled.

Why the patch can be written blind: it is numbered 0117, so 0019, 0026, 0099 and
0112 have already landed when it applies. Its context lines are therefore text
those patches produced, and every one of them is extracted verbatim from this
repo's own patches at generation time instead of being retyped from memory.
`--check` re-derives them, so a later patch that moves an anchor fails this
generator rather than the kernel build.

bsd/conf/files derives an object name from the source's basename, and XNU already
builds init.o, pm.o, core.o, scan.o, util.o, pci.o, chan.o, main.o and many more.
Every staged source therefore gets an fxa_ prefix, so the stack can never shadow
a kernel object; 18 of the 125 sources also share a basename with another one
(ce.c exists in ath12k/ and in ath12k/wifi7/, core.c in three trees, and so on),
so those carry their parent directory or tree name as well. Left alone, config
would emit one rule per basename and 19 objects would silently overwrite each
other, which is exactly the bug the standalone build hit. Nothing includes a .c
file, so renaming one
changes no include.

Usage: python3 tools/ci/mk-ath12k-kernel.py [--check]
"""
import argparse
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT.parent.parent
ATH12K = DOCS / "src/Ath12kWifi"
PATCHES = ROOT / "xnu/patches"
MANIFEST = ROOT / "xnu/fuxi-ath12k/manifest.txt"
STAGE = PATCHES / "9999-stage-ath12k.sh"
PATCH = PATCHES / "0117-fuxi-ath12k.patch"

# Where each sources.txt label lands inside bsd/fuxi_ath12k.
PREFIX = {
    "ath12k": "out/ath12k-src",
    "mac80211": "out/mac80211",
    "cfg80211": "out/cfg80211",
    "mhi": "out/mhi-src",
    "qmi": "deps/linux/drivers/soc/qcom",
    "crypto": "out/crypto",
    "generated": "out",
}

# The standalone build keeps four flag sets apart; the reason each one exists is
# recorded next to it in tools/fuxi-ath12k-build.sh. Reproduced per object
# because bsd/conf/Makefile.template only has per-object hooks.
INCLUDES = {
    "MODULE": [
        "shim/include", "include", "out/pinned", "shim/crypto", "out", "src",
        "out/mac80211", "out/mhi-src", "out/crypto", "out/cfg80211",
        "out/ath12k-src", "out/ath12k-src/wifi7",
    ],
    "ATH12K": [
        "shim/include", "include", "out/pinned", "shim/crypto", "out", "src",
        "out/mac80211", "out/mhi-src", "out/crypto", "out/ath12k-src",
        "out/ath12k-src/wifi7", "out/cfg80211",
    ],
    # nw_debug.c is the one module unit that means ath12k's own core.h/debug.h.
    "ATH12K_FIRST": [
        "out/ath12k-src", "out/ath12k-src/wifi7", "shim/include", "include",
        "out/pinned", "shim/crypto", "out", "src", "out/mac80211",
        "out/mhi-src", "out/crypto", "out/cfg80211",
    ],
    "CRYPTO": [
        "shim/include", "include", "out/pinned", "shim/crypto", "out", "src",
        "out/mac80211", "out/mhi-src", "out/crypto", "out/cfg80211",
        "out/ath12k-src", "out/ath12k-src/wifi7",
    ],
}

DEFINES = [
    "-DCONFIG_ATH12K_DEBUG",
    "-DCONFIG_MAC80211_STA_HASH_MAX_SIZE=0",
    "-DCONFIG_CFG80211=1",
    "-DCONFIG_MAC80211=1",
    '-DCONFIG_MAC80211_RC_DEFAULT=""',
]
# sock_release and the AES entry points are exported by the kernel under the
# same names the stack uses; rename the stack's side so the kernel keeps its own.
BASE_DEFINES = DEFINES + ["-Dsock_release=nw_sock_release"]
CRYPTO_DEFINES = BASE_DEFINES + [
    "-Daes_siv_encrypt=nw_aes_siv_encrypt",
    "-Daes_siv_decrypt=nw_aes_siv_decrypt",
    "-Daes_encrypt=nw_aes_encrypt",
    "-Daes_encrypt_init=nw_aes_encrypt_init",
    "-Daes_encrypt_deinit=nw_aes_encrypt_deinit",
]


def hunk_lines(patch, target):
    """Every line of a patch's hunks for one file, as (kind, text).

    kind is ' ', '+' or '-' exactly as the patch records it, so the whitespace
    a generator re-quotes is the whitespace that is really on disk.
    """
    out, current, in_hunk = [], None, False
    text = (PATCHES / patch).read_text(encoding="utf-8", errors="surrogateescape")
    for line in text.splitlines():
        if line.startswith("+++ b/"):
            current, in_hunk = line[6:], False
            continue
        if line.startswith("--- a/") or line.startswith("diff --git"):
            current, in_hunk = None, False
            continue
        if line.startswith("@@"):
            in_hunk = current == target
            continue
        if in_hunk and line[:1] in (" ", "+", "-"):
            out.append((line[0], line[1:]))
    if not out:
        raise SystemExit(f"{patch} has no hunk for {target}")
    return out


def last_added(lines):
    return max(i for i, (kind, _) in enumerate(lines) if kind == "+")


def context_around(lines, index):
    """The context lines immediately before and after a given line."""
    before = [text for kind, text in lines[:index] if kind == " "]
    after = [text for kind, text in lines[index + 1:] if kind == " "]
    return (before[-1] if before else None), (after[0] if after else None)


def anchor(patch, target):
    """(last line the earlier patch added, first context line after it).

    A later patch's context has to match the file as it stands when that patch
    runs, and by then the earlier patch's addition is already in place. Quoting
    only the untouched lines on either side would straddle the addition and the
    hunk would never match.
    """
    lines = hunk_lines(patch, target)
    index = last_added(lines)
    after = [text for kind, text in lines[index + 1:] if kind == " "]
    return lines[index][1], (after[0] if after else None)


def anchors():
    """Anchor pairs for 0117, each taken from the patch that produced the text.

    config/MASTER.arm64.fuxi needs no hunk: it includes MASTER, and 0026's
    SWEET_HFS precedent declares its option in MASTER alone. Declaring
    FUXI_ATH12K twice would only risk a duplicate-option error.
    """
    return (
        anchor("0112-fuxi-registry-trace.patch", "bsd/conf/files"),
        anchor("0026-sweet-builtin-hfs.patch", "config/MASTER"),
        anchor("0026-sweet-builtin-hfs.patch", "bsd/conf/Makefile.template"),
    )


def labels():
    text = (ATH12K / "tools/sources.txt").read_text()
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def staged():
    """label -> staged path under bsd/fuxi_ath12k.

    Two renames happen here. The first is a namespace: bsd/conf/files derives
    the object name from the file name alone, and XNU already has init.o, pm.o,
    core.o, scan.o, util.o, pci.o, chan.o, main.o and more, so every staged file
    gets an fxa_ prefix and the stack can never shadow a kernel object. The
    second resolves collisions inside the stack: 18 basenames appear twice or
    three times (ath12k/ce.c and wifi7/ce.c, mac80211/core.c and cfg80211/core.c
    and wifi7/core.c), so the colliding ones carry their parent directory or
    tree name as well.
    """
    items = []
    for label in labels():
        if ":" in label:
            tree, rest = label.split(":", 1)
            rel = f"{PREFIX[tree]}/{rest}"
        else:
            rest = label
            rel = rest
        items.append((label, rel, rest))
    counts = Counter(Path(rest).name for _, _, rest in items)
    out = []
    for label, rel, rest in items:
        base = Path(rest).name
        if counts[base] > 1:
            prefix = Path(rest).parent.name or label.split(":", 1)[0]
            name = f"fxa_{prefix}_{base}"
        else:
            name = f"fxa_{base}"
        out.append((label, str(Path(rel).with_name(name)).replace("\\", "/")))
    dupes = [n for n, c in Counter(Path(r).name for _, r in out).items() if c > 1]
    if dupes:
        raise SystemExit(f"object names still collide: {dupes}")
    return out


def group(label):
    if label.startswith("ath12k:"):
        return "ATH12K"
    if label.startswith("crypto:"):
        return "CRYPTO"
    if label == "src/nw_debug.c":
        return "ATH12K_FIRST"
    return "MODULE"


def obj(rel):
    return Path(rel).name[: -len(".c")] + ".o"


def make_manifest(rows):
    lines = [
        "# Staged by xnu/patches/9999-stage-ath12k.sh from $FUXI_ATH12K_TREE.",
        "# label<TAB>staged path under bsd/fuxi_ath12k<TAB>flag group",
        "# Generated by tools/ci/mk-ath12k-kernel.py; do not edit by hand.",
    ]
    for label, rel in rows:
        lines.append(f"{label}\t{rel}\t{group(label)}")
    return "\n".join(lines) + "\n"


def make_stage(rows):
    entries = "".join(f"    ({label!r}, {rel!r}),\n" for label, rel in rows)
    return f'''#!/bin/bash
# Stage the Ath12kWifi Linux Wi-Fi stack into bsd/fuxi_ath12k for the FUXI kernel.
# dxb runs every patches/*.sh before it applies any patches/*.patch, so this
# script only puts files on disk; 0117-fuxi-ath12k.patch declares them to config.
set -euo pipefail

[[ "${{MACHINE_CONFIG:-}}" == FUXI ]] || exit 0
: "${{FUXI_ATH12K_TREE:?FUXI_ATH12K_TREE must name a staged Ath12kWifi tree}}"
tree=$FUXI_ATH12K_TREE
[[ -f "$tree/tools/sources.txt" ]] || {{ echo "no tools/sources.txt under $tree" >&2; exit 1; }}
[[ -f "$tree/out/ath12k-src/core.c" ]] || {{ echo "$tree is not prepared (run tools/prepare.sh)" >&2; exit 1; }}
[[ -f bsd/kern/bsd_init.c ]] || {{ echo "not an XNU source root: $PWD" >&2; exit 1; }}

# The sources are not in git. tools/ci/build.sh stages the pinned ath12k commit
# and the pinned Linux tarball into $tree with the same tools the compile-only
# lane uses, so both lanes compile byte-identical sources.
python3 - "$tree" <<'PY'
from pathlib import Path
import shutil
import sys

tree = Path(sys.argv[1])
dest = Path('bsd/fuxi_ath12k')
if dest.exists():
    shutil.rmtree(dest)

ENTRIES = [
{entries}]

PREFIX = {{
    'ath12k': 'out/ath12k-src',
    'mac80211': 'out/mac80211',
    'cfg80211': 'out/cfg80211',
    'mhi': 'out/mhi-src',
    'qmi': 'deps/linux/drivers/soc/qcom',
    'crypto': 'out/crypto',
    'generated': 'out',
}}

missing = []
for label, rel in ENTRIES:
    if ':' in label:
        base, rest = label.split(':', 1)
        source = tree / PREFIX[base] / rest
    else:
        source = tree / label
    if not source.is_file():
        missing.append((label, str(source)))
        continue
    target = dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    # Dereference: out/ is a symlink farm, and a link leaving bsd/ would break
    # both the linker and any later tarball of the tree.
    data = source.resolve().read_bytes()
    if label == 'src/nw_crypto_mac80211.c':
        # This is the one unit whose first include is lx_pci.h, and crypto.h
        # (pulled in through aes_wrap.h) uses u8 and size_t without including
        # anything that defines them. State the dependency in the file itself
        # rather than relying on the forced include reaching it.
        data = b'#include "lx.h"' + bytes([10]) + data
    target.write_bytes(data)
if missing:
    for label, source in missing:
        print(f'missing {{source}} (from {{label}})', file=sys.stderr)
    raise SystemExit(1)

# Headers travel whole: every source includes its siblings by relative path, and
# the generated out/ trees are symlink farms into deps/, so copy contents rather
# than links. Only the include roots the Makefile names are walked; deps/linux is
# 7k files and only its soc/qcom headers are ever reached.
for sub in ('include', 'shim/include', 'shim/libc', 'shim/crypto', 'out', 'src',
            'deps/linux/drivers/soc/qcom'):
    source = tree / sub
    if not source.is_dir():
        continue
    for header in source.rglob('*'):
        if header.suffix not in ('.h', '.inc') or not header.is_file():
            continue
        target = dest / header.relative_to(tree)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(header.resolve().read_bytes())

print(f'staged {{len(ENTRIES)}} C files into {{dest}}')
PY

echo "ath12k: $(find bsd/fuxi_ath12k -name '*.c' | wc -l | tr -d ' ') sources staged for FUXI_ATH12K"
'''


def hunk(context_before, additions, context_after, start):
    """One hunk with explicit context; git apply tolerates the line numbers."""
    before = [line for line in context_before if line is not None]
    after = [line for line in context_after if line is not None]
    body = [" " + line for line in before]
    body += ["+" + line for line in additions]
    body += [" " + line for line in after]
    old = len(before) + len(after)
    new = old + len(additions)
    head = f"@@ -{start},{old} +{start},{new} @@\n"
    return head + "".join(line + "\n" for line in body)


def make_patch(rows):
    files_anchor, master_anchor, template_anchor = anchors()

    files_add = ["", "# Ath12kWifi Linux Wi-Fi stack, staged by xnu/patches/9999-stage-ath12k.sh"]
    for _, rel in rows:
        files_add.append(f"bsd/fuxi_ath12k/{rel}\t\toptional fuxi_ath12k")

    option = "options\t\tFUXI_ATH12K\t# Ath12kWifi Linux Wi-Fi stack (bsd/fuxi_ath12k)"

    mk = [
        "",
        "# Ath12kWifi Linux stack built in (bsd/fuxi_ath12k). The stack brings its",
        "# own Linux-flavoured headers, so each object gets the include order and the",
        "# renames its standalone build uses. _CFLAGS_ADD keeps them after the",
        "# kernel's own -D options; a plain _CFLAGS would drop them.",
        "FUXI_ATH12K_ROOT = $(SRCROOT)/bsd/fuxi_ath12k",
        "FUXI_ATH12K_BASE = -include lx.h -include lx_pci.h -w -std=gnu11 \\",
        "\t-isystem $(FUXI_ATH12K_ROOT)/shim/libc",
    ]
    seen = {}
    for label, rel in rows:
        name = obj(rel)
        kind = group(label)
        if name in seen:
            raise SystemExit(f"duplicate object {name}")
        seen[name] = kind
        mk.append("FUXI_ATH12K_INC_{} = {}".format(
            name, " ".join("-I$(FUXI_ATH12K_ROOT)/" + p for p in INCLUDES[kind])))
        mk.append("FUXI_ATH12K_DEF_{} = {}".format(
            name, " ".join(CRYPTO_DEFINES if kind == "CRYPTO" else BASE_DEFINES)))
    mk.append("FUXI_ATH12K_OBJS = " + " ".join(seen))
    mk.append("$(foreach o,$(FUXI_ATH12K_OBJS),$(eval $(o)_CFLAGS_ADD += "
              "$(FUXI_ATH12K_BASE) $(FUXI_ATH12K_INC_$(o)) $(FUXI_ATH12K_DEF_$(o))))")

    out = []
    out.append("--- a/config/MASTER\n+++ b/config/MASTER\n")
    out.append(hunk([], [option], master_anchor, 115))

    out.append("--- a/bsd/conf/files\n+++ b/bsd/conf/files\n")
    out.append(hunk(files_anchor[:1], files_add, files_anchor[1:], 108))

    out.append("--- a/bsd/conf/Makefile.template\n+++ b/bsd/conf/Makefile.template\n")
    out.append(hunk(template_anchor[:1], mk, template_anchor[1:], 54))
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="fail if the checked-in files differ from the generator")
    args = ap.parse_args()
    rows = staged()
    wanted = {
        MANIFEST: make_manifest(rows),
        STAGE: make_stage(rows),
        PATCH: make_patch(rows),
    }
    drift = []
    for path, text in wanted.items():
        current = path.read_text() if path.exists() else None
        if current != text:
            drift.append(path)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(text)
    print(f"labels={len(rows)} objects={len({obj(r) for _, r in rows})}")
    if args.check:
        if drift:
            raise SystemExit("generated files differ: " + ", ".join(p.name for p in drift))
        print("ATH12K_KERNEL_IN_SYNC")
    else:
        for path in wanted:
            print(("wrote " if path in drift else "unchanged ") + str(path.relative_to(ROOT)))


if __name__ == "__main__":
    main()
