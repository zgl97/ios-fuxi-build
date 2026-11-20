#!/bin/bash
# Local sanity check for the generated XNU wiring, run from the driver checkout.
# Everything here is what CI will do, minus the XNU tree itself.
set -uo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root" || exit 1
fail=0

say() { printf '%s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fail=1; }

script=xnu/patches/9999-stage-ath12k.sh
manifest=xnu/fuxi-ath12k/manifest.txt
patch=xnu/patches/0117-fuxi-ath12k.patch

[[ -f $script ]] || bad "missing $script"
[[ -f $manifest ]] || bad "missing $manifest"
[[ -f $patch ]] || bad "missing $patch"

bash -n "$script" && say "bash syntax ok" || bad "bash syntax"

python3 - "$script" "$manifest" "$patch" <<'PY' || fail=1
from pathlib import Path
import ast
import re
import sys

script = Path(sys.argv[1]).read_text()
manifest = Path(sys.argv[2]).read_text()
patch = Path(sys.argv[3]).read_text()

# Every object forces lx.h in before lx_pci.h. lx_pci.h reaches lx.h only through
# lx_ath.h -> lx_skb.h; a unit that includes lx_pci.h first (nw_crypto_mac80211.c)
# saw crypto.h parsed before u8 and size_t existed when only lx_pci.h was forced.
if "-include lx.h -include lx_pci.h" not in patch:
    raise SystemExit("the forced include must be lx.h then lx_pci.h")

# The staging script only copies sources now; the four XNU files are edited by
# the patch, because dxb runs every .sh before it applies any .patch.
bodies = re.findall(r"<<'PY'\n(.*?)\nPY\n", script, re.S)
if len(bodies) != 1:
    raise SystemExit(f"expected 1 python heredoc, found {len(bodies)}")
for index, body in enumerate(bodies):
    try:
        ast.parse(body)
    except SyntaxError as error:
        raise SystemExit(f"heredoc {index} does not parse: {error}")

rows = [line.split('\t') for line in manifest.splitlines() if line and not line.startswith('#')]
if len(rows) != 125:
    raise SystemExit(f"manifest has {len(rows)} rows, expected 125")
labels = [r[0] for r in rows]
paths = [r[1] for r in rows]
objects = [Path(p).name[:-2] + '.o' for p in paths]
if len(set(objects)) != 125:
    dupes = sorted({o for o in objects if objects.count(o) > 1})
    raise SystemExit(f"object names collide: {dupes}")
for row in rows:
    if len(row) != 3:
        raise SystemExit(f"manifest row is not three fields: {row}")
    if row[2] not in ('MODULE', 'ATH12K', 'ATH12K_FIRST', 'CRYPTO'):
        raise SystemExit(f"unknown flag group {row[2]} for {row[0]}")

# Every label must appear in the script's ENTRIES, or a source silently drops.
entries = re.search(r"ENTRIES = \[\n(.*?)\n\]", script, re.S).group(1)
staged = re.findall(r"\('([^']+)', '([^']+)'\)", entries)
if [l for l, _ in staged] != labels:
    raise SystemExit("script ENTRIES and manifest disagree on labels")
if [p.replace('\\', '/') for _, p in staged] != [p.replace('\\', '/') for p in paths]:
    raise SystemExit("script ENTRIES and manifest disagree on paths")

# The objects table lives in the patch, not the staging script.
missing = [o for o in objects if o not in patch]
if missing:
    raise SystemExit(f"patch never mentions {missing}")

# The patch must touch exactly the four wiring files, and every hunk must agree
# with its own header -- a hunk whose counts drift is what "corrupt patch" means.
targets, current, old, new, hunk = [], None, 0, 0, None
for number, line in enumerate(patch.splitlines(), start=1):
    if line.startswith('--- a/'):
        current = line[6:]
        continue
    if line.startswith('+++ b/'):
        if line[6:] != current:
            raise SystemExit(f"line {number}: --- and +++ disagree")
        targets.append(current)
        continue
    if line.startswith('@@'):
        if hunk is not None and (old or new):
            raise SystemExit(f"line {number}: hunk has {old} old / {new} new lines left")
        match = re.match(r'@@ -\d+,(\d+) \+\d+,(\d+) @@$', line)
        if not match:
            raise SystemExit(f"line {number}: unparsable hunk header {line!r}")
        old, new = int(match.group(1)), int(match.group(2))
        continue
    if hunk is None and line[:1] in (' ', '+', '-'):
        hunk = True
    if line.startswith(' '):
        old -= 1
        new -= 1
    elif line.startswith('-'):
        old -= 1
    elif line.startswith('+'):
        new -= 1
    elif line.strip():
        raise SystemExit(f"line {number}: unexpected line {line!r}")
if old or new:
    raise SystemExit(f"last hunk has {old} old / {new} new lines left")

wanted = ['config/MASTER', 'bsd/conf/files', 'bsd/conf/Makefile.template']
if targets != wanted:
    raise SystemExit(f"patch touches {targets}, expected {wanted}")

# Every staged source needs a bsd/conf/files entry, or it is copied but never built.
for rel in paths:
    if f"bsd/fuxi_ath12k/{rel}" not in patch:
        raise SystemExit(f"patch never adds bsd/fuxi_ath12k/{rel} to bsd/conf/files")

# The object list in the patch must be the manifest's, in the same order.
listed = re.search(r'^\+FUXI_ATH12K_OBJS = (.*)$', patch, re.M).group(1).split()
if listed != objects:
    raise SystemExit("patch object list and manifest disagree")

# rate.c uses CONFIG_MAC80211_RC_DEFAULT as both a string literal and a
# strlen() argument, so the flag has to reach the compiler with its quotes
# intact. It travels through a make variable and then the shell, which strips
# one level: the plain spelling -D...="" arrives empty and rate.c fails with
# three errors. Require the escaped spelling in every object's flag line.
plain = '-DCONFIG_MAC80211_RC_DEFAULT=""'
escaped = '-DCONFIG_MAC80211_RC_DEFAULT=\\"\\"'
for obj in objects:
    line = re.search(r'^\+FUXI_ATH12K_DEF_' + re.escape(obj) + r' = (.*)$',
                     patch, re.M)
    if line is None:
        raise SystemExit(f"patch has no flag line for {obj}")
    if escaped not in line.group(1):
        raise SystemExit(f"{obj} does not get the escaped RC_DEFAULT flag")
    if plain in line.group(1):
        raise SystemExit(f"{obj} still gets the unescaped RC_DEFAULT flag")

print(f"manifest rows=125 objects={len(set(objects))} heredocs={len(bodies)} "
      f"patch targets={len(targets)}")
PY

python3 tools/ci/mk-ath12k-kernel.py --check || fail=1

if [[ $fail -ne 0 ]]; then
    say "ATH12K_KERNEL_CHECK_FAIL"
    exit 1
fi
say "ATH12K_KERNEL_CHECK_OK"
