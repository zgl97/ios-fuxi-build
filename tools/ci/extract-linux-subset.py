#!/usr/bin/env python3
"""Extract only the named paths from the pinned Linux tarball.

`tar --strip-components=1 -T paths` is not portable: GNU tar matches the list
against the archive's own names, while bsdtar (which is what macOS ships, and
what the runner uses) strips the first component before matching, so every
name misses and the extraction fails. Doing the selection here keeps the
behaviour identical on both, and it lets a directory entry pull its whole
subtree in one pass.

Usage: extract-linux-subset.py ARCHIVE DEST PATHS_FILE
"""
import sys
import tarfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print('usage: extract-linux-subset.py ARCHIVE DEST PATHS_FILE', file=sys.stderr)
        return 2
    archive, dest, paths_file = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    # source-lock.json names directories with a trailing slash and single files
    # without one. Normalise both, or `want + '/'` becomes `ath//` and every
    # directory entry silently misses.
    wanted = [line.strip().strip('/') for line in paths_file.read_text().splitlines() if line.strip()]
    if not wanted:
        print('empty path list', file=sys.stderr)
        return 2
    dest.mkdir(parents=True, exist_ok=True)

    files = 0
    with tarfile.open(archive, 'r:xz') as tar:
        for member in tar:
            parts = member.name.split('/', 1)
            if len(parts) != 2:
                continue
            relative = parts[1]
            for want in wanted:
                if relative == want or relative.startswith(want + '/'):
                    # Rewrite the name so the top-level linux-7.0.14/ directory
                    # does not reappear under DEST.
                    member.name = relative
                    if member.islnk():
                        member.linkname = member.linkname.split('/', 1)[-1]
                    tar.extract(member, path=dest, set_attrs=False)
                    if member.isfile():
                        files += 1
                    break

    missing = [w for w in wanted if not (dest / w).exists()]
    print(f'extracted files={files} paths={len(wanted)}')
    if missing:
        print('missing: ' + ' '.join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
