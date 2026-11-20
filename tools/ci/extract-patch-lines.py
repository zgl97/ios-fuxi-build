#!/usr/bin/env python3
"""Extract the exact added lines from an existing patch, for use as anchors.

A patch in this repo may only be applied after another patch has landed, so its
context must be text an earlier patch produced. That text is in the earlier
patch's own `+` lines; this prints it verbatim so a generator can quote it
without retyping whitespace.

Usage:
  extract-patch-lines.py PATCH FILE          print the added lines of FILE's hunks
  extract-patch-lines.py PATCH FILE --tail N print the last N of them
"""
import argparse
from pathlib import Path
import sys

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("patch", type=Path)
ap.add_argument("target", help="path as it appears after '+++ b/'")
ap.add_argument("--tail", type=int, default=0)
args = ap.parse_args()

lines = args.patch.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
out = []
current = None
in_hunk = False
for line in lines:
    if line.startswith("+++ b/"):
        current = line[6:]
        in_hunk = False
        continue
    if line.startswith("--- a/") or line.startswith("diff --git"):
        in_hunk = False
        continue
    if line.startswith("@@"):
        in_hunk = current == args.target
        continue
    if not in_hunk:
        continue
    if line.startswith("+"):
        out.append(line[1:])

if not out:
    sys.exit(f"no added lines for {args.target} in {args.patch}")
if args.tail:
    out = out[-args.tail:]
print("\n".join(out))
