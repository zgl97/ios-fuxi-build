#!/usr/bin/env python3
"""Print the hunks a patch applies to one file, with line kinds preserved.

A patch in this repo may only be applied after another has landed, so its
context must be text an earlier patch produced -- either an added line or a
context line of that earlier hunk. This prints them verbatim, so a generator can
quote them without retyping whitespace or tabs.

Usage:
  show-patch-hunk.py PATCH FILE            print every line, kind first
  show-patch-hunk.py PATCH FILE --after N  print the N context lines that follow
                                           the last added line
  show-patch-hunk.py PATCH FILE --before N print the N context lines before it
"""
import argparse
from pathlib import Path
import sys

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("patch", type=Path)
ap.add_argument("target", help="path as it appears after '+++ b/'")
ap.add_argument("--after", type=int, default=0)
ap.add_argument("--before", type=int, default=0)
args = ap.parse_args()

lines = args.patch.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
current = None
hunks = []
for line in lines:
    if line.startswith("+++ b/"):
        current = line[6:]
        continue
    if line.startswith("--- a/") or line.startswith("diff --git"):
        current = None
        continue
    if line.startswith("@@"):
        if current == args.target:
            hunks.append([])
        else:
            hunks.append(None)
        continue
    if not hunks or hunks[-1] is None:
        continue
    if line[:1] in (" ", "+", "-", "\\"):
        hunks[-1].append(line)

body = [line for hunk in hunks if hunk for line in hunk]
if not body:
    sys.exit(f"no hunks for {args.target} in {args.patch}")

if args.after or args.before:
    last_added = max(i for i, line in enumerate(body) if line.startswith("+"))
    if args.after:
        tail = [line[1:] for line in body[last_added + 1:] if line.startswith(" ")][: args.after]
        if len(tail) < args.after:
            sys.exit(f"only {len(tail)} context lines follow in {args.target}")
        print("\n".join(tail))
    if args.before:
        head = [line[1:] for line in body[:last_added] if line.startswith(" ")][-args.before:]
        if len(head) < args.before:
            sys.exit(f"only {len(head)} context lines precede in {args.target}")
        print("\n".join(head))
else:
    for line in body:
        print(line)
