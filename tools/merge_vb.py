#!/usr/bin/env python3
"""Splice an OrangeFox/TWRP recovery ramdisk fragment into a vendor_boot image.

Typical use: take the vendor_boot.img produced by your ROM build (which keeps
the ROM's own vendor ramdisk with its kernel modules) and add OrangeFox's
recovery fragment to it. The resulting image can be flashed with

    fastboot flash vendor_boot vendor_boot_orangefox.img

and keeps normal boot fully intact, because the ROM's platform ramdisk, dtb
and cmdline are preserved byte for byte.

Usage:
  # fragment stored as a separate file (e.g. extracted from an OF image)
  python3 merge_vb.py --base rom_vendor_boot.img \
      --fragment fox_ramdisk.lz4 -o vendor_boot_orangefox.img

  # or pull the fragment straight out of another vendor_boot image
  python3 merge_vb.py --base rom_vendor_boot.img \
      --fragment-from OrangeFox-vendor_boot.img --fragment-name recovery \
      -o vendor_boot_orangefox.img

If the base image already contains a fragment with the same name it is
replaced; any other fragments are preserved in their original order.
"""
import argparse
import struct
import sys

PAGE = 0  # filled from header


def align(x, a):
    return (x + a - 1) & ~(a - 1)


def set_uint32(buf, off, val):
    struct.pack_into('<I', buf, off, val)


def parse(data):
    assert data[:8] == b'VNDRBOOT', 'not a vendor_boot image'
    hdr_ver, page_size = struct.unpack('<2I', data[8:16])
    assert hdr_ver >= 4, f'vendor_boot header v{hdr_ver} has no ramdisk table'
    vramdisk_size = struct.unpack('<I', data[24:28])[0]
    header_size, dtb_size = struct.unpack('<2I', data[2096:2104])
    table_size, table_num, table_esize, bootconfig_size = struct.unpack('<4I', data[2112:2128])

    off = align(header_size, page_size)
    vramdisk_off = off
    off = align(off + vramdisk_size, page_size)
    dtb_off = off
    off = align(off + dtb_size, page_size)
    table_off = off
    off = align(off + table_size, page_size)
    bootconfig_off = off

    entries = []
    for i in range(table_num):
        e = data[table_off + i * table_esize: table_off + (i + 1) * table_esize]
        size, ent_off, etype = struct.unpack('<3I', e[:12])
        name = e[12:44].split(b'\0')[0].decode(errors='replace')
        board_id = e[44:44 + 16]
        entries.append(dict(size=size, offset=ent_off, type=etype,
                            name=name, board_id=board_id))

    return dict(
        data=data, hdr_ver=hdr_ver, page_size=page_size,
        vramdisk_off=vramdisk_off, vramdisk_size=vramdisk_size,
        dtb_off=dtb_off, dtb_size=dtb_size,
        table_off=table_off, table_size=table_size, table_num=table_num,
        table_esize=table_esize, bootconfig_off=bootconfig_off,
        bootconfig_size=bootconfig_size, entries=entries,
    )


