#!/usr/bin/env python3
"""Print the defined symbol names of a Mach-O kernel image, one per line.

The ath12k lane compiles objects and then asks what those objects want from the
kernel. Answering that needs the kernel's side of the same names, read from the
symbol table of a kernel binary rather than from a header: a name can be
declared in a header and still be missing from the linked image.

    python3 tools/ci/kernel-exports.py kernel.development.fuxi > exports.txt

Names come out without the leading underscore, so they compare directly with
what `llvm-nm -j` prints for a C object.
"""
import struct
import sys

# N_SECT, N_ABS and the indirect variants are definitions; N_UNDF and the
# stabs entries are not.
DEFINED_TYPES = {0x0E, 0x0F, 0x02, 0x0A, 0x0B, 0x01}


def slices(data):
    """Yield (offset, size) for each architecture slice of a Mach-O file."""
    magic = struct.unpack_from('<I', data, 0)[0]
    if magic in (0xCAFEBABE, 0xCAFEBABF):
        count = struct.unpack_from('>I', data, 4)[0]
        for i in range(count):
            _cpu, _sub, offset, size, _align = struct.unpack_from('>IIIII', data, 8 + i * 20)
            yield offset, size
    else:
        yield 0, len(data)


def defined_names(data, base):
    magic = struct.unpack_from('<I', data, base)[0]
    if magic != 0xFEEDFACF:
        raise SystemExit('not a 64-bit little-endian Mach-O at %#x: %#x' % (base, magic))
    ncmds = struct.unpack_from('<I', data, base + 16)[0]
    off = base + 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, off)
        if cmd == 0x2:  # LC_SYMTAB
            symoff, nsyms, stroff, _strsize = struct.unpack_from('<IIII', data, off + 8)
            names = set()
            for i in range(nsyms):
                strx, ntype = struct.unpack_from('<IB', data, base + symoff + i * 16)
                if ntype & 0xE0:            # N_STAB debug entry
                    continue
                if (ntype & 0x0E) not in DEFINED_TYPES:
                    continue
                start = base + stroff + strx
                end = data.index(b'\0', start)
                name = data[start:end].decode('utf-8', 'replace')
                names.add(name[1:] if name.startswith('_') else name)
            return names
        off += cmdsize
    raise SystemExit('no LC_SYMTAB')


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: kernel-exports.py KERNEL')
    data = open(sys.argv[1], 'rb').read()
    names = set()
    for base, _size in slices(data):
        names |= defined_names(data, base)
    sys.stdout.write(''.join(n + '\n' for n in sorted(names)))


if __name__ == '__main__':
    main()
