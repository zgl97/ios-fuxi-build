#!/usr/bin/env python3
"""Structural check of xnu/patches/0099-fuxi-wlan.patch.

CI applies this patch with `git apply` and rejects the whole build when the
text is malformed, but nothing in the suite looked at the patch structure
itself. A blank line between two hunks is enough: git reads a bare empty line
as a context line, the declared counts stop adding up, and the build dies with
"corrupt patch at line N" — which is exactly how the M1 board layer first
failed, at line 7038, after the new hunks were appended.

So: every line inside a hunk must be '+', '-' or ' ' (a single space, never
empty), and the counts in the header must match the body. A file may carry
more than one hunk, and the whole-file hunks (@@ -0,0 +1,N @@) must match
their sources byte for byte.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'xnu/patches/0099-fuxi-wlan.patch'

HUNK = re.compile(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@')

# (target inside the kernel tree, source under xnu/fuxi-wlan/)
SOURCES = [
    ('bsd/dev/fuxi_wlan.c', 'fuxi_wlan.c'),
    ('bsd/dev/fuxi_wlan_board.c', 'fuxi_wlan_board.c'),
    ('bsd/dev/nw_abi.h', 'nw_abi.h'),
]


def parse(lines):
    """Yield (path, hunk_header_index, old_count, new_count) for every hunk."""
    i = 0
    path = None
    while i < len(lines):
        line = lines[i]
        if line.startswith('--- a/'):
            path = line[6:]
            if lines[i + 1] != '+++ b/' + path:
                raise SystemExit('line %d: header pair does not match (%r / %r)'
                                 % (i + 1, line, lines[i + 1]))
            i += 2
            continue
        m = HUNK.match(line)
        if m:
            if path is None:
                raise SystemExit('line %d: hunk before any file header' % (i + 1))
            yield path, i, int(m.group(2)), int(m.group(4))
        i += 1


def main():
    text = PATCH.read_text()
    lines = text.split('\n')
    trailing_newline = text.endswith('\n')
    if trailing_newline:
        lines.pop()

    problems = []
    hunks = []
    for path, header, old_count, new_count in parse(lines):
        # A hunk body runs until the next hunk header or file header.
        end = len(lines)
        for j in range(header + 1, len(lines)):
            if lines[j].startswith('--- a/') or HUNK.match(lines[j]):
                end = j
                break
        body = lines[header + 1:end]
        hunks.append((path, header, old_count, new_count, body))

        plus = minus = ctx = 0
        for offset, line in enumerate(body):
            if line.startswith('+'):
                plus += 1
            elif line.startswith('-'):
                minus += 1
            elif line.startswith(' '):
                ctx += 1
            else:
                problems.append('%s: line %d is not a hunk line: %r'
                                % (path, header + 2 + offset, line))
        if plus + ctx != new_count:
            problems.append('%s: line %d header says %d new lines, body has %d'
                            % (path, header + 1, new_count, plus + ctx))
        if minus + ctx != old_count:
            problems.append('%s: line %d header says %d old lines, body has %d'
                            % (path, header + 1, old_count, minus + ctx))
        if not body:
            problems.append('%s: line %d empty hunk' % (path, header + 1))

    # The whole-file hunks must still equal their sources.
    for target, source in SOURCES:
        found = [h for h in hunks if h[0] == target and h[2] == 0]
        if not found:
            problems.append('%s: no whole-file hunk' % target)
            continue
        _path, header, _old, _new, body = found[0]
        added = [line[1:] for line in body if line.startswith('+')]
        want = (ROOT / 'xnu/fuxi-wlan' / source).read_text().split('\n')
        if want and want[-1] == '':
            want.pop()
        if added != want:
            where = next((n for n, (a, b) in enumerate(zip(added, want)) if a != b),
                         min(len(added), len(want)))
            problems.append('%s: line %d body differs from %s at line %d'
                            % (target, header + 1, source, where + 1))

    touched = {h[0] for h in hunks}
    want_files = ['bsd/conf/files', 'bsd/dev/sweet_ufs.c', 'bsd/dev/fuxi_wlan.c',
                  'bsd/dev/wlan.c', 'bsd/dev/fuxi_wlan_board.c', 'bsd/dev/nw_abi.h']
    missing = [p for p in want_files if p not in touched]
    if missing:
        problems.append('patch does not touch: %s' % ', '.join(missing))

    if problems:
        for problem in problems:
            print('PATCH PROBLEM ' + problem)
        print('PATCH_STRUCTURE_FAIL problems=%d' % len(problems))
        return 1
    print('PATCH_STRUCTURE_OK files=%d hunks=%d lines=%d trailing_newline=%s'
          % (len(touched), len(hunks), len(lines), trailing_newline))
    return 0


if __name__ == '__main__':
    sys.exit(main())
