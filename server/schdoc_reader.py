"""Read the records of an Altium schematic document (.SchDoc) without Altium.

A SchDoc is an OLE compound file. Its "FileHeader" stream holds one record per
object, each a 4-byte header (length in the low 24 bits, a type byte on top)
followed by either "|KEY=VALUE|..." text or a binary record. This reads the
text records into dicts, in file order; OWNERINDEX links a child (pin,
designator, parameter, body graphic) to the record of its component.

Only the standard library is used, so the check can run anywhere and needs no
running Altium.
"""
import struct
from pathlib import Path

_FREESECT, _ENDOFCHAIN = 0xFFFFFFFF, 0xFFFFFFFE


class CompoundFile:
    """Minimal reader for OLE compound files (MS-CFB), enough to get a stream."""

    def __init__(self, data: bytes):
        if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError("not an OLE compound file")
        self.data = data
        self.sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
        self.mini_size = 1 << struct.unpack_from("<H", data, 32)[0]
        n_fat, first_dir, _, self.mini_cutoff, first_minifat, n_minifat, first_difat, n_difat = \
            struct.unpack_from("<IIIIIIII", data, 44)

        difat = list(struct.unpack_from("<109I", data, 76))
        sect = first_difat
        per = self.sector_size // 4 - 1
        for _ in range(n_difat):
            vals = struct.unpack_from(f"<{per + 1}I", self._sector(sect))
            difat.extend(vals[:per])
            sect = vals[per]
        fat = []
        for s in difat[:n_fat]:
            fat.extend(struct.unpack_from(f"<{self.sector_size // 4}I", self._sector(s)))
        self.fat = fat

        dir_data = self._chain(first_dir)
        self.entries = []
        for off in range(0, len(dir_data), 128):
            e = dir_data[off:off + 128]
            name_len = struct.unpack_from("<H", e, 64)[0]
            name = e[:max(name_len - 2, 0)].decode("utf-16-le", "replace")
            obj_type = e[66]
            start, size = struct.unpack_from("<IQ", e, 116)
            self.entries.append((name, obj_type, start, size))

        root = self.entries[0]
        self.ministream = self._chain(root[2])[:root[3]] if root[3] else b""
        self.minifat = []
        if n_minifat:
            raw = self._chain(first_minifat)
            self.minifat = list(struct.unpack_from(f"<{len(raw) // 4}I", raw))

    def _sector(self, s: int) -> bytes:
        off = (s + 1) * self.sector_size
        return self.data[off:off + self.sector_size]

    def _chain(self, start: int) -> bytes:
        out, s, seen = [], start, 0
        while s not in (_ENDOFCHAIN, _FREESECT) and seen <= len(self.fat):
            out.append(self._sector(s))
            s = self.fat[s]
            seen += 1
        return b"".join(out)

    def _mini_chain(self, start: int) -> bytes:
        out, s, seen = [], start, 0
        while s not in (_ENDOFCHAIN, _FREESECT) and seen <= len(self.minifat):
            out.append(self.ministream[s * self.mini_size:(s + 1) * self.mini_size])
            s = self.minifat[s]
            seen += 1
        return b"".join(out)

    def stream(self, name: str) -> bytes:
        for ename, obj_type, start, size in self.entries:
            if obj_type == 2 and ename.lower() == name.lower():
                raw = self._mini_chain(start) if size < self.mini_cutoff else self._chain(start)
                return raw[:size]
        raise KeyError(name)


def parse_records(stream: bytes) -> list:
    """Split a FileHeader stream into records. Binary records become None, so
    list indices still match the OWNERINDEX numbering."""
    records, off = [], 0
    while off + 4 <= len(stream):
        header = struct.unpack_from("<I", stream, off)[0]
        length, kind = header & 0xFFFFFF, header >> 24
        body = stream[off + 4:off + 4 + length]
        off += 4 + length
        if kind != 0:
            records.append(None)
            continue
        text = body.rstrip(b"\x00").decode("cp1252", "replace")
        rec = {}
        for field in text.split("|"):
            if "=" in field:
                k, v = field.split("=", 1)
                rec[k.upper()] = v
        # UTF-8 copies of text fields are stored as %UTF8%KEY
        for k in [k for k in rec if k.startswith("%UTF8%")]:
            try:
                rec[k[6:]] = rec[k].encode("cp1252", "replace").decode("utf-8")
            except UnicodeDecodeError:
                pass
            del rec[k]
        records.append(rec)
    return records


def read_schdoc(path) -> list:
    """All records of a .SchDoc, the sheet header first (index -1 in OWNERINDEX
    terms is the header, so record i here is OWNERINDEX i - 1)."""
    return read_schdoc_streams(path)[0]


def read_schdoc_streams(path) -> tuple:
    """The records of the "FileHeader" stream, and of the "Additional" stream
    that holds the harness objects: connectors (215), their entries (216) and
    types (217), and signal harnesses (218). Additional is empty when the
    file has no such stream."""
    cf = CompoundFile(Path(path).read_bytes())
    try:
        additional = parse_records(cf.stream("Additional"))
    except KeyError:
        additional = []
    return parse_records(cf.stream("FileHeader")), additional


def coord(rec: dict, key: str) -> float:
    """A coordinate in mils. Stored as 10-mil units plus a _FRAC part in
    1/100000 of a unit, so metric sheets keep their exact positions."""
    whole = int(rec.get(key, 0) or 0)
    frac = int(rec.get(key + "_FRAC", 0) or 0)
    return (whole + frac / 100000.0) * 10.0
