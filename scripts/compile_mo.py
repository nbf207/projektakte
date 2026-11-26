"""Pure-Python GNU gettext .po -> .mo compiler (no external msgfmt required).

Supports multiline strings, standard C escapes, msgctxt, and plural forms.
"""
from __future__ import annotations

import array
import ast
import struct
import sys
from pathlib import Path


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if not (raw.startswith('"') and raw.endswith('"')):
        raise ValueError(f'Expected quoted PO string, got: {raw!r}')
    return ast.literal_eval(raw)


def parse_po(text: str) -> dict[bytes, bytes]:
    """Parse a UTF-8 .po file into a mapping of binary gettext keys -> values."""
    messages: dict[bytes, bytes] = {}
    lines = text.splitlines()
    lines.append('')  # sentinel flush

    msgctxt: list[str] | None = None
    msgid: list[str] | None = None
    msgid_plural: list[str] | None = None
    msgstr: dict[int, list[str]] = {}
    section: tuple[str, int] | None = None
    fuzzy = False

    def flush() -> None:
        nonlocal msgctxt, msgid, msgid_plural, msgstr, section, fuzzy
        if msgid is not None and not fuzzy:
            src = ''.join(msgid)
            if msgctxt is not None:
                key_str = ''.join(msgctxt) + '\x04' + src
            else:
                key_str = src
            if msgid_plural is not None:
                key_str = key_str + '\x00' + ''.join(msgid_plural)
                indices = sorted(msgstr)
                vals = [''.join(msgstr[i]) for i in indices]
                if any(vals):
                    messages[key_str.encode('utf-8')] = '\x00'.join(vals).encode('utf-8')
            else:
                val = ''.join(msgstr.get(0, []))
                if val or src == '':
                    messages[key_str.encode('utf-8')] = val.encode('utf-8')
        msgctxt = None
        msgid = None
        msgid_plural = None
        msgstr = {}
        section = None
        fuzzy = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line.startswith('#,'):
            flags = {f.strip() for f in line[2:].split(',')}
            if 'fuzzy' in flags:
                fuzzy = True
            continue
        if line.startswith('#'):
            continue
        if line.startswith('msgctxt '):
            msgctxt = [_unquote(line[8:])]
            section = ('msgctxt', 0)
        elif line.startswith('msgid_plural '):
            msgid_plural = [_unquote(line[13:])]
            section = ('msgid_plural', 0)
        elif line.startswith('msgid '):
            msgid = [_unquote(line[6:])]
            section = ('msgid', 0)
        elif line.startswith('msgstr['):
            end = line.index(']')
            idx = int(line[7:end])
            msgstr[idx] = [_unquote(line[end + 1:].strip())]
            section = ('msgstr', idx)
        elif line.startswith('msgstr '):
            msgstr[0] = [_unquote(line[7:])]
            section = ('msgstr', 0)
        elif line.startswith('"') and section is not None:
            piece = _unquote(line)
            kind, idx = section
            if kind == 'msgctxt' and msgctxt is not None:
                msgctxt.append(piece)
            elif kind == 'msgid' and msgid is not None:
                msgid.append(piece)
            elif kind == 'msgid_plural' and msgid_plural is not None:
                msgid_plural.append(piece)
            elif kind == 'msgstr':
                msgstr.setdefault(idx, []).append(piece)

    return messages


def compile_mo_bytes(messages: dict[bytes, bytes]) -> bytes:
    """Serialize a gettext key/value mapping into standard GNU .mo binary bytes."""
    keys = sorted(messages.keys())
    offsets: list[tuple[int, int, int, int]] = []
    ids = b''
    strs = b''
    for key in keys:
        val = messages[key]
        offsets.append((len(ids), len(key), len(strs), len(val)))
        ids += key + b'\x00'
        strs += val + b'\x00'

    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets: list[int] = []
    voffsets: list[int] = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    all_offsets = koffsets + voffsets

    header = struct.pack(
        'Iiiiiii',
        0x950412DE,  # GNU gettext magic
        0,           # format revision
        len(keys),   # number of entries
        7 * 4,       # offset of original string table
        7 * 4 + len(keys) * 8,  # offset of translation string table
        0,           # hash table size
        0,           # hash table offset
    )
    return header + array.array('i', all_offsets).tobytes() + ids + strs


def compile_file(po_path: Path, mo_path: Path | None = None) -> Path:
    mo_path = mo_path or po_path.with_suffix('.mo')
    messages = parse_po(po_path.read_text(encoding='utf-8'))
    mo_path.write_bytes(compile_mo_bytes(messages))
    return mo_path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args:
        targets = [Path(p) for p in args]
    else:
        root = Path(__file__).resolve().parent.parent / 'locale'
        targets = sorted(root.rglob('*.po'))
    if not targets:
        print('No .po files found.')
        return 0
    for po in targets:
        mo = compile_file(po)
        print(f'Compiled {po} -> {mo}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