def build(base, new_fragment, frag_name):
    data = base['data']
    page = base['page_size']
    header_size = struct.unpack('<I', data[2096:2100])[0]

    kept = [e for e in base['entries'] if e['name'] != frag_name]
    replaced = len(base['entries']) - len(kept)

    # Rebuild the vendor ramdisk section from the kept entries, in order, then
    # append the new fragment.
    pieces = bytearray()
    new_entries = []
    for e in kept:
        start = base['vramdisk_off'] + e['offset']
        blob = data[start:start + e['size']]
        if len(blob) != e['size']:
            raise SystemExit(f'entry {e["name"]!r} runs past the image')
        ne = dict(e)
        ne['offset'] = len(pieces)
        pieces += blob
        new_entries.append(ne)

    new_entries.append(dict(size=len(new_fragment), offset=len(pieces),
                            type=2, name=frag_name, board_id=b'\0' * 16))
    pieces += new_fragment

    esize = base['table_esize'] or 108
    table = bytearray(esize * len(new_entries))
    for i, e in enumerate(new_entries):
        struct.pack_into('<3I', table, i * esize, e['size'], e['offset'], e['type'])
        nb = e['name'].encode()[:31]
        table[i * esize + 12:i * esize + 12 + len(nb)] = nb
        table[i * esize + 44:i * esize + 60] = (e['board_id'] or b'\0' * 16)[:16]

    out = bytearray()
    out += data[:header_size]                       # header (patched below)
    out += b'\0' * (align(len(out), page) - len(out))
    vramdisk_off = len(out)
    out += pieces
    out += b'\0' * (align(len(out), page) - len(out))
    dtb_off = len(out)
    out += data[base['dtb_off']:base['dtb_off'] + base['dtb_size']]
    out += b'\0' * (align(len(out), page) - len(out))
    table_off = len(out)
    out += table
    out += b'\0' * (align(len(out), page) - len(out))
    bootconfig_off = len(out)
    out += data[base['bootconfig_off']:base['bootconfig_off'] + base['bootconfig_size']]

    # patch header
    set_uint32(out, 24, len(pieces))                # vendor_ramdisk_size
    set_uint32(out, 2112, len(table))               # ramdisk_table_size
    set_uint32(out, 2116, len(new_entries))         # ramdisk_table_entry_num
    return bytes(out), dict(replaced=replaced, entries=new_entries,
                            vramdisk_off=vramdisk_off, dtb_off=dtb_off,
                            table_off=table_off, bootconfig_off=bootconfig_off)


def get_fragment_from_image(path, name):
    img = open(path, 'rb').read()
    p = parse(img)
    for e in p['entries']:
        if e['name'] == name:
            start = p['vramdisk_off'] + e['offset']
            return img[start:start + e['size']], p
    raise SystemExit(f'no vendor ramdisk fragment named {name!r} in {path} '
                     f'(have: {[e["name"] for e in p["entries"]]})')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', help='vendor_boot.img to modify')
    ap.add_argument('--extract', action='store_true',
                    help='just extract a fragment from --fragment-from')
    ap.add_argument('--fragment', help='recovery ramdisk fragment file')
    ap.add_argument('--fragment-from', help='vendor_boot.img to take the fragment from')
    ap.add_argument('--fragment-name', default='recovery')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--pad-to', type=int, default=0,
                    help='pad output to this many bytes (e.g. partition size)')
    args = ap.parse_args()

    if args.extract:
        if not args.fragment_from:
            raise SystemExit('--extract needs --fragment-from')
        frag, p = get_fragment_from_image(args.fragment_from, args.fragment_name)
        open(args.output, 'wb').write(frag)
        print(f'extracted {args.fragment_name!r} fragment from {args.fragment_from} '
              f'({len(frag)} bytes) -> {args.output}')
        return

    if not args.base:
        raise SystemExit('--base is required (or use --extract)')

    base_data = open(args.base, 'rb').read()
    base = parse(base_data)

    if args.fragment:
        frag = open(args.fragment, 'rb').read()
        if not frag:
            raise SystemExit('empty fragment file')
    elif args.fragment_from:
        frag, _ = get_fragment_from_image(args.fragment_from, args.fragment_name)
    else:
        raise SystemExit('need --fragment or --fragment-from')

    out, info = build(base, frag, args.fragment_name)
    target = args.pad_to or len(base_data)
    if len(out) > target:
        print(f'note: image is {len(out)} bytes, larger than {target}; '
              f'flashing it is still fine as long as it fits the partition')
    else:
        out += b'\0' * (target - len(out))

    open(args.output, 'wb').write(out)

    print(f'base:  {args.base} ({len(base_data)} bytes, {len(base["entries"])} entries)')
    for e in info['entries']:
        print(f'  entry {e["name"]!r:<12} type={e["type"]} size={e["size"]} offset={e["offset"]}')
    print(f'replaced {info["replaced"]} fragment(s) named {args.fragment_name!r}')
    print(f'wrote {args.output} ({len(out)} bytes)')
    print('note: any AVB/vbmeta trailer from the base image is not copied (it would be '
          'stale for the modified image anyway); flash on an unlocked bootloader')


if __name__ == '__main__':
    main()
